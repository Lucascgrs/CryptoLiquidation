"""
Socle commun des sources : connexions WebSocket tenues en permanence
(reconnexion de plus en plus espacée, chien de garde contre le silence),
lecture des listes de contrats par l'API REST, compteurs pour l'écran du
collecteur.

Une source décrit ses connexions par des `Plan` : l'adresse, les messages
d'abonnement, le traitement de chaque message reçu et, si la plateforme
l'exige, le message de maintien (« ping ») à envoyer régulièrement.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

import aiohttp
import websockets

from ..modele import Liquidation


def maintenant_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Plan:
    url: str
    traiter: Callable[[str | bytes], object]         # rend éventuellement un message à renvoyer
    abonnements: Callable[[], list] = list           # appelé à chaque (re)connexion
    ping: Callable[[], object] | None = None         # message de maintien
    intervalle_ping: float = 20.0
    silence_max: float | None = None                 # reconnexion si rien n'arrive pendant ce temps
    decodeur: Callable[[bytes], str] | None = None
    pause_abonnements: float = 0.0
    nom: str = ""
    ws: object = field(default=None, repr=False)     # connexion ouverte (None sinon)
    ouverte_le: int = 0                              # ms


class EtatSource:
    """Compteurs d'une source, lus par l'écran du collecteur."""

    def __init__(self, cle: str):
        self.cle = cle
        self.connexions = 0
        self.prevues = 0
        self.messages = 0
        self.liquidations = 0
        self.valeur = 0.0
        self.derniere = 0
        self.reconnexions = 0
        self.erreur = ""
        self.heure_erreur = 0
        self.connectee_le = 0
        self.note = ""

    def vers_dict(self) -> dict:
        return {"connexions": self.connexions, "prevues": self.prevues, "messages": self.messages,
                "liquidations": self.liquidations, "valeur": round(self.valeur, 2),
                "derniere": self.derniere, "reconnexions": self.reconnexions,
                "erreur": self.erreur, "heure_erreur": self.heure_erreur,
                "connectee_le": self.connectee_le, "note": self.note}


async def lire_json(session: aiohttp.ClientSession, url: str, params: dict | None = None,
                    corps: dict | None = None, essais: int = 3):
    """GET (ou POST si `corps`) et JSON, avec quelques nouvelles tentatives."""
    derniere = None
    for essai in range(essais):
        try:
            if corps is None:
                requete = session.get(url, params=params)
            else:
                requete = session.post(url, json=corps)
            async with requete as reponse:
                reponse.raise_for_status()
                return await reponse.json(content_type=None)
        except asyncio.CancelledError:
            raise
        except Exception as erreur:                     # noqa: BLE001
            derniere = erreur
            await asyncio.sleep(1.5 * (essai + 1))
    raise RuntimeError(f"{url} : {derniere}")


class Source:
    """Une plateforme. Les sous-classes définissent `cle`, `preparer()` et `plans()`."""

    cle = ""

    def __init__(self, emettre: Callable[[Liquidation], None], journal: logging.Logger):
        self._emettre = emettre
        self.journal = journal
        self.etat = EtatSource(self.cle)
        self.session: aiohttp.ClientSession | None = None
        self._erreurs_traitement = 0
        self._vues: dict[tuple, int] = {}
        self._vues_purgees = 0.0

    # ------------------------------------------------------------------
    def emettre(self, liquidation: Liquidation) -> None:
        if not (liquidation.valeur > 0) or not (liquidation.prix > 0):
            return
        if not self._nouvelle(liquidation):
            return
        self.etat.liquidations += 1
        self.etat.valeur += liquidation.valeur
        self.etat.derniere = max(self.etat.derniere, liquidation.t)
        self._emettre(liquidation)

    def _nouvelle(self, liq: Liquidation) -> bool:
        """
        Écarte les liquidations renvoyées à l'abonnement : certaines plateformes
        (OKX, Bitget) commencent par republier les dernières, parfois vieilles de
        plusieurs heures. On garde ce qui suit la première connexion (à deux
        minutes près, pour l'écart d'horloge), sans doublon après une reconnexion
        ni entre le flux et l'API REST (OKX). Les clés des liquidations vues sont
        gardées 30 minutes : au-delà de 25 minutes, une liquidation est trop
        ancienne pour être nouvelle.
        """
        if self.etat.connectee_le and liq.t < self.etat.connectee_le - 120_000:
            return False
        if liq.t < maintenant_ms() - 25 * 60_000:
            return False
        cle = (liq.marche, liq.t, liq.cote, round(liq.quantite, 9), round(liq.prix, 9))
        if cle in self._vues:
            return False
        self._vues[cle] = liq.t
        if time.monotonic() - self._vues_purgees > 60:
            self._vues_purgees = time.monotonic()
            limite = maintenant_ms() - 30 * 60_000
            self._vues = {c: t for c, t in self._vues.items() if t >= limite}
        return True

    def signaler(self, texte: str) -> None:
        self.etat.erreur = texte[:300]
        self.etat.heure_erreur = maintenant_ms()
        self.journal.warning("%s : %s", self.cle, texte)

    async def preparer(self) -> None:
        """Lit ce qu'il faut savoir des contrats (tailles, actifs) avant de se connecter."""

    def plans(self) -> list[Plan]:
        raise NotImplementedError

    async def taches_annexes(self) -> list:
        """Coroutines supplémentaires à faire tourner avec les connexions (Hyperliquid)."""
        return []

    # ------------------------------------------------------------------
    async def tourner(self, session: aiohttp.ClientSession) -> None:
        self.session = session
        delai = 5
        while True:
            try:
                await self.preparer()
                break
            except asyncio.CancelledError:
                raise
            except Exception as erreur:                 # noqa: BLE001
                self.signaler(f"préparation impossible ({erreur}), nouvel essai dans {delai} s")
                await asyncio.sleep(delai)
                delai = min(delai * 2, 120)
        plans = self.plans()
        self.etat.prevues = len(plans)
        annexes = await self.taches_annexes()
        await asyncio.gather(*(self._tenir(plan) for plan in plans), *annexes)

    async def _tenir(self, plan: Plan) -> None:
        delai = 2.0
        while True:
            debut = time.monotonic()
            try:
                await self._session_ws(plan)
            except asyncio.CancelledError:
                raise
            except Exception as erreur:                 # noqa: BLE001
                self.signaler(f"{plan.nom or 'connexion'} : {type(erreur).__name__} {erreur}")
            if time.monotonic() - debut > 120:
                delai = 2.0
            self.etat.reconnexions += 1
            await asyncio.sleep(delai)
            delai = min(delai * 2, 60.0)

    async def _session_ws(self, plan: Plan) -> None:
        async with websockets.connect(plan.url, max_size=2 ** 26, open_timeout=20, close_timeout=3,
                                      ping_interval=20, ping_timeout=40) as ws:
            plan.ws, plan.ouverte_le = ws, maintenant_ms()
            self.etat.connexions += 1
            if not self.etat.connectee_le:
                self.etat.connectee_le = plan.ouverte_le
            maintien = asyncio.create_task(self._maintenir(ws, plan)) if plan.ping else None
            try:
                for message in plan.abonnements():
                    await ws.send(message if isinstance(message, (str, bytes)) else json.dumps(message))
                    if plan.pause_abonnements:
                        await asyncio.sleep(plan.pause_abonnements)
                while True:
                    if plan.silence_max:
                        try:
                            brut = await asyncio.wait_for(ws.recv(), timeout=plan.silence_max)
                        except asyncio.TimeoutError:
                            raise ConnectionError(f"aucun message depuis {plan.silence_max:.0f} s")
                    else:
                        brut = await ws.recv()
                    self.etat.messages += 1
                    reponse = self._traiter(plan, brut)
                    if reponse is not None:
                        await ws.send(reponse if isinstance(reponse, (str, bytes)) else json.dumps(reponse))
            finally:
                plan.ws = None
                self.etat.connexions -= 1
                if maintien:
                    maintien.cancel()

    def _traiter(self, plan: Plan, brut):
        try:
            if plan.decodeur:
                brut = plan.decodeur(brut)
            return plan.traiter(brut)
        except Exception as erreur:                     # noqa: BLE001
            self._erreurs_traitement += 1
            if self._erreurs_traitement <= 5 or self._erreurs_traitement % 500 == 0:
                self.signaler(f"message illisible ({type(erreur).__name__} : {erreur}) : {str(brut)[:200]}")
            return None

    @staticmethod
    async def _maintenir(ws, plan: Plan) -> None:
        try:
            while True:
                await asyncio.sleep(plan.intervalle_ping)
                message = plan.ping()
                await ws.send(message if isinstance(message, (str, bytes)) else json.dumps(message))
        except (asyncio.CancelledError, websockets.ConnectionClosed):
            pass
