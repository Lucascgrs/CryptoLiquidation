"""
Page « Collecteur » : démarrer et arrêter la collecte, choisir les plateformes,
voir l'état de chaque connexion et la lecture des positions Hyperliquid.
"""

from __future__ import annotations

import os
import time

import customtkinter as ctk

from .. import config
from .composants import pastille
from .theme import COULEURS, dollars, duree, entier, il_y_a


class PageCollecteur(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self._journal_lu = 0.0

        tete = ctk.CTkFrame(self, fg_color=COULEURS["panneau"], corner_radius=10)
        tete.grid(row=0, column=0, sticky="ew")
        tete.grid_columnconfigure(1, weight=1)
        self.bouton = ctk.CTkButton(tete, text="Démarrer la collecte", width=220, height=46, corner_radius=10,
                                    font=ctk.CTkFont(size=15, weight="bold"), command=self._basculer)
        self.bouton.grid(row=0, column=0, rowspan=2, padx=18, pady=16)
        self.titre = ctk.CTkLabel(tete, text="", font=ctk.CTkFont(size=18, weight="bold"), anchor="w")
        self.titre.grid(row=0, column=1, sticky="ew", pady=(16, 0))
        self.sous_titre = ctk.CTkLabel(tete, text="", font=ctk.CTkFont(size=12), text_color=COULEURS["texte_doux"],
                                       anchor="w", justify="left")
        self.sous_titre.grid(row=1, column=1, sticky="ew", pady=(0, 16))

        options = ctk.CTkFrame(self, fg_color=COULEURS["panneau"], corner_radius=10)
        options.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ctk.CTkLabel(options, text="Plateformes collectées", font=ctk.CTkFont(size=13, weight="bold")
                     ).grid(row=0, column=0, sticky="w", padx=18, pady=(12, 4))
        ctk.CTkLabel(options, text="(les changements comptent au prochain démarrage)", font=ctk.CTkFont(size=11),
                     text_color=COULEURS["texte_pale"]).grid(row=0, column=1, columnspan=4, sticky="w", pady=(12, 4))
        self.cases = {}
        for k, p in enumerate(config.PLATEFORMES.values()):
            variable = ctk.BooleanVar(value=app.reglages_collecte["sources"].get(p.cle, True))
            case = ctk.CTkCheckBox(options, text=p.nom, variable=variable, command=self._enregistrer,
                                   font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18)
            case.grid(row=1 + k // 5, column=k % 5, sticky="w", padx=18, pady=3)
            self.cases[p.cle] = variable
        self.lire_positions = ctk.BooleanVar(value=app.reglages_collecte.get("positions_hyperliquid", True))
        self.arreter_en_fermant = ctk.BooleanVar(value=app.reglages_collecte.get("arreter_en_fermant", True))
        self.demarrer_a_l_ouverture = ctk.BooleanVar(value=app.reglages_collecte.get("demarrer_a_l_ouverture", False))
        for k, (variable, texte) in enumerate((
                (self.lire_positions, "Lire les positions Hyperliquid (page Niveaux)"),
                (self.arreter_en_fermant, "Arrêter la collecte en fermant l'interface"),
                (self.demarrer_a_l_ouverture, "Démarrer la collecte à l'ouverture de l'interface"))):
            ctk.CTkCheckBox(options, text=texte, variable=variable, command=self._enregistrer,
                            font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18
                            ).grid(row=3 + k, column=0, columnspan=5, sticky="w", padx=18,
                                   pady=(10 if k == 0 else 3, 12 if k == 2 else 3))

        self.grille = ctk.CTkFrame(self, fg_color="transparent")
        self.grille.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        for k in range(5):
            self.grille.grid_columnconfigure(k, weight=1, uniform="sources")
        self.fiches = {}
        for k, p in enumerate(config.PLATEFORMES.values()):
            self.fiches[p.cle] = Fiche(self.grille, p)
            self.fiches[p.cle].grid(row=k // 5, column=k % 5, sticky="nsew", padx=4, pady=4)

        bas = ctk.CTkFrame(self, fg_color="transparent")
        bas.grid(row=3, column=0, sticky="nsew", pady=(6, 0))
        bas.grid_columnconfigure(1, weight=1)
        bas.grid_rowconfigure(0, weight=1)
        self.hyperliquid = ctk.CTkLabel(bas, text="", justify="left", anchor="nw", font=ctk.CTkFont(size=12),
                                        fg_color=COULEURS["panneau"], corner_radius=10, width=420)
        self.hyperliquid.grid(row=0, column=0, sticky="nsew", padx=(4, 8))
        self.journal = ctk.CTkTextbox(bas, font=ctk.CTkFont(family="Consolas", size=11), fg_color=COULEURS["panneau"],
                                      corner_radius=10, wrap="none")
        self.journal.grid(row=0, column=1, sticky="nsew", padx=(0, 4))
        self.journal.configure(state="disabled")

    # ------------------------------------------------------------------
    def _enregistrer(self) -> None:
        r = self.app.reglages_collecte
        r["sources"] = {cle: bool(v.get()) for cle, v in self.cases.items()}
        r["positions_hyperliquid"] = bool(self.lire_positions.get())
        r["arreter_en_fermant"] = bool(self.arreter_en_fermant.get())
        r["demarrer_a_l_ouverture"] = bool(self.demarrer_a_l_ouverture.get())
        self.app.enregistrer_reglages_collecte()

    def _basculer(self) -> None:
        if self.app.etat:
            self.app.gestion.arreter()
            self.app.statut("Arrêt de la collecte demandé…")
        else:
            if not any(v.get() for v in self.cases.values()):
                self.app.statut("Coche au moins une plateforme.", erreur=True)
                return
            self.app.demarrer_collecte()
        self.tic()

    # ------------------------------------------------------------------
    def tic(self) -> None:
        etat = self.app.etat
        gestion = self.app.gestion
        if etat:
            depuis = (time.time() * 1000 - etat["debut"]) / 1000
            self.titre.configure(text=f"●  Collecte en marche depuis {duree(depuis)}", text_color=COULEURS["bon"])
            self.sous_titre.configure(text=f"{entier(etat['registre']['ecrites'])} liquidations enregistrées "
                                           f"dans donnees/liquidations/ · processus {etat['pid']}")
            self.bouton.configure(text="Arrêter la collecte", fg_color=COULEURS["grave"], hover_color="#a83232")
        elif gestion.demarrage_en_cours():
            self.titre.configure(text="Démarrage…", text_color=COULEURS["attention"])
            self.sous_titre.configure(text="Lecture des listes de contrats, puis connexion aux plateformes.")
            self.bouton.configure(text="Démarrage…", fg_color=COULEURS["neutre"], hover_color=COULEURS["neutre"])
        else:
            echec = gestion.a_echoue()
            self.titre.configure(text="Le collecteur s'est arrêté au démarrage" if echec else "Collecte arrêtée",
                                 text_color=COULEURS["grave"] if echec else COULEURS["texte"])
            self.sous_titre.configure(text="Voir le journal ci-dessous." if echec else
                                      "Rien n'est collecté tant que la collecte est arrêtée. Elle peut aussi tourner "
                                      "sans l'interface : python main.py collecter")
            self.bouton.configure(text="Démarrer la collecte", fg_color=COULEURS["accent"],
                                  hover_color=COULEURS["accent_clair"])
        sources = (etat or {}).get("sources", {})
        for cle, fiche in self.fiches.items():
            fiche.montrer(sources.get(cle), bool(etat), self.cases[cle].get())
        self._hyperliquid(etat)
        if time.monotonic() - self._journal_lu > 2:
            self._journal_lu = time.monotonic()
            self._lire_journal()

    def _hyperliquid(self, etat) -> None:
        hl = (etat or {}).get("hyperliquid")
        if not hl:
            self.hyperliquid.configure(text="  Hyperliquid\n\n  La lecture des positions et la reconnaissance des\n"
                                            "  liquidations démarrent avec la collecte.")
            return
        s, d = hl.get("scanner") or {}, hl.get("detecteur") or {}
        lignes = ["  Hyperliquid", ""]
        if s:
            lignes += [f"  Comptes connus : {entier(s.get('comptes', 0))}",
                       f"  … lus au moins une fois : {entier(s.get('lus', 0))}",
                       f"  Positions suivies : {entier(s.get('positions', 0))} "
                       f"(sur {entier(s.get('avec_positions', 0))} comptes)",
                       f"  Lectures : {s.get('lectures_minute', 0)} par minute",
                       f"  En attente : {entier(s.get('file_sale', 0))} vus en train de trader, "
                       f"{entier(s.get('file_nouveau', 0))} du classement jamais lus", ""]
        else:
            lignes += ["  Lecture des positions désactivée.", ""]
        preuves = {k[7:]: v for k, v in d.items() if k.startswith("preuve_")}
        lignes += [f"  Liquidations reconnues : {d.get('liquidations', 0)}",
                   "    " + (", ".join(f"{v} par {k}" for k, v in preuves.items()) or "aucune pour l'instant"),
                   f"  Vérifications : {d.get('verifications', 0)} ({d.get('fausses_pistes', 0)} n'étaient pas "
                   f"des liquidations), {d.get('en_attente', 0)} en attente",
                   f"  Comptes suivis en direct : {d.get('suivis', 0)} (coffres HLP et teneurs de marché)", "",
                   f"  API : {hl.get('poids_minute', 0)} poids par minute sur 1 200 permis"
                   + (f", {hl['refus']} refus" if hl.get("refus") else "")]
        self.hyperliquid.configure(text="\n".join(lignes))

    def _lire_journal(self) -> None:
        try:
            with open(config.FICHIER_JOURNAL, "rb") as f:
                f.seek(max(0, os.path.getsize(config.FICHIER_JOURNAL) - 30_000))
                lignes = f.read().decode("utf-8", errors="replace").splitlines()[-150:]
        except OSError:
            lignes = ["(pas encore de journal)"]
        texte = "\n".join(lignes)
        if texte != getattr(self, "_texte_journal", None):
            self._texte_journal = texte
            self.journal.configure(state="normal")
            self.journal.delete("1.0", "end")
            self.journal.insert("1.0", texte)
            self.journal.see("end")
            self.journal.configure(state="disabled")

    def au_affichage(self) -> None:
        self._journal_lu = 0.0
        self.tic()


class Fiche(ctk.CTkFrame):
    """L'état d'une plateforme : connexion, liquidations reçues, ce qu'elle publie."""

    def __init__(self, parent, plateforme: config.Plateforme):
        super().__init__(parent, fg_color=COULEURS["carte"], corner_radius=10)
        self.plateforme = plateforme
        entete = ctk.CTkFrame(self, fg_color="transparent")
        entete.pack(fill="x", padx=12, pady=(10, 0))
        ctk.CTkLabel(entete, text=plateforme.nom, image=pastille(plateforme.couleur), compound="left",
                     font=ctk.CTkFont(size=14, weight="bold"), anchor="w").pack(side="left")
        self.etat = ctk.CTkLabel(entete, text="", font=ctk.CTkFont(size=11), anchor="e")
        self.etat.pack(side="right")
        self.chiffres = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=12), anchor="w", justify="left")
        self.chiffres.pack(fill="x", padx=12, pady=(4, 0))
        ctk.CTkLabel(self, text=("Publication complète" if plateforme.complete else "Publication partielle"),
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=COULEURS["texte_doux"] if plateforme.complete else COULEURS["attention"],
                     anchor="w").pack(fill="x", padx=12, pady=(6, 0))
        self.note = ctk.CTkLabel(self, text=plateforme.couverture, font=ctk.CTkFont(size=11),
                                 text_color=COULEURS["texte_doux"], anchor="w", justify="left", wraplength=240)
        self.note.pack(fill="x", padx=12, pady=(0, 10))
        self.bind("<Configure>", lambda e: self.note.configure(wraplength=max(120, e.width - 30)))

    def montrer(self, e: dict | None, en_marche: bool, cochee: bool) -> None:
        if not en_marche or e is None:
            self.etat.configure(text="désactivée" if not cochee else "à l'arrêt",
                                text_color=COULEURS["texte_pale"])
            self.chiffres.configure(text="—")
            return
        if e["prevues"] and e["connexions"] == e["prevues"]:
            self.etat.configure(text="●  connectée", text_color=COULEURS["bon"])
        elif e["connexions"]:
            self.etat.configure(text=f"●  {e['connexions']}/{e['prevues']} connexions", text_color=COULEURS["attention"])
        else:
            self.etat.configure(text="●  connexion…" if not e["erreur"] else "●  reconnexion…",
                                text_color=COULEURS["attention"] if not e["erreur"] else COULEURS["grave"])
        texte = f"{entier(e['liquidations'])} liquidations · {dollars(e['valeur'])}"
        if e["derniere"]:
            texte += f"\ndernière {il_y_a(e['derniere'])}"
        if e["erreur"] and time.time() * 1000 - e["heure_erreur"] < 600_000:
            texte += f"\n{e['erreur'][:90]}"
        self.chiffres.configure(text=texte)
