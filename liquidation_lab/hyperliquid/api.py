"""
Appels à l'API publique `info` d'Hyperliquid, dans la limite de débit.

Hyperliquid accorde 1200 « poids » par minute et par adresse IP. Une lecture
de compte (clearinghouseState) pèse 2, la plupart des autres requêtes 20, et
les historiques de transactions (userFillsByTime) 20 plus 1 par tranche de 20
lignes rendues. Le budget est réparti entre trois seaux, pour que la lecture
des positions ne prive jamais les vérifications de liquidations :
- `lectures` : les comptes (page « Niveaux ») ;
- `verifications` : la confirmation des liquidations ;
- `divers` : le reste (liste des cryptos, coffres HLP).
"""

from __future__ import annotations

import asyncio
import json
import re
import time

import aiohttp

URL_INFO = "https://api.hyperliquid.xyz/info"
URL_CLASSEMENT = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
# part du budget et réserve (en poids) de chaque seau
PARTS = {"lectures": (0.70, 60), "verifications": (0.24, 45), "divers": (0.06, 120)}


class Seau:
    """Seau à jetons : `par_minute` de poids, plus une réserve pour les rafales."""

    def __init__(self, par_minute: float, reserve: float = 40.0):
        self.debit = par_minute / 60.0
        self.capacite = reserve
        self.jetons = self.capacite
        self._t = time.monotonic()
        self.consomme = 0.0

    def _remplir(self) -> None:
        t = time.monotonic()
        self.jetons = min(self.capacite, self.jetons + (t - self._t) * self.debit)
        self._t = t

    async def prendre(self, poids: float) -> None:
        while True:
            self._remplir()
            if self.jetons >= poids:
                self.jetons -= poids
                self.consomme += poids
                return
            await asyncio.sleep(min(5.0, (poids - self.jetons) / self.debit))

    def disponible(self, poids: float) -> bool:
        self._remplir()
        return self.jetons >= poids

    def penaliser(self, secondes: float) -> None:
        self._remplir()
        self.jetons = min(self.jetons, -secondes * self.debit)


class LimiteHyperliquid(Exception):
    pass


class InfoHyperliquid:
    def __init__(self, session: aiohttp.ClientSession, poids_minute: float = 1100):
        self.session = session
        self.seaux = {nom: Seau(poids_minute * part, reserve) for nom, (part, reserve) in PARTS.items()}
        self.requetes = 0
        self.refus = 0
        self._debut = time.monotonic()

    async def demander(self, corps: dict, poids: float = 20, seau: str = "divers"):
        await self.seaux[seau].prendre(poids)
        self.requetes += 1
        async with self.session.post(URL_INFO, json=corps, timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status == 429:
                self.refus += 1
                for s in self.seaux.values():
                    s.penaliser(15)
                raise LimiteHyperliquid("limite de débit atteinte (429)")
            r.raise_for_status()
            return await r.json(content_type=None)

    def poids_par_minute(self) -> float:
        duree = max(1.0, time.monotonic() - self._debut)
        return sum(s.consomme for s in self.seaux.values()) / duree * 60

    # -- requêtes utilisées -----------------------------------------------
    async def meta(self):
        return await self.demander({"type": "meta"})

    async def compte(self, adresse: str):
        return await self.demander({"type": "clearinghouseState", "user": adresse}, 2, "lectures")

    async def transactions(self, adresse: str, debut_ms: int, fin_ms: int):
        return await self.demander({"type": "userFillsByTime", "user": adresse, "startTime": debut_ms,
                                    "endTime": fin_ms}, 21, "verifications")

    async def coffre(self, adresse: str):
        return await self.demander({"type": "vaultDetails", "vaultAddress": adresse})

    async def classement(self) -> list[tuple[str, float]]:
        """
        Le classement public : environ 50 000 comptes et leur valeur. Le fichier
        pèse 40 Mo ; on n'en extrait que l'adresse et la valeur de chaque compte.
        """
        async with self.session.get(URL_CLASSEMENT, timeout=aiohttp.ClientTimeout(total=180)) as r:
            r.raise_for_status()
            octets = await r.read()
        return await asyncio.to_thread(_extraire_classement, octets)


_LIGNE_CLASSEMENT = re.compile(rb'"ethAddress"\s*:\s*"(0x[0-9a-fA-F]{40})"\s*,\s*"accountValue"\s*:\s*"([^"]+)"')


def _extraire_classement(octets: bytes) -> list[tuple[str, float]]:
    lignes = []
    for adresse, valeur in _LIGNE_CLASSEMENT.findall(octets):
        try:
            lignes.append((adresse.decode().lower(), float(valeur)))
        except ValueError:
            continue
    if not lignes:                      # format changé : lecture complète, plus lente
        for ligne in json.loads(octets).get("leaderboardRows", []):
            try:
                lignes.append((ligne["ethAddress"].lower(), float(ligne["accountValue"])))
            except (KeyError, TypeError, ValueError):
                continue
    return lignes
