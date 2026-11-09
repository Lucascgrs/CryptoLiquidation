"""
Toiles dessinées avec cairo (anticrénelage, transparences) puis affichées dans Tk.

`Toile` gère la surface à la taille du widget, la conversion vers une image Tk
et les événements de la souris ; les vues n'ont qu'à écrire `dessiner()`.
Le rendu n'a lieu que sur demande (`rafraichir()`), ou en continu pendant une
animation (`animer()`).
"""

from __future__ import annotations

import math
import time
import tkinter as tk

import cairo
from PIL import Image, ImageTk

from .theme import COULEURS, POLICE, rgb

try:
    import customtkinter as ctk
except ImportError:                                 # pragma: no cover
    ctk = None


class Toile(tk.Canvas):
    def __init__(self, parent, fond: str = COULEURS["toile"], **options):
        super().__init__(parent, highlightthickness=0, bd=0, bg=fond, **options)
        self.fond = fond
        self.largeur = self.hauteur = 0
        self.echelle = 1.0
        self.souris: tuple[float, float] | None = None
        self._surface = None
        self._photo = None
        self._image = None
        self._prevu = None
        self._animation = None
        self.visible = True
        self.bind("<Configure>", self._redimensionner)
        self.bind("<Motion>", self._mouvement)
        self.bind("<Leave>", self._sortie)
        self.bind("<Button-1>", lambda e: self.clic(e.x, e.y))
        self.bind("<Double-Button-1>", lambda e: self.double_clic(e.x, e.y))

    # -- à écrire par les vues -------------------------------------------
    def dessiner(self, c: cairo.Context, l: int, h: int) -> None:
        raise NotImplementedError

    def taille_changee(self) -> None:
        pass

    def clic(self, x: float, y: float) -> None:
        pass

    def double_clic(self, x: float, y: float) -> None:
        pass

    def survol(self, x: float | None, y: float | None) -> None:
        self.rafraichir()

    # -- mécanique ---------------------------------------------------------
    def _redimensionner(self, evenement) -> None:
        l, h = max(2, evenement.width), max(2, evenement.height)
        if (l, h) == (self.largeur, self.hauteur):
            return
        self.largeur, self.hauteur = l, h
        try:
            self.echelle = ctk.ScalingTracker.get_widget_scaling(self) if ctk else 1.0
        except Exception:                           # noqa: BLE001
            self.echelle = 1.0
        self._surface = cairo.ImageSurface(cairo.FORMAT_RGB24, l, h)
        self._photo = ImageTk.PhotoImage(Image.new("RGB", (l, h), self.fond))
        if self._image is None:
            self._image = self.create_image(0, 0, anchor="nw", image=self._photo)
        else:
            self.itemconfigure(self._image, image=self._photo)
        self.taille_changee()
        self.rafraichir()

    def rafraichir(self) -> None:
        if self._prevu is None and self._surface is not None:
            self._prevu = self.after_idle(self._rendre)

    def _rendre(self, force: bool = False) -> None:
        self._prevu = None
        if self._surface is None or not self.visible:
            return
        if not force and not self.winfo_viewable():
            # fenêtre réduite : on ne dessine pas, mais on revient voir dans une demi-seconde
            if self._animation is None:
                self._animation = self.after(500, self._image_suivante)
            return
        c = cairo.Context(self._surface)
        c.set_source_rgb(*rgb(self.fond))
        c.paint()
        self.dessiner(c, self.largeur, self.hauteur)
        self._surface.flush()
        image = Image.frombuffer("RGB", (self.largeur, self.hauteur), self._surface.get_data(),
                                 "raw", "BGRX", 0, 1)
        self._photo.paste(image)

    def animer(self, actif: bool, images_par_seconde: float = 30) -> None:
        """Rendu continu tant que `actif` (les vues l'appellent depuis dessiner())."""
        if actif and self._animation is None and self.visible:
            self._animation = self.after(int(1000 / images_par_seconde), self._image_suivante)

    def _image_suivante(self) -> None:
        self._animation = None
        self.rafraichir()

    def _mouvement(self, evenement) -> None:
        self.souris = (evenement.x, evenement.y)
        self.survol(evenement.x, evenement.y)

    def _sortie(self, _evenement) -> None:
        self.souris = None
        self.survol(None, None)

    def image_pil(self) -> Image.Image:
        """L'image rendue (pour les essais : jamais de capture d'écran)."""
        self._rendre(force=True)
        return Image.frombuffer("RGB", (self.largeur, self.hauteur), self._surface.get_data(),
                                "raw", "BGRX", 0, 1).copy()


