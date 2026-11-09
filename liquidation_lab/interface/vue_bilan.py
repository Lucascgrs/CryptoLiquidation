"""
Le bilan de la fenêtre : par plateforme et par crypto, les longs liquidés à
gauche de l'axe, les shorts à droite. Un clic sur une crypto la choisit.
"""

from __future__ import annotations

import cairo
import numpy as np

from .. import config
from .donnees import CODES_PLATEFORMES, Memoire
from .rendu import Toile, bulle_info, horloge, texte
from .theme import COULEURS, LONG, SHORT, dollars, rgb


class VueBilan(Toile):
    def __init__(self, parent, memoire: Memoire, quand_actif=None):
        super().__init__(parent, fond=COULEURS["panneau"])
        self.memoire = memoire
        self.quand_actif = quand_actif
        self.filtres = (300.0, None, None, 0.0)
        self._plateformes: list[tuple] = []
        self._actifs: list[tuple] = []
        self._zones: list[tuple] = []           # (x0, y0, x1, y1, genre, clé, ligne)
        self._survol = None
        self._calcule = (-1, 0.0)

    def regler(self, fenetre, actif, plateformes, valeur_min) -> None:
        self.filtres = (fenetre, actif, plateformes, valeur_min)
        self._calcule = (-1, 0.0)
        self.rafraichir()

    def _calculer(self) -> None:
        fenetre, actif, plateformes, valeur_min = self.filtres
        m = self.memoire
        t0 = horloge() - fenetre * 1000
        # par plateforme : toutes les plateformes (celles écartées par le filtre sont grisées)
        indices = m.selection(t0, None, actif, None, valeur_min)
        lignes = []
        for cle, code in CODES_PLATEFORMES.items():
            choix = indices[m.plateforme[indices] == code]
            longs = float(m.valeur[choix][~m.short[choix]].sum())
            shorts = float(m.valeur[choix][m.short[choix]].sum())
            if longs + shorts > 0:
                lignes.append((cle, longs, shorts, len(choix), plateformes is None or cle in plateformes))
        self._plateformes = sorted(lignes, key=lambda x: -(x[1] + x[2]))
        # par crypto : toutes les cryptos, pour pouvoir en choisir une
        indices = m.selection(t0, None, None, plateformes, valeur_min)
        if len(indices):
            codes = m.actif[indices]
            n = int(codes.max()) + 1
            longs = np.bincount(codes, weights=np.where(m.short[indices], 0.0, m.valeur[indices]), minlength=n)
            shorts = np.bincount(codes, weights=np.where(m.short[indices], m.valeur[indices], 0.0), minlength=n)
            ordre = np.argsort(-(longs + shorts))[:12]
            self._actifs = [(m.actifs[k], float(longs[k]), float(shorts[k]), actif == m.actifs[k])
                            for k in ordre if longs[k] + shorts[k] > 0]
        else:
            self._actifs = []

    def dessiner(self, c: cairo.Context, l: int, h: int) -> None:
        version, quand = self._calcule
        if version == -1 or (version != self.memoire.version and horloge() - quand > 1000) or horloge() - quand > 3000:
            self._calculer()
            self._calcule = (self.memoire.version, horloge())
        s = self.echelle
        self._zones = []
        moitie = l / 2
        self._panneau(c, 0, moitie - 8 * s, h, "Par plateforme",
                      [(config.PLATEFORMES[cle].nom + ("" if config.PLATEFORMES[cle].complete else " *"),
                        lo, sh, actif_filtre, ("plateforme", cle, n))
                       for cle, lo, sh, n, actif_filtre in self._plateformes])
        self._panneau(c, moitie + 8 * s, l, h, "Par crypto — un clic pour la choisir",
                      [(a, lo, sh, True, ("actif", a, choisi)) for a, lo, sh, choisi in self._actifs])
        if any(not config.PLATEFORMES[x[0]].complete for x in self._plateformes):
            texte(c, 16 * s, h - 10 * s, "* la plateforme ne publie qu'une partie de ses liquidations",
                  9.5 * s, COULEURS["texte_pale"])
        if self._survol is not None:
            self._infobulle(c)
        self.animer(True, 1)

    def _panneau(self, c, x0: float, x1: float, h: int, titre: str, lignes: list) -> None:
        s = self.echelle
        texte(c, x0 + 16 * s, 18 * s, titre, 12.5 * s, COULEURS["texte"], gras=True)
        haut, h_ligne = 36 * s, 21 * s
        place = int((h - haut - 18 * s) // h_ligne)
        lignes = lignes[:max(0, place)]
        if not lignes:
            texte(c, x0 + 16 * s, haut + 12 * s, "rien sur la fenêtre", 11 * s, COULEURS["texte_pale"])
            return
        l_nom = 92 * s
        axe = x0 + 16 * s + l_nom + (x1 - x0 - l_nom - 32 * s) / 2
        demi = (x1 - x0 - l_nom - 32 * s) / 2 - 56 * s
        plus_grand = max(max(lo, sh) for _, lo, sh, _, _ in lignes) or 1.0
        for k, (nom, lo, sh, actif, zone) in enumerate(lignes):
            y = haut + k * h_ligne + h_ligne / 2
            alpha = 1.0 if actif else 0.35
            gras = zone[0] == "actif" and zone[2]
            texte(c, x0 + 16 * s, y, nom, 11 * s, COULEURS["texte"], alpha=alpha, gras=gras)
            for valeur, couleur, signe in ((lo, LONG, -1), (sh, SHORT, 1)):
                if valeur <= 0:
                    continue
                longueur = max(2.0, valeur / plus_grand * demi)
                xa = axe - longueur if signe < 0 else axe + 1
                c.rectangle(xa, y - 6 * s, longueur, 12 * s)
                c.set_source_rgba(*rgb(couleur, 0.85 * alpha))
                c.fill()
                texte(c, axe - longueur - 6 * s if signe < 0 else axe + longueur + 6 * s, y, dollars(valeur),
                      10 * s, COULEURS["texte_doux"], alpha=alpha, ancre="e" if signe < 0 else "w")
            self._zones.append((x0, y - h_ligne / 2, x1, y + h_ligne / 2, zone, nom, lo, sh))
        c.rectangle(round(axe), haut, 1, len(lignes) * h_ligne)
        c.set_source_rgba(*rgb(COULEURS["axe"]))
        c.fill()

    def _zone(self, x, y):
        for zone in self._zones:
            if zone[0] <= x <= zone[2] and zone[1] <= y <= zone[3]:
                return zone
        return None

    def _infobulle(self, c) -> None:
        x, y = self.souris or (0, 0)
        zone = self._survol
        s = self.echelle
        _, _, _, _, (genre, cle, extra), nom, lo, sh = zone
        total = lo + sh
        lignes = [(nom.rstrip(" *"), 12.5 * s, COULEURS["texte"], True),
                  (f"longs liquidés : {dollars(lo)}", 11 * s, COULEURS["texte"], False, LONG),
                  (f"shorts liquidés : {dollars(sh)}", 11 * s, COULEURS["texte"], False, SHORT)]
        if total:
            lignes.append((f"{lo / total:.0%} de longs".replace("%", " %"), 11 * s, COULEURS["texte_doux"]))
        if genre == "plateforme":
            lignes.append((f"{extra} liquidations", 11 * s, COULEURS["texte_doux"]))
            if not config.PLATEFORMES[cle].complete:
                lignes.append(("publication partielle (voir la page Collecteur)", 10.5 * s, COULEURS["texte_doux"]))
        bulle_info(c, x, y, lignes, self.largeur, self.hauteur)

    def survol(self, x, y) -> None:
        zone = None if x is None else self._zone(x, y)
        if zone != self._survol:
            self._survol = zone
            self.configure(cursor="hand2" if zone and zone[4][0] == "actif" else "")
        self.rafraichir()

    def clic(self, x: float, y: float) -> None:
        zone = self._zone(x, y)
        if zone and zone[4][0] == "actif" and self.quand_actif:
            self.quand_actif(None if zone[4][2] else zone[4][1])
