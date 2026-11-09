"""
Page « Niveaux Hyperliquid » : les vrais prix de liquidation des positions
connues d'une crypto, lus compte par compte par le collecteur.

Rien n'est estimé : chaque bulle est une position réelle. La couverture dit
quelle part de l'open interest d'Hyperliquid les positions connues
représentent ; elle grandit à mesure que le collecteur lit des comptes.
"""

from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import ttk

import customtkinter as ctk
import numpy as np
from PIL import Image, ImageDraw, ImageTk

from .. import config
from ..hyperliquid import base
from ..niveaux import Carte as CarteNiveaux
from ..symboles import normaliser
from .composants import Carte, etiquette, segments
from .rendu import horloge
from .theme import COULEURS, LONG, SHORT, dollars, entier, il_y_a, pourcent, prix
from .vue_carte import VueCarte

PLAGES = {"±2 %": 0.02, "±5 %": 0.05, "±10 %": 0.10, "±25 %": 0.25, "±50 %": 0.50}
TAILLES = {"Toutes": 0.0, "10 k$": 1e4, "100 k$": 1e5, "1 M$": 1e6}
MARGES = {"Toutes": None, "Croisée": "cross", "Isolée": "isolated"}
FRAICHEURS = {"< 15 min": 900_000, "< 1 h": 3_600_000, "< 24 h": 86_400_000, "Toutes": None}
COLONNES = [("cote", "Côté", 70), ("valeur", "Valeur", 86), ("liquidation", "Liquidation", 92),
            ("distance", "Distance", 72), ("levier", "Levier", 54), ("marge", "Marge", 66),
            ("entree", "Entrée", 88), ("compte", "Compte", 104), ("lue", "Lue", 80)]
EXPLORATEUR = "https://app.hyperliquid.xyz/explorer/address/"


