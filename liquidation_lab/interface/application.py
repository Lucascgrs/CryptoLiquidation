"""
Fenêtre principale : barre latérale, trois pages, barre d'état.

L'interface ne se connecte à aucune plateforme : elle lit ce que le collecteur
écrit (registre CSV, base Hyperliquid, état JSON). Toutes les 300 ms, les
nouvelles lignes du registre sont lues et passées à la page « En direct » ;
chaque seconde, l'état du collecteur est relu et les pages se mettent à jour.
"""

from __future__ import annotations

import os
import queue
import time
import traceback

import customtkinter as ctk

from .. import config
from ..collecteur import etat_collecteur
from ..hyperliquid import base
from . import theme
from .composants import pastille
from .donnees import HEURES_EN_MEMOIRE, Lecteur, Memoire, Prix
from .gestion import Gestion
from .page_collecteur import PageCollecteur
from .page_direct import PageDirect
from .page_niveaux import PageNiveaux
from .theme import COULEURS, LONG, SHORT, entier

NAVIGATION = [
    ("direct", "En direct", "Les liquidations, partout"),
    ("niveaux", "Niveaux Hyperliquid", "Les vrais prix de liquidation"),
    ("collecteur", "Collecteur", "Démarrer, arrêter, surveiller"),
]


class Application(ctk.CTk):

    def __init__(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        super().__init__()
        config.preparer_dossiers()
        self.title("CryptoLiquidation")
        self.geometry("1500x940")
        self.minsize(1280, 800)
        self.configure(fg_color=COULEURS["fond"])
        theme.configurer_tableaux()

        self.reglages_collecte = config.charger_reglages()
        self.reglages = dict(self.reglages_collecte.get("interface") or {})
        self.memoire = Memoire()
        self.lecteur = Lecteur(self.memoire)
        self.prix = Prix()
        self.gestion = Gestion()
        self.etat = etat_collecteur()
        self.file = queue.Queue()
        self._coins = []
        self._coins_lus = 0.0
        self._purge = time.monotonic()

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._barre_laterale()
        self.contenu = ctk.CTkFrame(self, fg_color="transparent")
        self.contenu.grid(row=0, column=1, sticky="nsew", padx=16, pady=(14, 6))
        self.contenu.grid_columnconfigure(0, weight=1)
        self.contenu.grid_rowconfigure(0, weight=1)
        self._barre_etat()

        self.pages = {
            "direct": PageDirect(self.contenu, self),
            "niveaux": PageNiveaux(self.contenu, self),
            "collecteur": PageCollecteur(self.contenu, self),
        }
        self.page_active = None
        self.afficher(self.reglages.get("page", "direct") if self.reglages.get("page") in self.pages else "direct")
        self.lecteur.charger_en_fond(lambda: self.file.put("charge"))
        if self.reglages_collecte.get("demarrer_a_l_ouverture") and not self.etat:
            self.demarrer_collecte()
        self.after(300, self._sonder)
        self.after(1000, self._tic)
        self.protocol("WM_DELETE_WINDOW", self.fermer)

    # ------------------------------------------------------------------
    # STRUCTURE
    # ------------------------------------------------------------------
    def _barre_laterale(self) -> None:
        barre = ctk.CTkFrame(self, width=230, corner_radius=0, fg_color=COULEURS["panneau"])
        barre.grid(row=0, column=0, rowspan=2, sticky="nsew")
        barre.grid_propagate(False)
        ctk.CTkLabel(barre, text="Liquidations", font=ctk.CTkFont(size=24, weight="bold")
                     ).pack(pady=(26, 0), padx=20, anchor="w")
        ctk.CTkLabel(barre, text="en direct · niveaux réels", font=ctk.CTkFont(size=12),
                     text_color=COULEURS["texte_doux"]).pack(padx=20, anchor="w", pady=(0, 24))
        self.boutons = {}
        for cle, libelle, aide in NAVIGATION:
            bouton = ctk.CTkButton(barre, text=f"{libelle}\n{aide}", anchor="w", height=54, corner_radius=8,
                                   fg_color="transparent", hover_color=COULEURS["carte"], font=ctk.CTkFont(size=13),
                                   command=lambda c=cle: self.afficher(c))
            bouton.pack(fill="x", padx=12, pady=3)
            self.boutons[cle] = bouton
        legende = ctk.CTkFrame(barre, fg_color="transparent")
        legende.pack(side="bottom", fill="x", padx=20, pady=20)
        self.etat_lateral = ctk.CTkLabel(legende, text="", font=ctk.CTkFont(size=12, weight="bold"), anchor="w")
        self.etat_lateral.pack(fill="x", pady=(0, 14))
        for couleur, titre, aide in ((LONG, "Longs liquidés", "le prix a baissé : les acheteurs\nà levier sont "
                                                              "forcés de vendre"),
                                     (SHORT, "Shorts liquidés", "le prix a monté : les vendeurs\nà découvert sont "
                                                                "forcés de racheter")):
            ctk.CTkLabel(legende, text=f"  {titre}", image=pastille(couleur, 10), compound="left",
                         font=ctk.CTkFont(size=12, weight="bold"), anchor="w").pack(fill="x")
            ctk.CTkLabel(legende, text=aide, font=ctk.CTkFont(size=11), text_color=COULEURS["texte_doux"],
                         anchor="w", justify="left").pack(fill="x", pady=(0, 10))

    def _barre_etat(self) -> None:
        barre = ctk.CTkFrame(self, height=30, corner_radius=0, fg_color=COULEURS["panneau"])
        barre.grid(row=1, column=1, sticky="ew")
        self.texte_statut = ctk.CTkLabel(barre, text="Lecture du registre…", anchor="w",
                                         text_color=COULEURS["texte_doux"])
        self.texte_statut.pack(side="left", padx=14, fill="x", expand=True)
        self.texte_memoire = ctk.CTkLabel(barre, text="", anchor="e", text_color=COULEURS["texte_doux"])
        self.texte_memoire.pack(side="right", padx=14)

    def afficher(self, cle: str) -> None:
        if self.page_active is not None:
            page = self.pages[self.page_active]
            page.grid_remove()
            if hasattr(page, "au_masquage"):
                page.au_masquage()
        self.page_active = cle
        self.pages[cle].grid(row=0, column=0, sticky="nsew")
        for c, bouton in self.boutons.items():
            bouton.configure(fg_color=COULEURS["accent"] if c == cle else "transparent")
        if hasattr(self.pages[cle], "au_affichage"):
            self.pages[cle].au_affichage()

    def statut(self, texte: str, erreur: bool = False) -> None:
        self.texte_statut.configure(text=texte, text_color=COULEURS["grave"] if erreur else COULEURS["texte_doux"])

    # ------------------------------------------------------------------
    # COLLECTE
    # ------------------------------------------------------------------
    def demarrer_collecte(self) -> None:
        self.enregistrer_reglages_collecte()
        erreur = self.gestion.demarrer()
        self.statut(erreur or "Démarrage de la collecte…", erreur=bool(erreur) and "déjà" not in erreur)

    def enregistrer_reglages_collecte(self) -> None:
        try:
            self.reglages_collecte["interface"] = self._reglages_pages()
            config.enregistrer_reglages(self.reglages_collecte)
        except Exception:                               # noqa: BLE001
            traceback.print_exc()

    def coins_hyperliquid(self) -> list[str]:
        """Les cryptos d'Hyperliquid : celles du collecteur en marche, sinon celles de la base."""
        mids = ((self.etat or {}).get("hyperliquid") or {}).get("mids")
        if mids:
            return list(mids)
        if not self._coins and time.monotonic() - self._coins_lus > 30 and os.path.exists(config.BASE_HYPERLIQUID):
            self._coins_lus = time.monotonic()
            try:
                self._coins = list(base.lire_marches()["actif"])
            except Exception:                           # noqa: BLE001
                self._coins = []
        return self._coins

    # ------------------------------------------------------------------
    # BOUCLES
    # ------------------------------------------------------------------
    def _sonder(self) -> None:
        try:
            while True:
                if self.file.get_nowait() == "charge":
                    if self.lecteur.erreur:
                        self.statut(f"Registre illisible : {self.lecteur.erreur}", erreur=True)
                    else:
                        self.statut(f"Registre relu : {entier(self.memoire.n)} liquidations des dernières "
                                    f"{HEURES_EN_MEMOIRE} heures.")
                    self.pages["direct"].appliquer()
        except queue.Empty:
            pass
        try:
            nouvelles = self.lecteur.sonder()
            if len(nouvelles):
                self.pages["direct"].nouvelles(nouvelles)
            if time.monotonic() - self._purge > 600:
                self._purge = time.monotonic()
                version = self.memoire.version
                self.memoire.purger(int(time.time() * 1000) - HEURES_EN_MEMOIRE * 3_600_000)
                if self.memoire.version != version:
                    self.pages["direct"].appliquer()
        except Exception:                               # noqa: BLE001
            traceback.print_exc()
        self.after(300, self._sonder)

    def _tic(self) -> None:
        try:
            self.etat = etat_collecteur()
            if self.etat:
                self.etat_lateral.configure(text="●  Collecte en marche", text_color=COULEURS["bon"])
            else:
                self.etat_lateral.configure(text="Collecte arrêtée", text_color=COULEURS["texte_doux"])
            self.texte_memoire.configure(text=f"{entier(self.memoire.n)} liquidations en mémoire "
                                              f"({HEURES_EN_MEMOIRE} h)")
            page = self.pages.get(self.page_active)
            if page is not None and hasattr(page, "tic"):
                page.tic()
        except Exception:                               # noqa: BLE001
            traceback.print_exc()
        self.after(1000, self._tic)

    # ------------------------------------------------------------------
    def _reglages_pages(self) -> dict:
        reglages = {"page": self.page_active}
        for page in getattr(self, "pages", {}).values():
            if hasattr(page, "reglages"):
                reglages.update(page.reglages())
        return reglages

    def fermer(self) -> None:
        self.enregistrer_reglages_collecte()
        self.gestion.fermer(self.reglages_collecte.get("arreter_en_fermant", True))
        self.destroy()


def lancer() -> None:
    app = Application()
    app.state("zoomed")              # fenêtre agrandie sous Windows
    app.mainloop()