# ----------------------------------------------------------------------
# AIDES DE DESSIN
# ----------------------------------------------------------------------
def police(c: cairo.Context, taille: float, gras: bool = False, semi: bool = False, chaine: str = "") -> None:
    famille = POLICE + (" Semibold" if semi and not gras else "")
    if any(ord(caractere) >= 0x2E80 for caractere in chaine):
        famille = "Microsoft YaHei UI"          # quelques cryptos ont un nom chinois (龙虾…)
    c.select_font_face(famille, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if gras else cairo.FONT_WEIGHT_NORMAL)
    c.set_font_size(taille)


def texte(c: cairo.Context, x: float, y: float, chaine: str, taille: float = 12, couleur: str = COULEURS["texte"],
          alpha: float = 1.0, gras: bool = False, semi: bool = False, ancre: str = "w", base: str = "m") -> tuple:
    """
    Écrit `chaine` ; `ancre` : w (gauche), e (droite), c (centre) ; `base` : m
    (milieu), t (haut), b (bas, ligne de base). Rend le rectangle occupé (x0, y0, x1, y1).
    """
    police(c, taille, gras, semi, chaine)
    e = c.text_extents(chaine)
    largeur = e.x_advance
    x0 = x if ancre == "w" else x - largeur if ancre == "e" else x - largeur / 2
    if base == "m":
        yb = y + taille * 0.36
    elif base == "t":
        yb = y + taille * 0.78
    else:
        yb = y
    c.set_source_rgba(*rgb(couleur, alpha))
    c.move_to(x0, yb)
    c.show_text(chaine)
    c.new_path()            # sinon le prochain arc partirait d'ici par un trait
    return (x0, yb - taille * 0.78, x0 + largeur, yb + taille * 0.22)


def largeur_texte(c: cairo.Context, chaine: str, taille: float, gras: bool = False, semi: bool = False) -> float:
    police(c, taille, gras, semi, chaine)
    return c.text_extents(chaine).x_advance


def triangle(c: cairo.Context, x: float, y: float, demi: float, haut: bool, couleur: str, alpha: float = 1.0) -> None:
    """Triangle plein centré en (x, y), pointe vers le haut (▲) ou vers le bas (▼)."""
    h = demi * 1.7
    if haut:
        c.move_to(x, y - h / 2)
        c.line_to(x + demi, y + h / 2)
        c.line_to(x - demi, y + h / 2)
    else:
        c.move_to(x, y + h / 2)
        c.line_to(x + demi, y - h / 2)
        c.line_to(x - demi, y - h / 2)
    c.close_path()
    c.set_source_rgba(*rgb(couleur, alpha))
    c.fill()


def rectangle_arrondi(c: cairo.Context, x: float, y: float, l: float, h: float, r: float) -> None:
    r = max(0.0, min(r, l / 2, h / 2))
    c.new_sub_path()
    c.arc(x + l - r, y + r, r, -math.pi / 2, 0)
    c.arc(x + l - r, y + h - r, r, 0, math.pi / 2)
    c.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    c.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    c.close_path()


def bulle_info(c: cairo.Context, x: float, y: float, lignes: list[tuple], l_max: float, h_max: float) -> None:
    """
    Infobulle près de (x, y), sans sortir de la toile. `lignes` : (texte, taille,
    couleur, gras) ; une ligne peut commencer par une pastille (couleur, texte).
    """
    marge, interligne = 10, 4
    largeur = 0.0
    hauteur = marge
    for ligne in lignes:
        chaine, taille = ligne[0], ligne[1]
        gras = len(ligne) > 3 and ligne[3]
        largeur = max(largeur, largeur_texte(c, chaine, taille, gras) + (14 if len(ligne) > 4 else 0))
        hauteur += taille + interligne
    largeur += 2 * marge
    hauteur += marge - interligne
    bx = x + 16 if x + 16 + largeur < l_max - 4 else x - 16 - largeur
    by = min(max(4, y - hauteur / 2), h_max - hauteur - 4)
    rectangle_arrondi(c, bx, by, largeur, hauteur, 6)
    c.set_source_rgba(*rgb("#0e0e0e", 0.94))
    c.fill_preserve()
    c.set_source_rgba(*rgb("#ffffff", 0.12))
    c.set_line_width(1)
    c.stroke()
    yy = by + marge
    for ligne in lignes:
        chaine, taille, couleur = ligne[0], ligne[1], ligne[2]
        gras = len(ligne) > 3 and ligne[3]
        xx = bx + marge
        if len(ligne) > 4:                      # pastille de couleur devant le texte
            c.set_source_rgba(*rgb(ligne[4]))
            c.arc(xx + 4, yy + taille * 0.5, 4, 0, 2 * math.pi)
            c.fill()
            xx += 14
        texte(c, xx, yy, chaine, taille, couleur, gras=gras, base="t")
        yy += taille + interligne


def horloge() -> float:
    return time.time() * 1000
