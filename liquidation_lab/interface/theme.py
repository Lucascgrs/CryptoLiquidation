"""
Apparence : palette, polices, mise en forme des nombres.

Les couleurs de l'application reprennent celles de CryptoPatterns et
CryptoSupertrend, pour que les applications se ressemblent. Les deux couleurs
des graphiques ont été vérifiées (luminosité, contraste, daltonisme) sur les
fonds sombres utilisés :
- rouge #e66767 : longs liquidés (le prix a baissé) ;
- cyan  #239fc0 : shorts liquidés (le prix a monté).
Le côté est aussi toujours dit autrement que par la couleur : position
(en dessous / au-dessus, à gauche / à droite du prix), triangle ▼ ▲, texte.
"""

from __future__ import annotations

from tkinter import ttk

# nombres, prix et durées : les mêmes fonctions que la ligne de commande
from ..formats import dollars, duree, entier, heure, il_y_a, pourcent, prix, quantite

__all__ = ["COULEURS", "LONG", "SHORT", "POLICE", "rgb", "melanger", "configurer_tableaux",
           "dollars", "duree", "entier", "heure", "il_y_a", "pourcent", "prix", "quantite"]

COULEURS = {
    "fond":         "#1a1a1a",
    "panneau":      "#242424",
    "carte":        "#2b2b2b",
    "toile":        "#161616",
    "accent":       "#1f6aa5",
    "accent_clair": "#2e86c1",
    "texte":        "#e6e6e6",
    "texte_doux":   "#9aa0a6",
    "texte_pale":   "#6b7075",
    "grille":       "#262626",
    "axe":          "#3a3a3a",
    "neutre":       "#3a3a3a",
    "long":         "#e66767",
    "short":        "#239fc0",
    # états (toujours accompagnés d'un mot)
    "bon":          "#0ca30c",
    "attention":    "#fab219",
    "grave":        "#d03b3b",
}
LONG, SHORT = COULEURS["long"], COULEURS["short"]
POLICE = "Segoe UI"


def rgb(couleur: str, alpha: float | None = None) -> tuple:
    c = couleur.lstrip("#")
    r, g, b = (int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return (r, g, b) if alpha is None else (r, g, b, alpha)


def melanger(a: str, b: str, part_b: float) -> str:
    ra, ga, ba = rgb(a)
    rb, gb, bb = rgb(b)
    m = lambda x, y: int(round((x * (1 - part_b) + y * part_b) * 255))     # noqa: E731
    return f"#{m(ra, rb):02x}{m(ga, gb):02x}{m(ba, bb):02x}"


def configurer_tableaux() -> None:
    """Accorde les tableaux ttk (Treeview) au thème sombre de CustomTkinter."""
    style = ttk.Style()
    style.theme_use("default")
    style.configure("Treeview", background=COULEURS["carte"], foreground=COULEURS["texte"],
                    fieldbackground=COULEURS["carte"], rowheight=24, borderwidth=0, font=(POLICE, 10))
    style.configure("Treeview.Heading", background=COULEURS["accent"], foreground="white",
                    relief="flat", font=(POLICE + " Semibold", 10))
    style.map("Treeview", background=[("selected", COULEURS["accent_clair"])])
    style.map("Treeview.Heading", background=[("active", COULEURS["accent_clair"])])
    style.configure("Vertical.TScrollbar", background=COULEURS["neutre"],
                    troughcolor=COULEURS["panneau"], borderwidth=0, arrowsize=12)

