"""
La rivière des liquidations : le temps s'écoule de droite à gauche, chaque
liquidation est une bulle.

Deux lectures :
- « Volcan » : les shorts liquidés jaillissent au-dessus de l'axe, les longs
  liquidés tombent en dessous ; plus la liquidation est grosse, plus la bulle
  est loin de l'axe (échelle logarithmique en dollars) et plus elle est large ;
- « Prix » (une seule crypto) : chaque bulle est placée au prix où la position
  a été liquidée, sur la courbe du prix.

Sous la rivière, un histogramme du montant liquidé par tranche de temps.

Les bulles sont peintes une fois pour toutes sur une bande deux fois plus
large que la vue, qui défile ; seules les nouvelles arrivées sont ajoutées.
Le rendu reste ainsi léger même avec des milliers de bulles.
"""

from __future__ import annotations

import math
import time

import cairo
import numpy as np

from .. import config
from .donnees import CODES_PLATEFORMES, Memoire, Prix
from .rendu import Toile, bulle_info, horloge, largeur_texte, texte, triangle
from .theme import COULEURS, LONG, SHORT, dollars, heure, il_y_a, melanger, prix, quantite, rgb

PAS_TEMPS = [10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600]
V_HAUT_DEFAUT = 7.0                     # 10 M$
MAX_BULLES = 4000                       # au-delà (longues fenêtres), seules les plus grosses sont peintes
PUISSANCES = {2: "100 $", 3: "1 k$", 4: "10 k$", 5: "100 k$", 6: "1 M$", 7: "10 M$", 8: "100 M$", 9: "1 Md$"}
DUREE_APPARITION = 1.1                  # secondes d'animation à l'arrivée d'une bulle
PREUVES = {"HLP": "contrepartie HLP", "HLP partiel": "contrepartie HLP (taille partielle)",
           "prix": "prix de liquidation franchi (position connue)", "vérif": "historique du compte vérifié"}


def rayon(v: float, echelle: float) -> float:
    return echelle * (1.8 + 2.6 * max(0.0, math.log10(max(v, 1.0)) - 2.0) ** 1.45)


