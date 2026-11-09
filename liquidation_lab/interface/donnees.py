"""
Les données vues par l'interface : les liquidations en mémoire (tableaux
numpy, pour filtrer vite des centaines de milliers de lignes), relues depuis le
registre puis complétées au fil de l'eau, et l'historique des prix des cryptos
affichées.
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pandas as pd
import requests

from .. import config, stockage
from ..symboles import normaliser

CODES_PLATEFORMES = {cle: i for i, cle in enumerate(config.ORDRE_PLATEFORMES)}
HEURES_EN_MEMOIRE = 24


class Memoire:
    """Les liquidations des dernières 24 heures, en colonnes."""

    def __init__(self):
        self.n = 0
        self._cap = 0
        self.t = np.zeros(0, np.int64)
        self.plateforme = np.zeros(0, np.int8)
        self.actif = np.zeros(0, np.int32)
        self.short = np.zeros(0, bool)
        self.prix = np.zeros(0, np.float64)
        self.valeur = np.zeros(0, np.float64)
        self.marches: list[str] = []
        self.details: list[str] = []
        self.actifs: list[str] = []
        self._codes_actifs: dict[str, int] = {}
        self.verrou = threading.Lock()
        self.version = 0                            # change à chaque ajout ou purge

    def _code(self, actif: str) -> int:
        code = self._codes_actifs.get(actif)
        if code is None:
            code = self._codes_actifs[actif] = len(self.actifs)
            self.actifs.append(actif)
        return code

    def code_actif(self, actif: str) -> int | None:
        return self._codes_actifs.get(actif)

    def _agrandir(self, besoin: int) -> None:
        if besoin <= self._cap:
            return
        cap = max(besoin, int(self._cap * 1.6) + 4096)
        for nom in ("t", "plateforme", "actif", "short", "prix", "valeur"):
            ancien = getattr(self, nom)
            nouveau = np.zeros(cap, ancien.dtype)
            nouveau[:self.n] = ancien[:self.n]
            setattr(self, nom, nouveau)
        self._cap = cap

    def ajouter(self, tableau: pd.DataFrame | None) -> range:
        """Ajoute les lignes du registre ; rend les indices des nouvelles lignes."""
        if tableau is None or tableau.empty:
            return range(self.n, self.n)
        tableau = tableau[tableau["plateforme"].isin(CODES_PLATEFORMES)]
        k = len(tableau)
        with self.verrou:
            debut = self.n
            self._agrandir(debut + k)
            fin = debut + k
            self.t[debut:fin] = tableau["t"].to_numpy(np.int64)
            self.plateforme[debut:fin] = tableau["plateforme"].map(CODES_PLATEFORMES).to_numpy(np.int8)
            self.actif[debut:fin] = [self._code(a) for a in tableau["actif"].astype(str)]
            self.short[debut:fin] = (tableau["cote"] == "short").to_numpy()
            self.prix[debut:fin] = tableau["prix"].to_numpy(np.float64)
            self.valeur[debut:fin] = tableau["valeur"].to_numpy(np.float64)
            self.marches.extend(tableau["marche"].astype(str))
            self.details.extend(tableau["detail"].astype(str))
            self.n = fin
            self.version += 1
        return range(debut, fin)

    def purger(self, avant_ms: int) -> None:
        """Oublie ce qui précède `avant_ms` (la mémoire ne garde que les dernières 24 heures)."""
        with self.verrou:
            garder = self.t[:self.n] >= avant_ms
            if garder.all():
                return
            k = int(garder.sum())
            for nom in ("t", "plateforme", "actif", "short", "prix", "valeur"):
                tableau = getattr(self, nom)
                tableau[:k] = tableau[:self.n][garder]
            self.marches = [m for m, g in zip(self.marches, garder) if g]
            self.details = [d for d, g in zip(self.details, garder) if g]
            self.n = k
            self.version += 1

    def selection(self, t0: float, t1: float | None = None, actif: str | None = None,
                  plateformes: set[str] | None = None, valeur_min: float = 0.0) -> np.ndarray:
        """Indices des liquidations qui passent les filtres."""
        n = self.n
        masque = self.t[:n] >= t0
        if t1 is not None:
            masque &= self.t[:n] <= t1
        if actif:
            code = self._codes_actifs.get(actif)
            if code is None:
                return np.zeros(0, np.int64)
            masque &= self.actif[:n] == code
        if plateformes is not None and len(plateformes) < len(CODES_PLATEFORMES):
            codes = [CODES_PLATEFORMES[p] for p in plateformes if p in CODES_PLATEFORMES]
            masque &= np.isin(self.plateforme[:n], codes)
        if valeur_min > 0:
            masque &= self.valeur[:n] >= valeur_min
        return np.flatnonzero(masque)

    def cle_plateforme(self, i: int) -> str:
        return config.ORDRE_PLATEFORMES[int(self.plateforme[i])]


class Lecteur:
    """Relit le registre au démarrage (dans un thread), puis suit les lignes ajoutées."""

    def __init__(self, memoire: Memoire):
        self.memoire = memoire
        self.suivi = stockage.Suivi()
        self.pret = False
        self.erreur = None

    def charger_en_fond(self, apres) -> None:
        def travail():
            try:
                debut = int(time.time() * 1000) - HEURES_EN_MEMOIRE * 3_600_000
                self.memoire.ajouter(self.suivi.charger(debut))
            except Exception as erreur:             # noqa: BLE001
                self.erreur = erreur
            self.pret = True
            apres()

        threading.Thread(target=travail, daemon=True).start()

    def sonder(self) -> range:
        if not self.pret:
            return range(0)
        try:
            return self.memoire.ajouter(self.suivi.nouvelles())
        except Exception as erreur:                 # noqa: BLE001
            self.erreur = erreur
            return range(0)


# ----------------------------------------------------------------------
# PRIX
# ----------------------------------------------------------------------
class Prix:
    """
    Historique du prix d'une crypto : bougies d'une minute à l'ouverture (API
    publique d'Hyperliquid, ou de Binance pour les cryptos absentes
    d'Hyperliquid), puis prix courants publiés par le collecteur chaque seconde.
    """

    def __init__(self):
        self.actif = None
        self.t = np.zeros(0)
        self.p = np.zeros(0)                        # clôture (ou prix courant)
        self.bas = np.zeros(0)                      # plus bas et plus haut de la minute :
        self.haut = np.zeros(0)                     # les liquidations ont souvent lieu dans les mèches
        self._verrou = threading.Lock()
        self._demande = None
        self.source = ""

    def suivre(self, actif: str, coins_hl: list[str], depuis_ms: int) -> None:
        if actif == self.actif:
            return
        self.actif = actif
        with self._verrou:
            self.t, self.p, self.bas, self.haut = (np.zeros(0) for _ in range(4))
        jeton = object()
        self._demande = jeton
        threading.Thread(target=self._historique, args=(actif, coins_hl, depuis_ms, jeton), daemon=True).start()

    def _historique(self, actif: str, coins_hl: list[str], depuis_ms: int, jeton) -> None:
        coin = coin_hyperliquid(actif, coins_hl)
        maintenant = int(time.time() * 1000)
        lignes, source = [], ""
        try:
            if coin:
                bougies = requests.post("https://api.hyperliquid.xyz/info", timeout=15, json={
                    "type": "candleSnapshot",
                    "req": {"coin": coin, "interval": "1m", "startTime": depuis_ms, "endTime": maintenant}}).json()
                paquet = normaliser(coin)[1]
                lignes = [(b["t"] + 30_000, float(b["c"]) / paquet, float(b["l"]) / paquet, float(b["h"]) / paquet)
                          for b in bougies]
                source = "Hyperliquid"
            else:
                reponse = requests.get("https://fapi.binance.com/fapi/v1/klines", timeout=15, params={
                    "symbol": f"{actif}USDT", "interval": "1m", "startTime": depuis_ms, "limit": 1500})
                if reponse.ok:
                    lignes = [(b[0] + 30_000, float(b[4]), float(b[3]), float(b[2])) for b in reponse.json()]
                    source = "Binance"
        except Exception:                           # noqa: BLE001
            pass
        if not lignes:
            return
        t, p, bas, haut = (np.array(colonne, float) for colonne in zip(*lignes))
        with self._verrou:
            if self._demande is jeton:
                apres = self.t > t[-1]              # prix courants arrivés pendant la lecture
                self.t = np.concatenate([t, self.t[apres]])
                self.p = np.concatenate([p, self.p[apres]])
                self.bas = np.concatenate([bas, self.bas[apres]])
                self.haut = np.concatenate([haut, self.haut[apres]])
                self.source = source

    def ajouter(self, t_ms: float, valeur: float) -> None:
        if not valeur or valeur != valeur:
            return
        with self._verrou:
            if len(self.t) and t_ms <= self.t[-1]:
                return
            self.t = np.append(self.t, t_ms)
            self.p = np.append(self.p, valeur)
            self.bas = np.append(self.bas, valeur)
            self.haut = np.append(self.haut, valeur)
            if len(self.t) > 200_000:
                self.t, self.p = self.t[-150_000:], self.p[-150_000:]
                self.bas, self.haut = self.bas[-150_000:], self.haut[-150_000:]

    def serie(self, t0: float, avec_meches: bool = False):
        with self._verrou:
            i = max(0, int(np.searchsorted(self.t, t0)) - 1)
            if avec_meches:
                return self.t[i:].copy(), self.p[i:].copy(), self.bas[i:].copy(), self.haut[i:].copy()
            return self.t[i:].copy(), self.p[i:].copy()


def coin_hyperliquid(actif: str, coins_hl: list[str]) -> str | None:
    """Le nom Hyperliquid d'un actif : BTC -> BTC, PEPE -> kPEPE."""
    if actif in coins_hl:
        return actif
    if f"k{actif}" in coins_hl:
        return f"k{actif}"
    return None
