"""Petits éléments communs aux pages : cartes de chiffres, puces de plateformes, barres de filtres."""

from __future__ import annotations

import customtkinter as ctk
from PIL import Image, ImageDraw

from .theme import COULEURS

_PASTILLES: dict[tuple, ctk.CTkImage] = {}


def pastille(couleur: str, taille: int = 10) -> ctk.CTkImage:
    """Petit disque de couleur, pour une puce ou une légende."""
    cle = (couleur, taille)
    if cle not in _PASTILLES:
        image = Image.new("RGBA", (taille * 4, taille * 4), (0, 0, 0, 0))
        ImageDraw.Draw(image).ellipse((0, 0, taille * 4 - 1, taille * 4 - 1), fill=couleur)
        _PASTILLES[cle] = ctk.CTkImage(light_image=image, dark_image=image, size=(taille, taille))
    return _PASTILLES[cle]


class Carte(ctk.CTkFrame):
    """Une carte de chiffre : titre discret, valeur en grand, détail en dessous."""

    def __init__(self, parent, titre: str, couleur_marque: str | None = None):
        super().__init__(parent, fg_color=COULEURS["carte"], corner_radius=10)
        entete = ctk.CTkFrame(self, fg_color="transparent")
        entete.pack(fill="x", padx=14, pady=(10, 0))
        if couleur_marque:
            ctk.CTkLabel(entete, text="", image=pastille(couleur_marque, 9), width=12).pack(side="left", padx=(0, 6))
        self.titre = ctk.CTkLabel(entete, text=titre, font=ctk.CTkFont(size=12), text_color=COULEURS["texte_doux"],
                                  anchor="w")
        self.titre.pack(side="left", fill="x")
        self.valeur = ctk.CTkLabel(self, text="—", font=ctk.CTkFont(size=22, weight="bold"), anchor="w")
        self.valeur.pack(fill="x", padx=14)
        self.detail = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=11), text_color=COULEURS["texte_doux"],
                                   anchor="w")
        self.detail.pack(fill="x", padx=14, pady=(0, 10))

    def montrer(self, valeur: str, detail: str = "") -> None:
        if self.valeur.cget("text") != valeur:
            self.valeur.configure(text=valeur)
        if self.detail.cget("text") != detail:
            self.detail.configure(text=detail)


def segments(parent, valeurs: list[str], defaut: str, commande, largeur: int | None = None) -> ctk.CTkSegmentedButton:
    bouton = ctk.CTkSegmentedButton(parent, values=valeurs, command=commande, font=ctk.CTkFont(size=12),
                                    selected_color=COULEURS["accent"], selected_hover_color=COULEURS["accent_clair"],
                                    dynamic_resizing=True)
    bouton.set(defaut if defaut in valeurs else valeurs[0])
    if largeur:
        bouton.configure(width=largeur)
    return bouton


def etiquette(parent, texte: str) -> ctk.CTkLabel:
    return ctk.CTkLabel(parent, text=texte, font=ctk.CTkFont(size=12), text_color=COULEURS["texte_doux"])


class Puces(ctk.CTkFrame):
    """Une puce par plateforme, à cocher ; un clic droit (ou « Seule ») ne garde que celle-là."""

    def __init__(self, parent, elements: list[tuple[str, str, str]], actives: set[str], commande):
        super().__init__(parent, fg_color="transparent")
        self.commande = commande
        self.actives = set(actives)
        self.boutons = {}
        etiquette(self, "Plateformes").pack(side="left", padx=(0, 8))
        for cle, nom, couleur in elements:
            bouton = ctk.CTkButton(self, text=nom, image=pastille(couleur), compound="left", width=10, height=26,
                                   corner_radius=13, font=ctk.CTkFont(size=12), border_width=1,
                                   command=lambda c=cle: self.basculer(c))
            bouton.bind("<Button-3>", lambda e, c=cle: self.seule(c))
            bouton.pack(side="left", padx=3)
            self.boutons[cle] = bouton
        ctk.CTkButton(self, text="Toutes", width=60, height=26, corner_radius=13, font=ctk.CTkFont(size=12),
                      fg_color="transparent", border_width=1, border_color=COULEURS["neutre"],
                      hover_color=COULEURS["carte"], command=self.toutes).pack(side="left", padx=(10, 3))
        self._peindre()

    def _peindre(self) -> None:
        for cle, bouton in self.boutons.items():
            actif = cle in self.actives
            bouton.configure(fg_color=COULEURS["carte"] if actif else "transparent",
                             hover_color=COULEURS["neutre"],
                             border_color=COULEURS["neutre"] if actif else COULEURS["carte"],
                             text_color=COULEURS["texte"] if actif else COULEURS["texte_pale"])

    def basculer(self, cle: str) -> None:
        if cle in self.actives and len(self.actives) > 1:
            self.actives.discard(cle)
        else:
            self.actives.add(cle)
        self._peindre()
        self.commande(set(self.actives))

    def seule(self, cle: str) -> None:
        self.actives = {cle}
        self._peindre()
        self.commande(set(self.actives))

    def toutes(self) -> None:
        self.actives = set(self.boutons)
        self._peindre()
        self.commande(set(self.actives))