class PageNiveaux(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        r = app.reglages.get("niveaux", {})
        self.coin = r.get("coin", "BTC")
        self.plage = r.get("plage") if r.get("plage") in PLAGES else "±10 %"
        self.taille = r.get("taille") if r.get("taille") in TAILLES else "Toutes"
        self.marge = r.get("marge") if r.get("marge") in MARGES else "Toutes"
        self.fraicheur = r.get("fraicheur") if r.get("fraicheur") in FRAICHEURS else "Toutes"
        self.tri = ("valeur", True)
        self._positions = None              # positions brutes lues dans la base
        self._marches = None
        self._carte: CarteNiveaux | None = None
        self._prix_utilise = None
        self._file = queue.Queue()
        self._lecture_en_cours = False
        self._derniere_lecture = 0.0
        self._liste_coins: list[str] = []
        self._stats = None
        self._lignes: dict[str, int] = {}
        self._images = {nom: _point(couleur) for nom, couleur in (("long", LONG), ("short", SHORT))}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._barre_filtres()
        self._cartes()
        centre = ctk.CTkFrame(self, fg_color="transparent")
        centre.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        centre.grid_columnconfigure(0, weight=1)
        centre.grid_rowconfigure(0, weight=1)
        cadre = ctk.CTkFrame(centre, fg_color=COULEURS["toile"], corner_radius=10)
        cadre.grid(row=0, column=0, sticky="nsew")
        self.vue = VueCarte(cadre, quand_selection=self._selection_carte)
        self.vue.pack(fill="both", expand=True, padx=6, pady=6)
        self._tableau(centre)
        self.statut = ctk.CTkLabel(self, text="", anchor="w", font=ctk.CTkFont(size=11),
                                   text_color=COULEURS["texte_doux"])
        self.statut.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self.after(300, self._pomper)

    # ------------------------------------------------------------------
    def _barre_filtres(self) -> None:
        barre = ctk.CTkFrame(self, fg_color="transparent")
        barre.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(barre, text="Niveaux de liquidation réels", font=ctk.CTkFont(size=20, weight="bold")
                     ).pack(side="left", padx=(2, 6))
        ctk.CTkLabel(barre, text="Hyperliquid", font=ctk.CTkFont(size=13), text_color=COULEURS["texte_doux"]
                     ).pack(side="left", padx=(0, 18))
        etiquette(barre, "Crypto").pack(side="left", padx=(0, 6))
        self.choix_coin = ctk.CTkComboBox(barre, values=[self.coin], width=110, command=self._coin_choisi,
                                          font=ctk.CTkFont(size=12))
        self.choix_coin.set(self.coin)
        self.choix_coin.bind("<Return>", lambda e: self._coin_choisi(self.choix_coin.get()))
        self.choix_coin.pack(side="left", padx=(0, 14))
        for titre, valeurs, attribut in (("Plage", PLAGES, "plage"), ("Taille", TAILLES, "taille"),
                                         ("Marge", MARGES, "marge"), ("Lues", FRAICHEURS, "fraicheur")):
            etiquette(barre, titre).pack(side="left", padx=(0, 6))
            segments(barre, list(valeurs), getattr(self, attribut),
                     lambda v, a=attribut: self._filtre(a, v)).pack(side="left", padx=(0, 12))

    def _cartes(self) -> None:
        rangee = ctk.CTkFrame(self, fg_color="transparent")
        rangee.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        for k in range(6):
            rangee.grid_columnconfigure(k, weight=1, uniform="cartes")
        self.carte_prix = Carte(rangee, "Prix")
        self.carte_positions = Carte(rangee, "Positions connues")
        self.carte_couverture = Carte(rangee, "Part de l'open interest connue")
        self.carte_bas = Carte(rangee, "Si le prix baisse de 5 %", LONG)
        self.carte_haut = Carte(rangee, "Si le prix monte de 5 %", SHORT)
        self.carte_mur = Carte(rangee, "Le plus gros niveau")
        for k, carte in enumerate((self.carte_prix, self.carte_positions, self.carte_couverture, self.carte_bas,
                                   self.carte_haut, self.carte_mur)):
            carte.grid(row=0, column=k, sticky="nsew", padx=(0 if k == 0 else 4, 0 if k == 5 else 4))

    def _tableau(self, parent) -> None:
        cadre = ctk.CTkFrame(parent, fg_color=COULEURS["panneau"], corner_radius=10, width=740)
        cadre.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        cadre.grid_propagate(False)
        cadre.grid_columnconfigure(0, weight=1)
        cadre.grid_rowconfigure(1, weight=1)
        self.titre_tableau = ctk.CTkLabel(cadre, text="Positions", font=ctk.CTkFont(size=14, weight="bold"),
                                          anchor="w")
        self.titre_tableau.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 4))
        interieur = tk.Frame(cadre, bg=COULEURS["panneau"])
        interieur.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 4))
        interieur.grid_columnconfigure(0, weight=1)
        interieur.grid_rowconfigure(0, weight=1)
        self.arbre = ttk.Treeview(interieur, columns=[c for c, _, _ in COLONNES], show="tree headings",
                                  selectmode="browse")
        self.arbre.column("#0", width=26, stretch=False)
        for cle, titre, largeur in COLONNES:
            self.arbre.heading(cle, text=titre, command=lambda c=cle: self._trier(c))
            self.arbre.column(cle, width=largeur, anchor="e" if cle not in ("cote", "marge", "compte") else "w",
                              stretch=cle == "compte")
        defilement = ttk.Scrollbar(interieur, orient="vertical", command=self.arbre.yview)
        self.arbre.configure(yscrollcommand=defilement.set)
        self.arbre.grid(row=0, column=0, sticky="nsew")
        defilement.grid(row=0, column=1, sticky="ns")
        self.arbre.bind("<<TreeviewSelect>>", self._selection_tableau)
        self.arbre.bind("<Double-1>", self._ouvrir_compte)
        ctk.CTkLabel(cadre, text="Double-clic : ouvrir le compte dans l'explorateur Hyperliquid. "
                                 "Un clic sur un titre de colonne trie.",
                     font=ctk.CTkFont(size=11), text_color=COULEURS["texte_pale"], anchor="w"
                     ).grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 8))

    # ------------------------------------------------------------------
    # FILTRES
    # ------------------------------------------------------------------
    def _coin_choisi(self, valeur: str) -> None:
        valeur = (valeur or "").strip()
        if not valeur:
            return
        connus = {c.lower(): c for c in self._liste_coins}
        self.coin = connus.get(valeur.lower(), valeur if valeur.startswith("k") else valeur.upper())
        self.choix_coin.set(self.coin)
        self.vue.selection = None
        self._positions = None
        self._derniere_lecture = 0.0
        self.vue.montrer(None, PLAGES[self.plage])
        self.vue.message = f"Lecture des positions {self.coin}…"
        self._lire()

    def _filtre(self, attribut: str, valeur: str) -> None:
        setattr(self, attribut, valeur)
        self._recalculer()

    def _trier(self, colonne: str) -> None:
        cle, decroissant = self.tri
        self.tri = (colonne, not decroissant if cle == colonne else colonne in ("valeur", "levier"))
        self._remplir_tableau()

    # ------------------------------------------------------------------
    # LECTURE DE LA BASE (dans un thread)
    # ------------------------------------------------------------------
    def _lire(self) -> None:
        if self._lecture_en_cours:
            return
        if not os.path.exists(config.BASE_HYPERLIQUID):
            self.vue.message = ("Aucune position lue pour l'instant : démarre la collecte (page Collecteur), "
                                "avec la lecture des positions Hyperliquid.")
            self.vue.montrer(None, PLAGES[self.plage])
            return
        self._lecture_en_cours = True
        coin = self.coin
        avec_liste = time.monotonic() - getattr(self, "_liste_lue", 0) > 20

        def travail():
            try:
                cx = base.ouvrir(lecture_seule=True)
                try:
                    positions = base.lire_positions(coin, cx)
                    marches = base.lire_marches(cx)
                    resume = base.resume_actifs(cx) if avec_liste else None
                    stats = base.statistiques(cx)
                finally:
                    cx.close()
                self._file.put(("ok", coin, positions, marches, resume, stats))
            except Exception as erreur:                 # noqa: BLE001
                self._file.put(("erreur", coin, erreur, None, None, None))

        threading.Thread(target=travail, daemon=True).start()

    def _pomper(self) -> None:
        try:
            while True:
                genre, coin, a, b, resume, stats = self._file.get_nowait()
                self._lecture_en_cours = False
                if genre == "erreur":
                    self.statut.configure(text=f"Lecture de la base impossible : {a}")
                    continue
                if resume is not None:
                    self._liste_lue = time.monotonic()
                    liste = list(resume["actif"])[:80]
                    if self.coin not in liste:
                        liste.insert(0, self.coin)
                    if liste != self._liste_coins:
                        self._liste_coins = liste
                        self.choix_coin.configure(values=liste)
                if coin != self.coin:
                    continue
                self._positions, self._marches, self._stats = a, b.set_index("actif"), stats
                self._recalculer()
        except queue.Empty:
            pass
        if self.app.page_active == "niveaux" and time.monotonic() - self._derniere_lecture > 5:
            self._derniere_lecture = time.monotonic()
            self._lire()
        self.after(300, self._pomper)

    # ------------------------------------------------------------------
    # CALCULS
    # ------------------------------------------------------------------
    def _prix(self) -> tuple[float | None, float | None, float]:
        """Prix courant (collecteur en marche) ou dernier prix de marque enregistré ; open interest."""
        oi, prix_base, maj = None, None, 0.0
        if self._marches is not None and self.coin in self._marches.index:
            ligne = self._marches.loc[self.coin]
            prix_base, oi, maj = float(ligne["prix"]), float(ligne["open_interest"]), float(ligne["maj"])
        mids = ((self.app.etat or {}).get("hyperliquid") or {}).get("mids") or {}
        if self.coin in mids:
            return float(mids[self.coin]), oi, horloge()
        return prix_base, oi, maj

    def _recalculer(self) -> None:
        if self._positions is None:
            return
        prix_courant, oi, maj = self._prix()
        if self._positions.empty or not prix_courant:
            self._carte = None
            self.vue.message = f"Aucune position connue sur {self.coin} pour l'instant."
            self.vue.montrer(None, PLAGES[self.plage])
            self._remplir_tableau()
            self._chiffres(None, prix_courant, maj)
            return
        self._prix_utilise = prix_courant
        carte = CarteNiveaux(self._positions, prix_courant, oi)
        self._carte = carte.filtrer(TAILLES[self.taille], MARGES[self.marge], FRAICHEURS[self.fraicheur])
        self.vue.recentes = self._liquidations_recentes()
        self.vue.montrer(self._carte, PLAGES[self.plage])
        self._remplir_tableau()
        self._chiffres(self._carte, prix_courant, maj)

    def _liquidations_recentes(self) -> list[tuple]:
        m = self.app.memoire
        actif, paquet = normaliser(self.coin)
        indices = m.selection(horloge() - 600_000, None, actif)
        return [(float(m.t[i]), float(m.prix[i]) * paquet, bool(m.short[i]), float(m.valeur[i]),
                 m.cle_plateforme(int(i))) for i in indices]

    def _chiffres(self, carte: CarteNiveaux | None, prix_courant, maj: float) -> None:
        if prix_courant:
            source = "en direct" if (self.app.etat and horloge() - maj < 5000) else f"marque, {il_y_a(maj)}"
            self.carte_prix.montrer(f"{prix(prix_courant)} $", f"{self.coin} · {source}")
        else:
            self.carte_prix.montrer("—", self.coin)
        if carte is None:
            for c in (self.carte_positions, self.carte_couverture, self.carte_bas, self.carte_haut, self.carte_mur):
                c.montrer("—", "")
            self._statut()
            return
        t = carte.totaux()
        self.carte_positions.montrer(entier(t["positions"]),
                                     f"{t['longs']} longs ({dollars(t['valeur_longs'])}) · {t['shorts']} shorts "
                                     f"({dollars(t['valeur_shorts'])})")
        if t["couverture_longs"] is not None:
            self.carte_couverture.montrer(f"{pourcent(t['couverture_longs'])} · {pourcent(t['couverture_shorts'])}",
                                          f"longs · shorts, open interest {dollars(t['oi_valeur'])}")
        else:
            self.carte_couverture.montrer("—", "open interest inconnu")
        ecart = min(0.05, PLAGES[self.plage])
        texte_ecart = pourcent(ecart).replace(" %", "")
        self.carte_bas.titre.configure(text=f"Si le prix baisse de {texte_ecart} %")
        self.carte_haut.titre.configure(text=f"Si le prix monte de {texte_ecart} %")
        v_bas, n_bas = carte.cumul(carte.prix * (1 - ecart))
        v_haut, n_haut = carte.cumul(carte.prix * (1 + ecart))
        self.carte_bas.montrer(dollars(v_bas), f"de longs liquidés · {n_bas} positions · à {prix(carte.prix * (1 - ecart))} $")
        self.carte_haut.montrer(dollars(v_haut), f"de shorts liquidés · {n_haut} positions · à {prix(carte.prix * (1 + ecart))} $")
        bords, par_cote = carte.tranches(PLAGES[self.plage], 40)
        meilleur = None
        for cote, (valeurs, _) in par_cote.items():
            k = int(np.argmax(valeurs))
            if valeurs[k] > 0 and (meilleur is None or valeurs[k] > meilleur[0]):
                meilleur = (float(valeurs[k]), cote, bords[k], bords[k + 1])
        if meilleur:
            v, cote, a, b = meilleur
            milieu = (a + b) / 2
            self.carte_mur.montrer(f"{dollars(v)} de {cote}",
                                   f"vers {prix(milieu)} $ ({pourcent(milieu / carte.prix - 1, signe=True)})")
        else:
            self.carte_mur.montrer("—", "")
        self._statut()

    def _statut(self) -> None:
        s = self._stats
        morceaux = []
        if s:
            morceaux.append(f"Base : {entier(s['adresses'])} comptes connus, {entier(s['lues'])} lus, "
                            f"{entier(s['positions'])} positions")
            if s["derniere_lecture"]:
                morceaux.append(f"dernière lecture {il_y_a(s['derniere_lecture'])}")
        etat = self.app.etat
        if not etat:
            morceaux.append("collecte arrêtée : les positions restent telles qu'à leur dernière lecture")
        elif not etat.get("positions", True):
            morceaux.append("la collecte en cours ne lit pas les positions (option de la page Collecteur)")
        self.statut.configure(text=" · ".join(morceaux))

    # ------------------------------------------------------------------
    # TABLEAU
    # ------------------------------------------------------------------
    def _remplir_tableau(self) -> None:
        defile = self.arbre.yview()[0]
        self.arbre.delete(*self.arbre.get_children())
        self._lignes = {}
        carte = self._carte
        if carte is None:
            self.titre_tableau.configure(text="Positions")
            return
        p = carte.positions
        p = p[p["prix_liquidation"].isna() | (p["distance"].abs() <= PLAGES[self.plage])]
        colonne, decroissant = self.tri
        cles = {"valeur": "notionnel", "liquidation": "prix_liquidation", "distance": "distance", "levier": "levier",
                "entree": "prix_entree", "lue": "lue_le", "cote": "long", "marge": "marge", "compte": "adresse"}
        trie = p.assign(_d=p["distance"].abs()).sort_values("_d" if colonne == "distance" else cles[colonne],
                                                           ascending=not decroissant, na_position="last")
        self.titre_tableau.configure(text=f"Positions {self.coin} dans la plage — {len(trie)}"
                                     + (" (les 400 premières)" if len(trie) > 400 else ""))
        maintenant = horloge()
        for _, x in trie.head(400).iterrows():
            long = bool(x["long"])
            liq = x["prix_liquidation"]
            iid = self.arbre.insert("", "end", image=self._images["long" if long else "short"], values=(
                "long" if long else "short", dollars(float(x["notionnel"])),
                prix(float(liq)) if liq == liq else "aucune",
                pourcent(float(x["distance"]), signe=True) if liq == liq else "",
                f"{float(x['levier']):.0f}", "isolée" if x["marge"] == "isolated" else "croisée",
                prix(float(x["prix_entree"])), f"{x['adresse'][:8]}…{x['adresse'][-4:]}",
                il_y_a(float(x["lue_le"]), maintenant).replace("il y a ", "")))
            self._lignes[iid] = x["adresse"]            # une ligne = le compte (une position par crypto)
        self.arbre.yview_moveto(defile)                 # la liste reste où on l'avait laissée
        if self.vue.selection is not None:
            for iid, adresse in self._lignes.items():
                if adresse == self.vue.selection:
                    self.arbre.selection_set(iid)
                    break

    def _selection_tableau(self, _evenement=None) -> None:
        choix = self.arbre.selection()
        if not choix:
            return
        adresse = self._lignes.get(choix[0])
        if adresse is not None and adresse != self.vue.selection:
            self.vue.selection = adresse
            self.vue.rafraichir()

    def _selection_carte(self, adresse) -> None:
        if adresse is None:
            self.arbre.selection_remove(*self.arbre.selection())
            return
        for iid, a in self._lignes.items():
            if a == adresse:
                self.arbre.selection_set(iid)
                self.arbre.see(iid)
                return

    def _ouvrir_compte(self, _evenement=None) -> None:
        choix = self.arbre.selection()
        adresse = self._lignes.get(choix[0]) if choix else None
        if adresse:
            webbrowser.open(EXPLORATEUR + adresse)

    # ------------------------------------------------------------------
    def tic(self) -> None:
        """Chaque seconde : nouveau prix, nouvelles liquidations récentes."""
        if self._carte is None or self._positions is None:
            return
        prix_courant, _, _ = self._prix()
        if prix_courant and self._prix_utilise and abs(prix_courant / self._prix_utilise - 1) > 0.0005:
            self._recalculer()
        else:
            self.vue.recentes = self._liquidations_recentes()
            self.vue.rafraichir()

    def au_affichage(self) -> None:
        self.vue.visible = True
        self._derniere_lecture = 0.0
        self.vue.rafraichir()

    def au_masquage(self) -> None:
        self.vue.visible = False

    def reglages(self) -> dict:
        return {"niveaux": {"coin": self.coin, "plage": self.plage, "taille": self.taille, "marge": self.marge,
                            "fraicheur": self.fraicheur}}


def _point(couleur: str) -> ImageTk.PhotoImage:
    image = Image.new("RGBA", (14, 14), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((3, 3, 11, 11), fill=couleur)
    return ImageTk.PhotoImage(image)
