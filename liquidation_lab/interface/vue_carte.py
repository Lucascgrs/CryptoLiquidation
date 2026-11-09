"""
La carte des niveaux de liquidation réels d'une crypto (positions Hyperliquid).

L'axe horizontal est le prix ; le prix courant est au centre, les longs (qui
seraient liquidés si le prix baissait) à gauche, les shorts à droite. Trois
bandes partagent cet axe :
1. les positions, une bulle chacune : à son prix de liquidation, d'autant plus
   haute et large qu'elle est grosse (échelle logarithmique). Les plus grosses
   sont nommées ;
2. le montant à liquider par tranche de prix ;
3. le cumul : tout ce qui serait liquidé si le prix allait jusque-là.
En bas, les liquidations des dernières minutes (toutes plateformes) marquent
le prix où elles ont eu lieu.
"""

from __future__ import annotations

import math
import time

import cairo
import numpy as np

from ..niveaux import Carte
from .rendu import Toile, bulle_info, horloge, texte, triangle
from .theme import COULEURS, LONG, SHORT, dollars, il_y_a, melanger, pourcent, prix, quantite, rgb

V_BAS, V_HAUT = 3.0, 8.0                # bulles : de 1 k$ à 100 M$


class VueCarte(Toile):
    def __init__(self, parent, quand_selection=None):
        super().__init__(parent)
        self.quand_selection = quand_selection
        self.carte: Carte | None = None
        self.plage = 0.10
        self.recentes: list[tuple] = []         # (t, prix, short, valeur, plateforme) des liquidations récentes
        self.message = ""
        # sélection et survol désignent une position par l'adresse de son compte : la carte est
        # relue toutes les 5 s, les numéros de ligne changent, pas les adresses
        self.selection: str | None = None
        self._survol = None                     # ("position", adresse) ou ("prix", x à l'écran)
        self._points = None                     # (x, y, r, adresses) des bulles dessinées
        self._dernier_survol = 0.0

    def montrer(self, carte: Carte | None, plage: float) -> None:
        self.carte, self.plage = carte, plage
        if carte is None:
            self._survol = None
        self.rafraichir()

    def position(self, adresse: str):
        """La ligne de la position de ce compte dans la carte affichée (None si elle n'y est plus)."""
        if self.carte is None or adresse is None:
            return None
        p = self.carte.positions
        lignes = p[p["adresse"] == adresse]
        return None if lignes.empty else lignes.iloc[0]

    def taille_changee(self) -> None:
        s = self.echelle
        self.gx0, self.gx1 = int(64 * s), self.largeur - int(18 * s)
        h = self.hauteur
        self.ay0 = int(40 * s)                           # bande des bulles
        self.ay1 = int(h * 0.50)
        self.by0, self.by1 = self.ay1 + int(26 * s), int(h * 0.71)       # tranches
        self.cy0, self.cy1 = self.by1 + int(26 * s), h - int(52 * s)     # cumul
        self.ty = h - int(30 * s)                        # liquidations récentes, puis l'axe des prix

    # ------------------------------------------------------------------
    def _x(self, p):
        bas = self.carte.prix * (1 - self.plage)
        haut = self.carte.prix * (1 + self.plage)
        return self.gx0 + (np.asarray(p, float) - bas) / (haut - bas) * (self.gx1 - self.gx0)

    def _prix_en(self, x: float) -> float:
        bas = self.carte.prix * (1 - self.plage)
        haut = self.carte.prix * (1 + self.plage)
        return bas + (x - self.gx0) / (self.gx1 - self.gx0) * (haut - bas)

    def _y_bulle(self, v):
        f = (np.log10(np.maximum(v, 1.0)) - V_BAS) / (V_HAUT - V_BAS)
        return self.ay1 - 6 * self.echelle - np.clip(f, 0, 1) * (self.ay1 - self.ay0 - 16 * self.echelle)

    def _rayon(self, v):
        return self.echelle * (2.0 + 2.4 * np.maximum(0, np.log10(np.maximum(v, 1.0)) - 3) ** 1.35)

    # ------------------------------------------------------------------
    def dessiner(self, c: cairo.Context, l: int, h: int) -> None:
        s = self.echelle
        if self.carte is None or l < 300 or h < 300:
            texte(c, l / 2, h / 2, self.message or "Aucune position connue pour cette crypto.", 14 * s,
                  COULEURS["texte_doux"], ancre="c")
            self._points = None
            return
        carte = self.carte
        p = carte.positions
        dans = p[p["prix_liquidation"].notna() & (p["distance"].abs() <= self.plage)]
        self._graduations(c)
        self._bande_positions(c, dans)
        self._bande_tranches(c)
        self._bande_cumul(c)
        self._recentes(c)
        self._ligne_prix(c)
        if self._survol is not None:
            self._infobulle(c)
        anime = any(horloge() - t < 600_000 for t, *_ in self.recentes)
        self.animer(anime, 2)

    def _graduations(self, c) -> None:
        s = self.echelle
        carte = self.carte
        pas = next(x for x in (0.005, 0.01, 0.02, 0.05, 0.1, 0.2) if self.plage / x <= 6)
        k = -int(self.plage / pas)
        while k <= int(self.plage / pas):
            x = round(float(self._x(carte.prix * (1 + k * pas)))) + 0.5
            c.set_source_rgba(*rgb(COULEURS["grille"]))
            c.set_line_width(1)
            c.move_to(x, self.ay0)
            c.line_to(x, self.cy1)
            c.stroke()
            if k:
                texte(c, x, self.ay0 - 9 * s, pourcent(k * pas, signe=True).replace(",0 ", " "), 10 * s,
                      COULEURS["texte_pale"], ancre="c")
                texte(c, x, self.ty + 18 * s, prix(carte.prix * (1 + k * pas)), 10 * s, COULEURS["texte_pale"],
                      ancre="c")
            k += 1
        triangle(c, self.gx0 + 5 * s, 13 * s, 5 * s, False, LONG)
        texte(c, self.gx0 + 15 * s, 13 * s, "LONGS — liquidés si le prix baisse", 10.5 * s,
              COULEURS["texte"], semi=True)
        triangle(c, self.gx1 - 5 * s, 13 * s, 5 * s, True, SHORT)
        texte(c, self.gx1 - 15 * s, 13 * s, "SHORTS — liquidés si le prix monte", 10.5 * s,
              COULEURS["texte"], semi=True, ancre="e")

    def _bande_positions(self, c, dans) -> None:
        s = self.echelle
        for k in range(int(V_BAS), int(V_HAUT) + 1):
            y = round(float(self._y_bulle(10.0 ** k))) + 0.5
            c.set_source_rgba(*rgb(COULEURS["grille"], 0.7))
            c.set_line_width(1)
            c.move_to(self.gx0, y)
            c.line_to(self.gx1, y)
            c.stroke()
            texte(c, self.gx0 - 8 * s, y, {3: "1 k$", 4: "10 k$", 5: "100 k$", 6: "1 M$", 7: "10 M$",
                                           8: "100 M$"}[k], 9.5 * s, COULEURS["texte_pale"], ancre="e")
        if dans.empty:
            self._points = None
            texte(c, (self.gx0 + self.gx1) / 2, (self.ay0 + self.ay1) / 2,
                  "Aucune position connue dans cette plage de prix.", 12 * s, COULEURS["texte_doux"], ancre="c")
            return
        ordre = dans.sort_values("notionnel")           # les grosses par-dessus
        v = ordre["notionnel"].to_numpy()
        xs = self._x(ordre["prix_liquidation"].to_numpy())
        ys = self._y_bulle(v)
        rs = self._rayon(v)
        longs = ordre["long"].to_numpy()
        frais = (horloge() - ordre["lue_le"].to_numpy()) < 3_600_000
        for x, y, r, est_long, recent in zip(xs, ys, rs, longs, frais):
            couleur = LONG if est_long else SHORT
            c.arc(x, y, r, 0, 2 * math.pi)
            c.set_source_rgba(*rgb(couleur, 0.62 if recent else 0.3))
            c.fill_preserve()
            c.set_source_rgba(*rgb(melanger(couleur, "#ffffff", 0.45), 0.85 if recent else 0.4))
            c.set_line_width(max(1.0, 1.0 * s))
            c.stroke()
        self._points = (xs, ys, rs, ordre["adresse"].to_numpy())
        # les plus grosses sont nommées
        cases: list[tuple] = []
        for j in np.argsort(-v)[:10]:
            if v[j] < 2.5e5:
                break
            ligne = ordre.iloc[j]
            etiquette = f"{dollars(v[j])} ×{ligne['levier']:.0f}"
            x0 = xs[j] + rs[j] + 4 * s
            boite = (x0, ys[j] - 8 * s, x0 + 90 * s, ys[j] + 8 * s)
            if boite[2] > self.gx1 or any(not (boite[2] < b[0] or boite[0] > b[2] or boite[3] < b[1]
                                               or boite[1] > b[3]) for b in cases):
                continue
            cases.append(boite)
            texte(c, x0, ys[j], etiquette, 10.5 * s, COULEURS["texte"], gras=True)
        if self.selection is not None and (self._points[3] == self.selection).any():
            j = int(np.flatnonzero(self._points[3] == self.selection)[0])
            c.arc(xs[j], ys[j], rs[j] + 4 * s, 0, 2 * math.pi)
            c.set_source_rgba(1, 1, 1, 0.95)
            c.set_line_width(2.5 * s)
            c.stroke()

    def _bande_tranches(self, c) -> None:
        s = self.echelle
        bords, par_cote = self.carte.tranches(self.plage, 100)
        longs, shorts = par_cote["longs"][0], par_cote["shorts"][0]
        plus_haut = max(float(longs.max()), float(shorts.max()), 1.0)
        x = self._x(bords)
        hauteur = self.by1 - self.by0
        for k in range(len(longs)):
            for valeur, couleur in ((longs[k], LONG), (shorts[k], SHORT)):
                if valeur <= 0:
                    continue
                hh = max(1.0, valeur / plus_haut * hauteur)
                c.rectangle(x[k] + 0.5, self.by1 - hh, max(1.0, x[k + 1] - x[k] - 1.5), hh)
                c.set_source_rgba(*rgb(couleur, 0.85))
                c.fill()
        c.rectangle(self.gx0, self.by1, self.gx1 - self.gx0, 1)
        c.set_source_rgba(*rgb(COULEURS["axe"]))
        c.fill()
        largeur = self.plage * 2 / 100                  # une tranche, en fraction du prix
        texte(c, self.gx0, self.by0 - 12 * s,
              f"Montant à liquider par tranche de {largeur * 100:.2g} % du prix".replace(".", ","),
              10.5 * s, COULEURS["texte_doux"], semi=True)
        texte(c, self.gx0 - 8 * s, self.by0, dollars(plus_haut), 9.5 * s, COULEURS["texte_pale"], ancre="e",
              base="t")

    def _bande_cumul(self, c) -> None:
        s = self.echelle
        bas, cumul_l, haut, cumul_s = self.carte.courbes(self.plage, 300)
        plus_haut = max(float(cumul_l[-1]) if len(cumul_l) else 0, float(cumul_s[-1]) if len(cumul_s) else 0, 1.0)
        hauteur = self.cy1 - self.cy0
        for xs_prix, cumul, couleur in ((bas, cumul_l, LONG), (haut, cumul_s, SHORT)):
            xs = self._x(xs_prix)
            ys = self.cy1 - cumul / plus_haut * hauteur
            c.move_to(xs[0], self.cy1)
            for x, y in zip(xs, ys):
                c.line_to(x, y)
            c.line_to(xs[-1], self.cy1)
            c.close_path()
            c.set_source_rgba(*rgb(couleur, 0.22))
            c.fill()
            c.move_to(xs[0], ys[0])
            for x, y in zip(xs, ys):
                c.line_to(x, y)
            c.set_source_rgba(*rgb(couleur, 0.95))
            c.set_line_width(2 * s)
            c.stroke()
        c.rectangle(self.gx0, self.cy1, self.gx1 - self.gx0, 1)
        c.set_source_rgba(*rgb(COULEURS["axe"]))
        c.fill()
        texte(c, self.gx0, self.cy0 - 12 * s, "Cumul : tout ce qui serait liquidé si le prix allait jusque-là",
              10.5 * s, COULEURS["texte_doux"], semi=True)
        texte(c, self.gx0 - 8 * s, self.cy0, dollars(plus_haut), 9.5 * s, COULEURS["texte_pale"], ancre="e", base="t")
        if len(cumul_l):
            texte(c, self.gx0 + 4 * s, self.cy0 + 4 * s, f"{dollars(float(cumul_l[-1]))} de longs", 10 * s,
                  COULEURS["texte"], semi=True, base="t")
        if len(cumul_s):
            texte(c, self.gx1 - 4 * s, self.cy0 + 4 * s, f"{dollars(float(cumul_s[-1]))} de shorts", 10 * s,
                  COULEURS["texte"], semi=True, ancre="e", base="t")

    def _recentes(self, c) -> None:
        """Les liquidations des 10 dernières minutes, au prix où elles ont eu lieu."""
        s = self.echelle
        maintenant = horloge()
        y = self.ty
        c.rectangle(self.gx0, y + 6 * s, self.gx1 - self.gx0, 1)
        c.set_source_rgba(*rgb(COULEURS["axe"]))
        c.fill()
        for t, p, short, v, _plateforme in self.recentes:
            age = (maintenant - t) / 600_000
            if age >= 1:
                continue
            x = float(self._x(p))
            if not self.gx0 <= x <= self.gx1:
                continue
            hauteur = (4 + 3 * max(0.0, math.log10(max(v, 1)) - 2)) * s
            c.rectangle(x - 1, y + 5 * s - hauteur, 2.2 * s, hauteur)
            c.set_source_rgba(*rgb(SHORT if short else LONG, 0.95 * (1 - age) + 0.05))
            c.fill()
        texte(c, self.gx0 - 8 * s, y, "10 min", 9 * s, COULEURS["texte_pale"], ancre="e")

    def _ligne_prix(self, c) -> None:
        s = self.echelle
        x = round(float(self._x(self.carte.prix))) + 0.5
        c.set_source_rgba(1, 1, 1, 0.75)
        c.set_line_width(1.5)
        c.set_dash([4 * s, 3 * s])
        c.move_to(x, self.ay0 - 4 * s)
        c.line_to(x, self.ty + 6 * s)
        c.stroke()
        c.set_dash([])
        etiquette = f"{prix(self.carte.prix)} $"
        texte(c, x, self.ty + 18 * s, etiquette, 11 * s, COULEURS["texte"], gras=True, ancre="c")

    # ------------------------------------------------------------------
    def _bulle_sous(self, x: float, y: float):
        if self._points is None:
            return None
        xs, ys, rs, adresses = self._points
        d = np.hypot(xs - x, ys - y) - rs
        k = int(np.argmin(d))
        return str(adresses[k]) if d[k] <= 3 * self.echelle else None

    def survol(self, x, y) -> None:
        if x is None:
            self._survol = None
            self.rafraichir()
            return
        if time.monotonic() - self._dernier_survol < 0.03:
            return
        self._dernier_survol = time.monotonic()
        if self.carte is None:
            return
        position = self._bulle_sous(x, y) if y <= self.ay1 + 4 * self.echelle else None
        if position is not None:
            self._survol = ("position", position)
        elif self.gx0 <= x <= self.gx1 and self.by0 <= y <= self.cy1:
            self._survol = ("prix", x)
        else:
            self._survol = None
        self.configure(cursor="hand2" if position is not None else "")
        self.rafraichir()

    def clic(self, x: float, y: float) -> None:
        self.selection = self._bulle_sous(x, y) if self.carte is not None else None
        if self.quand_selection:
            self.quand_selection(self.selection)
        self.rafraichir()

    def _infobulle(self, c) -> None:
        s = self.echelle
        x, y = self.souris or (0, 0)
        genre, valeur = self._survol
        carte = self.carte
        if genre == "position":
            ligne = self.position(valeur)
            if ligne is None:                   # position disparue à la dernière relecture
                self._survol = None
                return
            long = bool(ligne["long"])
            lignes = [(f"{'Long' if long else 'Short'} {dollars(float(ligne['notionnel']))}", 13 * s,
                       COULEURS["texte"], True, LONG if long else SHORT),
                      (f"liquidation à {prix(float(ligne['prix_liquidation']))} $ "
                       f"({pourcent(float(ligne['distance']), signe=True)})", 12 * s, COULEURS["texte"], True),
                      (f"taille {quantite(float(ligne['taille']))} {ligne['actif']} · entrée "
                       f"{prix(float(ligne['prix_entree']))} $", 11 * s, COULEURS["texte_doux"]),
                      (f"levier {float(ligne['levier']):.0f} · marge {'isolée' if ligne['marge'] == 'isolated' else 'croisée'}"
                       f" · PnL {dollars(float(ligne['pnl']))}", 11 * s, COULEURS["texte_doux"]),
                      (f"compte {ligne['adresse'][:10]}…{ligne['adresse'][-4:]} · {dollars(float(ligne['valeur_compte']))}",
                       11 * s, COULEURS["texte_doux"]),
                      (f"lue {il_y_a(float(ligne['lue_le']))}", 11 * s, COULEURS["texte_doux"])]
        else:
            cible = self._prix_en(valeur)
            montant, nombre = carte.cumul(cible)
            ecart = cible / carte.prix - 1
            sens = "longs" if cible < carte.prix else "shorts"
            lignes = [(f"Si le prix allait à {prix(cible)} $ ({pourcent(ecart, signe=True)})", 12.5 * s,
                       COULEURS["texte"], True),
                      (f"{dollars(montant)} de {sens} liquidés", 12 * s, COULEURS["texte"], True,
                       LONG if sens == "longs" else SHORT),
                      (f"{nombre} positions connues", 11 * s, COULEURS["texte_doux"])]
            xr = round(x) + 0.5
            c.set_source_rgba(1, 1, 1, 0.35)
            c.set_line_width(1)
            c.move_to(xr, self.by0)
            c.line_to(xr, self.cy1)
            c.stroke()
        bulle_info(c, x, y, lignes, self.largeur, self.hauteur)
