"""
Page « En direct » : les liquidations de toutes les plateformes, au fil de l'eau.

En haut, les filtres (crypto, fenêtre de temps, taille minimale, vue,
plateformes) et les chiffres de la fenêtre ; au centre, la rivière des
liquidations et le fil ; en bas, le bilan par plateforme et par crypto.
"""

from __future__ import annotations

import time

import customtkinter as ctk
import numpy as np

from .. import config
from ..symboles import normaliser
from .composants import Carte, Puces, etiquette, segments
from .donnees import coin_hyperliquid
from .rendu import horloge
from .theme import COULEURS, LONG, SHORT, dollars, il_y_a
from .vue_bilan import VueBilan
from .vue_fil import VueFil
from .vue_riviere import VueRiviere

FENETRES = {"5 min": 300, "15 min": 900, "1 h": 3600, "4 h": 14400, "24 h": 86400}
TAILLES = {"Tout": 0.0, "1 k$": 1e3, "10 k$": 1e4, "100 k$": 1e5, "1 M$": 1e6}
VUES = {"Volcan": "volcan", "Prix": "prix"}
TOUTES = "Toutes"


class PageDirect(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        r = app.reglages.get("direct", {})
        self.fenetre = r.get("fenetre", "15 min") if r.get("fenetre") in FENETRES else "15 min"
        self.taille = r.get("taille", "Tout") if r.get("taille") in TAILLES else "Tout"
        self.vue = r.get("vue", "Volcan") if r.get("vue") in VUES else "Volcan"
        self.actif = r.get("actif") or None
        plateformes = set(r.get("plateformes") or config.ORDRE_PLATEFORMES) & set(config.ORDRE_PLATEFORMES)
        self.plateformes = plateformes or set(config.ORDRE_PLATEFORMES)
        self._liste_actifs: list[str] = []
        self._dernier_tic_kpi = 0.0
        self._prix_binance_le = 0.0

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self._barre_filtres()
        self._cartes()
        centre = ctk.CTkFrame(self, fg_color="transparent")
        centre.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        centre.grid_columnconfigure(0, weight=1)
        centre.grid_rowconfigure(0, weight=1)
        cadre = ctk.CTkFrame(centre, fg_color=COULEURS["toile"], corner_radius=10)
        cadre.grid(row=0, column=0, sticky="nsew")
        self.riviere = VueRiviere(cadre, app.memoire, app.prix, quand_selection=self._selection_riviere)
        self.riviere.pack(fill="both", expand=True, padx=6, pady=6)
        cadre_fil = ctk.CTkFrame(centre, fg_color=COULEURS["panneau"], corner_radius=10, width=360)
        cadre_fil.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        cadre_fil.grid_propagate(False)
        cadre_fil.pack_propagate(False)
        self.fil = VueFil(cadre_fil, app.memoire, quand_clic=self._clic_fil)
        self.fil.pack(fill="both", expand=True, padx=4, pady=4)
        cadre_bilan = ctk.CTkFrame(self, fg_color=COULEURS["panneau"], corner_radius=10, height=220)
        cadre_bilan.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        cadre_bilan.pack_propagate(False)
        self.bilan = VueBilan(cadre_bilan, app.memoire, quand_actif=self.choisir_actif)
        self.bilan.pack(fill="both", expand=True, padx=4, pady=4)
        self.appliquer()

    # ------------------------------------------------------------------
    def _barre_filtres(self) -> None:
        barre = ctk.CTkFrame(self, fg_color="transparent")
        barre.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(barre, text="Liquidations en direct", font=ctk.CTkFont(size=20, weight="bold")
                     ).pack(side="left", padx=(2, 18))
        etiquette(barre, "Crypto").pack(side="left", padx=(0, 6))
        self.choix_actif = ctk.CTkComboBox(barre, values=[TOUTES], width=120, command=self._actif_choisi,
                                           font=ctk.CTkFont(size=12))
        self.choix_actif.set(self.actif or TOUTES)
        self.choix_actif.bind("<Return>", lambda e: self._actif_choisi(self.choix_actif.get()))
        self.choix_actif.pack(side="left", padx=(0, 14))
        etiquette(barre, "Fenêtre").pack(side="left", padx=(0, 6))
        segments(barre, list(FENETRES), self.fenetre, self._fenetre_choisie).pack(side="left", padx=(0, 14))
        etiquette(barre, "Taille").pack(side="left", padx=(0, 6))
        segments(barre, list(TAILLES), self.taille, self._taille_choisie).pack(side="left", padx=(0, 14))
        etiquette(barre, "Vue").pack(side="left", padx=(0, 6))
        self.choix_vue = segments(barre, list(VUES), self.vue, self._vue_choisie)
        self.choix_vue.pack(side="left")
        self.etat_collecte = ctk.CTkButton(barre, text="", height=28, corner_radius=14, font=ctk.CTkFont(size=12),
                                           fg_color=COULEURS["carte"], hover_color=COULEURS["neutre"],
                                           command=self._bouton_collecte)
        self.etat_collecte.pack(side="right")
        puces = Puces(self, [(p.cle, p.nom, p.couleur) for p in config.PLATEFORMES.values()], self.plateformes,
                      self._plateformes_choisies)
        puces.grid(row=1, column=0, sticky="w", pady=(8, 0))

    def _cartes(self) -> None:
        rangee = ctk.CTkFrame(self, fg_color="transparent")
        rangee.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        for k in range(5):
            rangee.grid_columnconfigure(k, weight=1, uniform="cartes")
        self.carte_total = Carte(rangee, "Liquidé sur la fenêtre")
        self.carte_longs = Carte(rangee, "Longs liquidés (le prix baisse)", LONG)
        self.carte_shorts = Carte(rangee, "Shorts liquidés (le prix monte)", SHORT)
        self.carte_max = Carte(rangee, "La plus grosse")
        self.carte_rythme = Carte(rangee, "Dernière minute")
        for k, carte in enumerate((self.carte_total, self.carte_longs, self.carte_shorts, self.carte_max,
                                   self.carte_rythme)):
            carte.grid(row=0, column=k, sticky="nsew", padx=(0 if k == 0 else 4, 0 if k == 4 else 4))
        self.equilibre = ctk.CTkProgressBar(rangee, height=8, corner_radius=4, progress_color=LONG, fg_color=SHORT)
        self.equilibre.set(0.5)
        self.equilibre.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(8, 0))

    def _bouton_collecte(self) -> None:
        """Collecte arrêtée : la démarrer ici même. En marche : voir son état."""
        if self.app.etat or self.app.gestion.demarrage_en_cours():
            self.app.afficher("collecteur")
        else:
            self.app.demarrer_collecte()
            self.tic()

    # ------------------------------------------------------------------
    # FILTRES
    # ------------------------------------------------------------------
    def _actif_choisi(self, valeur: str) -> None:
        valeur = (valeur or "").strip()
        self.actif = None if not valeur or valeur == TOUTES else normaliser(valeur)[0]
        self.choix_actif.set(self.actif or TOUTES)
        self.appliquer()

    def choisir_actif(self, actif: str | None) -> None:
        self.actif = actif
        self.choix_actif.set(actif or TOUTES)
        self.appliquer()

    def _fenetre_choisie(self, valeur: str) -> None:
        self.fenetre = valeur
        self.appliquer()

    def _taille_choisie(self, valeur: str) -> None:
        self.taille = valeur
        self.appliquer()

    def _vue_choisie(self, valeur: str) -> None:
        self.vue = valeur
        self.appliquer()

    def _plateformes_choisies(self, plateformes: set[str]) -> None:
        self.plateformes = plateformes
        self.appliquer()

    def _filtres(self):
        plateformes = None if len(self.plateformes) == len(config.ORDRE_PLATEFORMES) else set(self.plateformes)
        return FENETRES[self.fenetre], self.actif, plateformes, TAILLES[self.taille]

    def appliquer(self) -> None:
        fenetre, actif, plateformes, valeur_min = self._filtres()
        mode = VUES[self.vue]
        if mode == "prix" and actif:
            self.app.prix.suivre(actif, self.app.coins_hyperliquid(), int(horloge() - fenetre * 1000) - 60_000)
        self.riviere.regler(fenetre, actif, plateformes, valeur_min, mode)
        self.fil.regler(fenetre, actif, plateformes, valeur_min)
        self.bilan.regler(fenetre, actif, plateformes, valeur_min)
        self._dernier_tic_kpi = 0.0
        self.tic()

    # ------------------------------------------------------------------
    # SÉLECTION
    # ------------------------------------------------------------------
    def _selection_riviere(self, indice: int | None) -> None:
        self.fil.selection = indice
        self.fil.rafraichir()

    def _clic_fil(self, indice: int | None) -> None:
        self.riviere.selection = indice
        self.riviere.rafraichir()

    # ------------------------------------------------------------------
    # MISES À JOUR
    # ------------------------------------------------------------------
    def nouvelles(self, indices: range) -> None:
        self.riviere.nouvelles(indices)
        self.fil.nouvelles(indices)

    def tic(self) -> None:
        """Chaque seconde (et à chaque changement de filtre)."""
        etat = self.app.etat
        if etat:
            depuis = (time.time() * 1000 - etat["debut"]) / 1000
            self.etat_collecte.configure(text=f"●  Collecte en marche · {_duree_courte(depuis)}",
                                         text_color=COULEURS["bon"])
        elif self.app.gestion.demarrage_en_cours():
            self.etat_collecte.configure(text="Démarrage de la collecte…", text_color=COULEURS["attention"])
        else:
            self.etat_collecte.configure(text="Collecte arrêtée — la démarrer", text_color=COULEURS["attention"])
        self._prix_courant(etat)
        if not self.app.lecteur.pret:
            self.riviere.message_vide = "Lecture du registre…"
        elif etat:
            self.riviere.message_vide = "En attente des premières liquidations sur cette fenêtre…"
        else:
            self.riviere.message_vide = "La collecte est arrêtée : démarre-la avec le bouton en haut à droite."
        if self.vue == "Prix" and not self.actif:
            self.riviere.message_vide = "Choisis une crypto pour la vue Prix (liste ou bilan ci-dessous)."
        maintenant = time.monotonic()
        if maintenant - self._dernier_tic_kpi >= 1.0:
            self._dernier_tic_kpi = maintenant
            self._chiffres()

    def _prix_courant(self, etat) -> None:
        if not (self.vue == "Prix" and self.actif):
            return
        coin = coin_hyperliquid(self.actif, self.app.coins_hyperliquid())
        mids = ((etat or {}).get("hyperliquid") or {}).get("mids") or {}
        if coin and coin in mids:
            self.app.prix.ajouter(etat["maj"], float(mids[coin]) / normaliser(coin)[1])
        elif self.app.prix.source == "Binance" and time.monotonic() - self._prix_binance_le > 20:
            self._prix_binance_le = time.monotonic()
            fenetre = FENETRES[self.fenetre]
            self.app.prix.actif = None          # relecture des bougies
            self.app.prix.suivre(self.actif, self.app.coins_hyperliquid(), int(horloge() - fenetre * 1000) - 60_000)

    def _chiffres(self) -> None:
        fenetre, actif, plateformes, valeur_min = self._filtres()
        m = self.app.memoire
        maintenant = horloge()
        indices = m.selection(maintenant - fenetre * 1000, None, actif, plateformes, valeur_min)
        self.fil.total = len(indices)
        if not len(indices):
            for carte in (self.carte_total, self.carte_longs, self.carte_shorts, self.carte_max, self.carte_rythme):
                carte.montrer("—", "")
            self.equilibre.set(0.5)
        else:
            valeurs = m.valeur[indices]
            shorts = m.short[indices]
            v_longs, v_shorts = float(valeurs[~shorts].sum()), float(valeurs[shorts].sum())
            total = v_longs + v_shorts
            self.carte_total.montrer(dollars(total), f"{len(indices)} liquidations")
            self.carte_longs.montrer(dollars(v_longs), f"{v_longs / total:.0%} du total · {int((~shorts).sum())} "
                                     "liquidations".replace("%", " %"))
            self.carte_shorts.montrer(dollars(v_shorts), f"{v_shorts / total:.0%} du total · {int(shorts.sum())} "
                                      "liquidations".replace("%", " %"))
            k = int(indices[int(np.argmax(valeurs))])
            nom = config.PLATEFORMES[m.cle_plateforme(k)].nom
            self.carte_max.montrer(dollars(float(m.valeur[k])),
                                   f"{m.actifs[m.actif[k]]} {'short' if m.short[k] else 'long'} · {nom} · "
                                   f"{il_y_a(float(m.t[k]))}")
            minute = indices[m.t[indices] >= maintenant - 60_000]
            v_minute = float(m.valeur[minute].sum())
            moyenne = total / max(1.0, fenetre / 60)
            rapport = v_minute / moyenne if moyenne else 0
            detail = f"{len(minute)} liquidations"
            if fenetre > 60 and moyenne:
                detail += f" · {rapport:.1f} × la moyenne".replace(".", ",")
            self.carte_rythme.montrer(dollars(v_minute), detail)
            self.equilibre.set(v_longs / total if total else 0.5)
        self._liste_cryptos(maintenant - fenetre * 1000, plateformes, valeur_min)

    def _liste_cryptos(self, t0: float, plateformes, valeur_min: float) -> None:
        m = self.app.memoire
        indices = m.selection(t0, None, None, plateformes, valeur_min)
        if len(indices):
            codes = m.actif[indices]
            sommes = np.bincount(codes, weights=m.valeur[indices])
            ordre = [m.actifs[k] for k in np.argsort(-sommes)[:30] if sommes[k] > 0]
        else:
            ordre = []
        liste = [TOUTES] + ordre
        if self.actif and self.actif not in liste:
            liste.insert(1, self.actif)
        if liste != self._liste_actifs:
            self._liste_actifs = liste
            self.choix_actif.configure(values=liste)

    # ------------------------------------------------------------------
    def au_affichage(self) -> None:
        for toile in (self.riviere, self.fil, self.bilan):
            toile.visible = True
            toile.rafraichir()

    def au_masquage(self) -> None:
        for toile in (self.riviere, self.fil, self.bilan):
            toile.visible = False

    def reglages(self) -> dict:
        return {"direct": {"fenetre": self.fenetre, "taille": self.taille, "vue": self.vue, "actif": self.actif,
                           "plateformes": sorted(self.plateformes)}}


def _duree_courte(secondes: float) -> str:
    if secondes < 3600:
        return f"{secondes / 60:.0f} min"
    return f"{int(secondes // 3600)} h {int(secondes % 3600 // 60):02d}"
