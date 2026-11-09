"""
Le fil : les dernières liquidations, la plus récente en haut. Chaque nouvelle
arrivée glisse en place et s'éclaire brièvement de la couleur de son côté.
"""

from __future__ import annotations

import time

import cairo
import numpy as np

from .. import config
from .donnees import CODES_PLATEFORMES, Memoire
from .rendu import Toile, horloge, rectangle_arrondi, texte, triangle
from .theme import COULEURS, LONG, SHORT, dollars, entier, heure, prix, rgb

LIGNES_MAX = 150
DUREE_ECLAT = 1.6
DUREE_GLISSE = 0.28


class VueFil(Toile):
    def __init__(self, parent, memoire: Memoire, quand_clic=None):
        super().__init__(parent, fond=COULEURS["panneau"])
        self.memoire = memoire
        self.quand_clic = quand_clic
        self.indices: list[int] = []
        self.selection: int | None = None
        self._eclats: dict[int, float] = {}
        self._glisse = (0.0, 0.0)               # (début, pixels)
        self._survolee: int | None = None
        self.filtres = (300.0, None, None, 0.0)
        self.total = 0

    @property
    def h_ligne(self) -> float:
        return 46 * self.echelle

    @property
    def haut(self) -> float:
        return 40 * self.echelle

    def regler(self, fenetre: float, actif: str | None, plateformes: set[str] | None, valeur_min: float) -> None:
        self.filtres = (fenetre, actif, plateformes, valeur_min)
        indices = self.memoire.selection(horloge() - fenetre * 1000, None, actif, plateformes, valeur_min)
        self.total = len(indices)
        if len(indices):
            ordre = np.argsort(self.memoire.t[indices], kind="stable")
            self.indices = [int(i) for i in indices[ordre][::-1][:LIGNES_MAX]]
        else:
            self.indices = []
        self._eclats.clear()
        self.rafraichir()

    def nouvelles(self, indices: range) -> None:
        fenetre, actif, plateformes, valeur_min = self.filtres
        m = self.memoire
        code = m.code_actif(actif) if actif else None
        codes = {CODES_PLATEFORMES[p] for p in plateformes} if plateformes is not None else None
        arrivees = []
        for i in indices:
            if actif and m.actif[i] != code:
                continue
            if codes is not None and int(m.plateforme[i]) not in codes:
                continue
            if m.valeur[i] < valeur_min:
                continue
            arrivees.append(i)
        if not arrivees:
            return
        arrivees.sort(key=lambda i: m.t[i], reverse=True)
        self.indices = (arrivees + self.indices)[:LIGNES_MAX]
        self.total += len(arrivees)
        maintenant = time.monotonic()
        for i in arrivees:
            self._eclats[i] = maintenant
        debut, pixels = self._glisse
        reste = pixels * max(0.0, 1 - (maintenant - debut) / DUREE_GLISSE)
        self._glisse = (maintenant, min(reste + len(arrivees) * self.h_ligne, 6 * self.h_ligne))
        self.rafraichir()

    def dessiner(self, c: cairo.Context, l: int, h: int) -> None:
        s = self.echelle
        maintenant = time.monotonic()
        texte(c, 16 * s, 20 * s, "Fil en direct", 14 * s, COULEURS["texte"], gras=True)
        texte(c, l - 14 * s, 20 * s, f"{entier(self.total)} sur la fenêtre", 10.5 * s, COULEURS["texte_doux"],
              ancre="e")
        debut, pixels = self._glisse
        f = min(1.0, (maintenant - debut) / DUREE_GLISSE)
        decalage = pixels * (1 - f) ** 3                # glissement qui ralentit en arrivant
        c.save()
        c.rectangle(0, self.haut, l, h - self.haut)
        c.clip()
        anime = f < 1
        for k, i in enumerate(self.indices):
            y = self.haut + k * self.h_ligne - decalage
            if y > h:
                break
            if y + self.h_ligne < self.haut:
                continue
            anime |= self._ligne(c, i, y, l, maintenant)
        if not self.indices:
            texte(c, l / 2, self.haut + 60 * s, "Aucune liquidation pour l'instant", 12 * s,
                  COULEURS["texte_doux"], ancre="c")
        c.restore()
        self._eclats = {i: t for i, t in self._eclats.items() if maintenant - t < DUREE_ECLAT}
        if anime:
            self.animer(True, 40)

    def _ligne(self, c, i: int, y: float, l: int, maintenant: float) -> bool:
        s = self.echelle
        m = self.memoire
        short = bool(m.short[i])
        couleur = SHORT if short else LONG
        v = float(m.valeur[i])
        hl = self.h_ligne
        anime = False
        fond = None
        if i in self._eclats:
            age = (maintenant - self._eclats[i]) / DUREE_ECLAT
            if age < 1:
                fond = (couleur, 0.28 * (1 - age))
                anime = True
        if i == self.selection:
            fond = ("#ffffff", 0.10)
        elif i == self._survolee and fond is None:
            fond = ("#ffffff", 0.05)
        if fond:
            rectangle_arrondi(c, 6 * s, y + 2 * s, l - 12 * s, hl - 4 * s, 6 * s)
            c.set_source_rgba(*rgb(fond[0], fond[1]))
            c.fill()
        c.rectangle(6 * s, y + 8 * s, (5 if v >= 1e5 else 3) * s, hl - 16 * s)
        c.set_source_rgba(*rgb(couleur))
        c.fill()
        triangle(c, 24 * s, y + hl * 0.34, 5 * s, short, couleur)
        actif = m.actifs[m.actif[i]]
        gros = v >= 1e5
        texte(c, 36 * s, y + hl * 0.34, actif, 13 * s, COULEURS["texte"], gras=True)
        texte(c, l - 14 * s, y + hl * 0.34, dollars(v), (14 if gros else 13) * s, COULEURS["texte"],
              gras=gros, semi=not gros, ancre="e")
        plateforme = config.PLATEFORMES[m.cle_plateforme(i)].nom
        texte(c, 36 * s, y + hl * 0.72, f"{plateforme} · {heure(float(m.t[i]))}", 10.5 * s,
              COULEURS["texte_doux"])
        texte(c, l - 14 * s, y + hl * 0.72, f"{'short' if short else 'long'} à {prix(float(m.prix[i]))} $",
              10.5 * s, COULEURS["texte_doux"], ancre="e")
        c.rectangle(14 * s, y + hl - 0.5, l - 28 * s, 1)
        c.set_source_rgba(*rgb(COULEURS["grille"]))
        c.fill()
        return anime

    def _ligne_sous(self, y: float) -> int | None:
        k = int((y - self.haut) // self.h_ligne)
        return self.indices[k] if 0 <= k < len(self.indices) and y >= self.haut else None

    def survol(self, x, y) -> None:
        ligne = None if y is None else self._ligne_sous(y)
        if ligne != self._survolee:
            self._survolee = ligne
            self.configure(cursor="hand2" if ligne is not None else "")
            self.rafraichir()

    def clic(self, x: float, y: float) -> None:
        self.selection = self._ligne_sous(y)
        if self.quand_clic:
            self.quand_clic(self.selection)
        self.rafraichir()