class VueRiviere(Toile):
    def __init__(self, parent, memoire: Memoire, historique_prix: Prix, quand_selection=None):
        super().__init__(parent)
        self.memoire = memoire
        self.historique_prix = historique_prix
        self.quand_selection = quand_selection
        self.fenetre = 300.0
        self.actif: str | None = None
        self.plateformes: set[str] | None = None
        self.valeur_min = 0.0
        self.mode = "volcan"
        self.message_vide = ""
        self.selection: int | None = None
        self._survole: int | None = None
        self._bande = None
        self._bande_t0 = 0.0
        self._bande_cle = None
        self._etiquettes: list[tuple] = []
        self._seuil_etiquette = 1e5
        self._apparitions: list[tuple[float, int]] = []
        self._indices = np.zeros(0, np.int64)
        self._peintes = np.zeros(0, np.int64)          # les bulles réellement peintes (pour le survol)
        self._seuil_bulle = 0.0
        self._seuil_halo = 2.5e5
        self._histo = (None, None)
        self._version = -1
        self._plage_prix = (0.0, 1.0)
        self._v_haut = V_HAUT_DEFAUT
        self._dernier_survol = 0.0

    # ------------------------------------------------------------------
    # RÉGLAGES
    # ------------------------------------------------------------------
    def regler(self, fenetre: float, actif: str | None, plateformes: set[str] | None, valeur_min: float,
               mode: str) -> None:
        self.fenetre, self.actif, self.plateformes = fenetre, actif, plateformes
        self.valeur_min, self.mode = valeur_min, mode
        # les indices changent si la mémoire a été purgée : rien ne doit en survivre
        self.selection = self._survole = None
        self._apparitions = []
        self._bande_cle = None                  # reconstruction au prochain rendu
        self.rafraichir()

    def taille_changee(self) -> None:
        s = self.echelle
        self.gx0, self.gx1 = int(74 * s), self.largeur - int(14 * s)
        self.gy0 = int(34 * s)
        self.h_hist = int(64 * s)
        self.hy1 = self.hauteur - int(26 * s)
        self.hy0 = self.hy1 - self.h_hist
        self.gy1 = self.hy0 - int(30 * s)
        self._bande_cle = None

    # ------------------------------------------------------------------
    # COORDONNÉES
    # ------------------------------------------------------------------
    @property
    def ms_par_px(self) -> float:
        return self.fenetre * 1000 / max(1, self.gx1 - self.gx0)

    def _y_volcan(self, v: np.ndarray | float, short) -> np.ndarray | float:
        milieu = (self.gy0 + self.gy1) / 2
        demi = (self.gy1 - self.gy0) / 2 - 4 * self.echelle
        ecart = 7 * self.echelle
        bas = math.log10(max(100.0, self.valeur_min or 100.0))
        f = (np.log10(np.maximum(v, 1.0)) - bas) / max(0.5, self._v_haut - bas)
        f = np.clip(f, 0.0, 1.0)
        signe = np.where(short, -1.0, 1.0)
        return milieu + signe * (ecart + f * (demi - ecart))

    def _y_prix(self, p: np.ndarray | float) -> np.ndarray | float:
        bas, haut = self._plage_prix
        return self.gy1 - (np.asarray(p) - bas) / max(1e-12, haut - bas) * (self.gy1 - self.gy0)

    def _y(self, indices: np.ndarray) -> np.ndarray:
        m = self.memoire
        if self.mode == "prix" and self.actif:
            return self._y_prix(m.prix[indices])
        return self._y_volcan(m.valeur[indices], m.short[indices])

    # ------------------------------------------------------------------
    # BANDE DES BULLES
    # ------------------------------------------------------------------
    def _cle_bande(self) -> tuple:
        return (self.fenetre, self.actif, frozenset(self.plateformes or ()), self.plateformes is None,
                self.valeur_min, self.mode, self.largeur, self.hauteur, self._v_haut, self._plage_prix)

    def _reconstruire(self, maintenant: float) -> None:
        l = max(2, int((self.gx1 - self.gx0) * 2))
        h = max(2, self.gy1 - self.gy0 + int(40 * self.echelle))
        self._bande = cairo.ImageSurface(cairo.FORMAT_ARGB32, l, h)
        self._bande_t0 = maintenant - self.fenetre * 1000
        self._etiquettes = []
        indices = self._indices[self.memoire.t[self._indices] >= self._bande_t0] if len(self._indices) else \
            self._indices
        self._seuil_bulle = 0.0
        if len(indices):
            valeurs = self.memoire.valeur[indices]
            if len(indices) > MAX_BULLES:
                self._seuil_bulle = float(np.partition(valeurs, -MAX_BULLES)[-MAX_BULLES])
                garder = valeurs >= self._seuil_bulle
                indices, valeurs = indices[garder], valeurs[garder]
            # étiquettes : les 14 plus grosses (au moins 100 k$) ; halo : les 40 plus grosses (au moins 250 k$)
            tri = np.sort(valeurs)
            self._seuil_etiquette = max(1e5, float(tri[-14]) if len(tri) >= 14 else 1e5)
            self._seuil_halo = max(2.5e5, float(tri[-40]) if len(tri) >= 40 else 2.5e5)
            ordre = indices[np.argsort(valeurs, kind="stable")]       # les grosses par-dessus
            self._peintes = ordre
            self._peindre(ordre)
        else:
            self._peintes = indices
        self._bande_cle = self._cle_bande()

    def _peindre(self, indices) -> None:
        if self._bande is None or not len(indices):
            return
        c = cairo.Context(self._bande)
        m = self.memoire
        s = self.echelle
        xs = (m.t[indices] - self._bande_t0) / self.ms_par_px
        ys = self._y(indices) - self.gy0
        for i, x, y in zip(indices, xs, ys):
            v = float(m.valeur[i])
            r = rayon(v, s)
            couleur = SHORT if m.short[i] else LONG
            if v >= self._seuil_halo:           # halo des grosses liquidations
                halo = cairo.RadialGradient(x, y, r * 0.6, x, y, r * 2.4)
                halo.add_color_stop_rgba(0, *rgb(couleur, 0.35))
                halo.add_color_stop_rgba(1, *rgb(couleur, 0.0))
                c.set_source(halo)
                c.arc(x, y, r * 2.4, 0, 2 * math.pi)
                c.fill()
            alpha = 0.5 + 0.35 * min(1.0, r / (22 * s))
            c.arc(x, y, r, 0, 2 * math.pi)
            c.set_source_rgba(*rgb(couleur, alpha))
            c.fill_preserve()
            c.set_source_rgba(*rgb(melanger(couleur, "#ffffff", 0.45), 0.85))
            c.set_line_width(max(1.0, 1.1 * s))
            c.stroke()
            if v >= self._seuil_etiquette:
                self._etiqueter(c, i, x, y, r)

    def _etiqueter(self, c, i: int, x: float, y: float, r: float) -> None:
        m = self.memoire
        s = self.echelle
        actif = m.actifs[m.actif[i]]
        principal = f"{actif} {dollars(float(m.valeur[i]))}"
        secondaire = config.PLATEFORMES[m.cle_plateforme(i)].nom
        l1 = largeur_texte(c, principal, 11.5 * s, gras=True)
        l2 = largeur_texte(c, secondaire, 9.5 * s)
        largeur, hauteur = max(l1, l2), 26 * s
        for cote in (1, -1):
            x0 = x + r + 5 * s if cote == 1 else x - r - 5 * s - largeur
            y0 = y - hauteur / 2
            boite = (x0 - 2, y0 - 2, x0 + largeur + 2, y0 + hauteur + 2)
            if x0 < 0 or not all(boite[2] < b[0] or boite[0] > b[2] or boite[3] < b[1] or boite[1] > b[3]
                                 for b in self._etiquettes):
                continue
            self._etiquettes.append(boite)
            texte(c, x0, y0, principal, 11.5 * s, COULEURS["texte"], gras=True, base="t")
            texte(c, x0, y0 + 14 * s, secondaire, 9.5 * s, COULEURS["texte_doux"], base="t")
            return

    def nouvelles(self, indices: range) -> None:
        """Nouvelles liquidations arrivées : ajoutées à la bande, avec une animation."""
        if not len(indices) or self._bande is None:
            return
        n0, n1 = indices.start, indices.stop
        m = self.memoire
        ok = np.ones(n1 - n0, bool)
        if self.actif:
            code = m.code_actif(self.actif)
            ok &= m.actif[n0:n1] == (code if code is not None else -1)
        if self.plateformes is not None:
            ok &= np.isin(m.plateforme[n0:n1], [CODES_PLATEFORMES[p] for p in self.plateformes])
        if self.valeur_min:
            ok &= m.valeur[n0:n1] >= self.valeur_min
        choisis = np.arange(n0, n1)[ok]
        if not len(choisis):
            return
        if self.mode == "prix" and self.actif:
            bas, haut = self._plage_prix
            p = m.prix[choisis]
            if p.min() < bas or p.max() > haut:
                self._bande_cle = None          # hors de l'échelle : tout est repeint
        if self.mode == "volcan" and m.valeur[choisis].max() >= 10 ** self._v_haut:
            self._bande_cle = None
        self._indices = np.concatenate([self._indices, choisis])
        if self._bande_cle is not None:
            peintes = choisis[m.valeur[choisis] >= self._seuil_bulle]
            peintes = peintes[np.argsort(m.valeur[peintes])]
            self._peintes = np.concatenate([self._peintes, peintes])
            self._peindre(peintes)
        maintenant = time.monotonic()
        self._apparitions.extend((maintenant, int(i)) for i in choisis[-40:])
        self.rafraichir()

    # ------------------------------------------------------------------
    # RENDU
    # ------------------------------------------------------------------
    def dessiner(self, c: cairo.Context, l: int, h: int) -> None:
        if l < 200 or h < 200:
            return
        s = self.echelle
        maintenant = horloge()
        t_gauche = maintenant - self.fenetre * 1000
        if self.memoire.version != self._version or self._bande_cle is None:
            self._version = self.memoire.version
            self._indices = self.memoire.selection(t_gauche - self.fenetre * 1000, None, self.actif,
                                                   self.plateformes, self.valeur_min)
        visibles = self._indices[self.memoire.t[self._indices] >= t_gauche] if len(self._indices) else self._indices
        self._ajuster_echelles(visibles, t_gauche)
        if (self._bande_cle != self._cle_bande()
                or maintenant > self._bande_t0 + (self._bande.get_width() if self._bande else 0) * self.ms_par_px
                - 2000):
            self._reconstruire(maintenant)

        self._fond(c, maintenant)
        self._histogramme(c, visibles, maintenant)
        if self.mode == "prix" and self.actif:
            self._courbe_prix(c, t_gauche, maintenant)
        # la bande des bulles, décalée pour que « maintenant » soit au bord droit
        decalage = (t_gauche - self._bande_t0) / self.ms_par_px
        c.save()
        c.rectangle(self.gx0, self.gy0 - 6 * s, self.gx1 - self.gx0, self.gy1 - self.gy0 + 12 * s)
        c.clip()
        c.set_source_surface(self._bande, self.gx0 - decalage, self.gy0)
        c.paint()
        anime = self._apparitions_en_cours(c, t_gauche)
        self._surbrillance(c, t_gauche)
        c.restore()
        self._maintenant(c, maintenant)
        if not len(visibles) and self.message_vide:
            texte(c, (self.gx0 + self.gx1) / 2, (self.gy0 + self.gy1) / 2, self.message_vide, 14 * s,
                  COULEURS["texte_doux"], ancre="c")
        if self._survole is not None:
            self._infobulle(c, self._survole, t_gauche)
        self.animer(True, 30 if anime else 6)

    def _ajuster_echelles(self, visibles: np.ndarray, t_gauche: float) -> None:
        m = self.memoire
        if self.mode == "volcan":
            plus_gros = float(m.valeur[visibles].max()) if len(visibles) else 0.0
            self._v_haut = V_HAUT_DEFAUT if plus_gros < 10 ** V_HAUT_DEFAUT else math.ceil(math.log10(plus_gros))
            return
        if not self.actif:
            return
        _, p, bas, haut = self.historique_prix.serie(t_gauche, avec_meches=True)
        valeurs = np.concatenate([p, bas, haut, m.prix[visibles]])
        if not len(valeurs):
            return
        bas, haut = float(valeurs.min()), float(valeurs.max())
        if haut <= bas:
            haut, bas = bas * 1.001 + 1e-12, bas * 0.999
        a, b = self._plage_prix
        # on ne change d'échelle que si les données en sortent, ou n'en occupent plus qu'un tiers
        if bas < a or haut > b or (haut - bas) < (b - a) / 3:
            marge = (haut - bas) * 0.12
            self._plage_prix = (bas - marge, haut + marge)

    def _fond(self, c: cairo.Context, maintenant: float) -> None:
        s = self.echelle
        gx0, gx1, gy0, gy1 = self.gx0, self.gx1, self.gy0, self.gy1
        c.set_line_width(1)
        if self.mode == "volcan" or not self.actif:
            milieu = (gy0 + gy1) / 2
            bas = math.log10(max(100.0, self.valeur_min or 100.0))
            for k in range(int(math.ceil(bas)), int(self._v_haut) + 1):
                for short in (True, False):
                    y = round(float(self._y_volcan(10.0 ** k, short))) + 0.5
                    c.set_source_rgba(*rgb(COULEURS["grille"]))
                    c.move_to(gx0, y)
                    c.line_to(gx1, y)
                    c.stroke()
                    texte(c, gx0 - 8 * s, y, PUISSANCES.get(k, dollars(10.0 ** k)), 10 * s,
                          COULEURS["texte_pale"], ancre="e")
            c.set_source_rgba(*rgb(COULEURS["axe"]))
            c.set_line_width(1.5)
            c.move_to(gx0, milieu)
            c.line_to(gx1, milieu)
            c.stroke()
            y = gy0 - 18 * s
            triangle(c, gx0 + 5 * s, y, 5 * s, True, SHORT)
            x = texte(c, gx0 + 15 * s, y, "Shorts liquidés, au-dessus : le prix monte", 11 * s,
                      COULEURS["texte"], semi=True)[2]
            triangle(c, x + 25 * s, y, 5 * s, False, LONG)
            texte(c, x + 35 * s, y, "Longs liquidés, en dessous : le prix baisse", 11 * s, COULEURS["texte"],
                  semi=True)
            texte(c, gx1, y, "plus la bulle est loin de l'axe, plus la liquidation est grosse", 10 * s,
                  COULEURS["texte_pale"], ancre="e")
        else:
            bas, haut = self._plage_prix
            for p in _graduations(bas, haut, 6):
                y = round(float(self._y_prix(p))) + 0.5
                c.set_source_rgba(*rgb(COULEURS["grille"]))
                c.move_to(gx0, y)
                c.line_to(gx1, y)
                c.stroke()
                texte(c, gx0 - 8 * s, y, prix(p), 10 * s, COULEURS["texte_pale"], ancre="e")
            y = gy0 - 18 * s
            triangle(c, gx0 + 5 * s, y, 5 * s, True, SHORT)
            x = texte(c, gx0 + 15 * s, y, "Shorts liquidés", 11 * s, COULEURS["texte"], semi=True)[2]
            triangle(c, x + 20 * s, y, 5 * s, False, LONG)
            texte(c, x + 30 * s, y, "Longs liquidés", 11 * s, COULEURS["texte"], semi=True)
            texte(c, gx1, y, f"{self.actif} : chaque bulle au prix où la position a été liquidée", 10 * s,
                  COULEURS["texte_pale"], ancre="e")
        # temps
        pas = next((p for p in PAS_TEMPS if self.fenetre / p <= 8), PAS_TEMPS[-1])
        decalage_local = -time.altzone if time.localtime().tm_isdst else -time.timezone
        t = math.floor(((maintenant / 1000) + decalage_local) / pas) * pas - decalage_local
        while True:
            x = gx1 - (maintenant - t * 1000) / self.ms_par_px
            if x < gx0:
                break
            xr = round(x) + 0.5
            c.set_source_rgba(*rgb(COULEURS["grille"]))
            c.set_line_width(1)
            c.move_to(xr, gy0)
            c.line_to(xr, self.hy1)
            c.stroke()
            texte(c, x, self.hy1 + 13 * s, heure(t * 1000, secondes=pas < 60), 10 * s, COULEURS["texte_pale"],
                  ancre="c")
            t -= pas

    def _histogramme(self, c: cairo.Context, visibles: np.ndarray, maintenant: float) -> None:
        s = self.echelle
        m = self.memoire
        largeur_tranche = max(1.0, self.fenetre / 100)                # secondes
        milieu = (self.hy0 + self.hy1) / 2
        c.set_source_rgba(*rgb(COULEURS["axe"]))
        c.set_line_width(1)
        c.move_to(self.gx0, round(milieu) + 0.5)
        c.line_to(self.gx1, round(milieu) + 0.5)
        c.stroke()
        if not len(visibles):
            return
        cle = (self._version, int(maintenant // 1000), self._bande_cle)
        if self._histo[0] != cle:               # recalculé au plus une fois par seconde
            tranche = np.floor(m.t[visibles] / 1000 / largeur_tranche).astype(np.int64)
            premiere = int(tranche.min())
            n = int(tranche.max()) - premiere + 1
            longs = np.bincount(tranche - premiere, weights=np.where(m.short[visibles], 0.0, m.valeur[visibles]),
                                minlength=n)
            shorts = np.bincount(tranche - premiere, weights=np.where(m.short[visibles], m.valeur[visibles], 0.0),
                                 minlength=n)
            self._histo = (cle, (premiere, n, longs, shorts))
        premiere, n, longs, shorts = self._histo[1]
        plus_haut = max(float(longs.max()), float(shorts.max()), 1.0)
        demi = (self.hy1 - self.hy0) / 2 - 1
        l_barre = max(1.0, largeur_tranche * 1000 / self.ms_par_px - 1.5)
        for k in range(n):
            x = self.gx1 - (maintenant - (premiere + k) * largeur_tranche * 1000) / self.ms_par_px
            if x + l_barre < self.gx0:
                continue
            for valeur, couleur, signe in ((shorts[k], SHORT, -1), (longs[k], LONG, 1)):
                if valeur <= 0:
                    continue
                hauteur = max(1.0, valeur / plus_haut * demi)
                y = milieu + 1 if signe > 0 else milieu - hauteur
                c.rectangle(max(self.gx0, x), y, l_barre, hauteur)
                c.set_source_rgba(*rgb(couleur, 0.8))
                c.fill()
        legende = f"montant liquidé par tranche de {largeur_tranche:.0f} s".replace(".", ",")
        if largeur_tranche >= 60:
            legende = f"montant liquidé par tranche de {largeur_tranche / 60:.0f} min"
        texte(c, self.gx0 - 8 * s, self.hy0 + 2 * s, dollars(plus_haut), 9.5 * s, COULEURS["texte_pale"],
              ancre="e", base="t")
        texte(c, self.gx0, self.hy0 - 12 * s, legende, 10 * s, COULEURS["texte_doux"])

    def _courbe_prix(self, c: cairo.Context, t_gauche: float, maintenant: float) -> None:
        t, p, bas, haut = self.historique_prix.serie(t_gauche, avec_meches=True)
        if len(t) < 2:
            return
        x = self.gx1 - (maintenant - t) / self.ms_par_px
        y = self._y_prix(p)
        c.save()
        c.rectangle(self.gx0, self.gy0, self.gx1 - self.gx0, self.gy1 - self.gy0)
        c.clip()
        # le plus haut et le plus bas de chaque minute, en voile : c'est là que tombent les liquidations
        y_bas, y_haut = self._y_prix(bas), self._y_prix(haut)
        c.move_to(x[0], y_haut[0])
        for xi, yi in zip(x[1:], y_haut[1:]):
            c.line_to(xi, yi)
        for xi, yi in zip(x[::-1], y_bas[::-1]):
            c.line_to(xi, yi)
        c.close_path()
        c.set_source_rgba(*rgb("#d9d9d9", 0.10))
        c.fill()
        c.move_to(x[0], y[0])
        for xi, yi in zip(x[1:], y[1:]):
            c.line_to(xi, yi)
        c.set_source_rgba(*rgb("#d9d9d9", 0.85))
        c.set_line_width(1.6 * self.echelle)
        c.set_line_join(cairo.LINE_JOIN_ROUND)
        c.stroke()
        c.restore()
        texte(c, self.gx1 - 4, float(y[-1]) - 10 * self.echelle, prix(float(p[-1])), 10.5 * self.echelle,
              COULEURS["texte"], ancre="e", semi=True)

    def _apparitions_en_cours(self, c: cairo.Context, t_gauche: float) -> bool:
        maintenant = time.monotonic()
        self._apparitions = [(t, i) for t, i in self._apparitions if maintenant - t < DUREE_APPARITION]
        if not self._apparitions:
            return False
        m = self.memoire
        s = self.echelle
        indices = np.array([i for _, i in self._apparitions])
        xs = self.gx0 + (m.t[indices] - t_gauche) / self.ms_par_px
        ys = self._y(indices)
        for (t0, i), x, y in zip(self._apparitions, xs, ys):
            f = (maintenant - t0) / DUREE_APPARITION
            v = float(m.valeur[i])
            r = rayon(v, s)
            couleur = SHORT if m.short[i] else LONG
            ondes = 2 if v >= 1e6 else 1
            for k in range(ondes):
                g = min(1.0, f * (1 + 0.6 * k))
                c.arc(min(x, self.gx1), y, r + g * (14 + 10 * k) * s * (1.6 if v >= 1e6 else 1), 0, 2 * math.pi)
                c.set_source_rgba(*rgb(melanger(couleur, "#ffffff", 0.3), (1 - g) * 0.8))
                c.set_line_width(2 * s)
                c.stroke()
        return True

    def _maintenant(self, c: cairo.Context, maintenant: float) -> None:
        s = self.echelle
        x = self.gx1 + 0.5
        c.set_source_rgba(*rgb("#ffffff", 0.18))
        c.set_line_width(1)
        c.move_to(x, self.gy0 - 6 * s)
        c.line_to(x, self.hy1)
        c.stroke()
        pulsation = 0.5 + 0.5 * math.sin(time.monotonic() * 3)
        c.arc(x, (self.gy0 + self.gy1) / 2 if self.mode == "volcan" or not self.actif else self.gy0, 3.5 * s,
              0, 2 * math.pi)
        c.set_source_rgba(*rgb("#ffffff", 0.35 + 0.4 * pulsation))
        c.fill()

    def _surbrillance(self, c: cairo.Context, t_gauche: float) -> None:
        for i, epaisseur in ((self.selection, 2.5), (self._survole, 1.5)):
            if i is None or i >= self.memoire.n:
                continue
            m = self.memoire
            x = self.gx0 + (m.t[i] - t_gauche) / self.ms_par_px
            y = float(self._y(np.array([i]))[0])
            c.arc(min(x, self.gx1), y, rayon(float(m.valeur[i]), self.echelle) + 4 * self.echelle, 0, 2 * math.pi)
            c.set_source_rgba(1, 1, 1, 0.9)
            c.set_line_width(epaisseur * self.echelle)
            c.stroke()

    def _infobulle(self, c: cairo.Context, i: int, t_gauche: float) -> None:
        m = self.memoire
        s = self.echelle
        if i >= m.n or m.t[i] < t_gauche:
            self._survole = None
            return
        x = self.gx0 + (m.t[i] - t_gauche) / self.ms_par_px
        y = float(self._y(np.array([i]))[0])
        cle = m.cle_plateforme(i)
        plateforme = config.PLATEFORMES[cle]
        actif = m.actifs[m.actif[i]]
        v, p = float(m.valeur[i]), float(m.prix[i])
        short = bool(m.short[i])
        lignes = [("Short liquidé (le prix a monté)" if short else "Long liquidé (le prix a baissé)", 13 * s,
                   COULEURS["texte"], True, SHORT if short else LONG),
                  (f"{dollars(v)} — {actif} à {prix(p)} $", 12 * s, COULEURS["texte"], True),
                  (f"{quantite(v / p if p else 0)} {actif}", 11 * s, COULEURS["texte_doux"]),
                  (f"{plateforme.nom} · {m.marches[i]}", 11 * s, COULEURS["texte_doux"]),
                  (f"{heure(float(m.t[i]))} · {il_y_a(float(m.t[i]))}", 11 * s, COULEURS["texte_doux"])]
        detail = m.details[i]
        if cle == "hyperliquid" and detail:
            morceaux = detail.split(" ", 2)
            if morceaux:
                lignes.append((f"compte {morceaux[0][:10]}…{morceaux[0][-4:]}", 11 * s, COULEURS["texte_doux"]))
            if len(morceaux) == 3:
                lignes.append((f"confirmée par : {PREUVES.get(morceaux[2], morceaux[2])}", 11 * s,
                               COULEURS["texte_doux"]))
        elif detail == "partielle":
            lignes.append(("liquidation partielle", 11 * s, COULEURS["texte_doux"]))
        bulle_info(c, x, y, lignes, self.largeur, self.hauteur)

    # ------------------------------------------------------------------
    # SOURIS
    # ------------------------------------------------------------------
    def _plus_proche(self, x: float, y: float) -> int | None:
        if not len(self._peintes):
            return None
        m = self.memoire
        t_gauche = horloge() - self.fenetre * 1000
        indices = self._peintes[m.t[self._peintes] >= t_gauche]
        if not len(indices):
            return None
        xs = self.gx0 + (m.t[indices] - t_gauche) / self.ms_par_px
        ys = self._y(indices)
        rs = self.echelle * (1.8 + 2.6 * np.maximum(0, np.log10(np.maximum(m.valeur[indices], 1)) - 2) ** 1.45)
        d = np.hypot(xs - x, ys - y) - rs
        k = int(np.argmin(d))
        return int(indices[k]) if d[k] <= 4 * self.echelle else None

    def survol(self, x, y) -> None:
        if x is None:
            self._survole = None
            self.rafraichir()
            return
        if time.monotonic() - self._dernier_survol < 0.03:
            return
        self._dernier_survol = time.monotonic()
        avant = self._survole
        self._survole = self._plus_proche(x, y)
        if self._survole != avant:
            self.configure(cursor="hand2" if self._survole is not None else "")
            self.rafraichir()

    def clic(self, x: float, y: float) -> None:
        self.selection = self._plus_proche(x, y)
        if self.quand_selection:
            self.quand_selection(self.selection)
        self.rafraichir()


def _graduations(bas: float, haut: float, nombre: int) -> list[float]:
    etendue = haut - bas
    if etendue <= 0:
        return [bas]
    brut = etendue / nombre
    puissance = 10 ** math.floor(math.log10(brut))
    pas = next(m * puissance for m in (1, 2, 2.5, 5, 10) if m * puissance >= brut)
    debut = math.ceil(bas / pas) * pas
    return [debut + k * pas for k in range(int((haut - debut) / pas) + 1)]
