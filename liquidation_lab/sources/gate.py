"""
Gate : canal `futures.public_liquidates` sur les contrats USDT, tous contrats (« !all »).

La taille est en contrats, signée comme un ordre : négative quand l'ordre de
liquidation vend, donc quand un long est liquidé (vérifié sur une vague de
ventes, face à Binance). Un contrat vaut `quanto_multiplier` unités de l'actif.
"""

from __future__ import annotations

import asyncio
import json
import time

from ..modele import LONG, SHORT, Liquidation, nombre
from ..symboles import normaliser
from .base import Plan, Source, lire_json


class SourceGate(Source):
    cle = "gate"

    async def preparer(self) -> None:
        contrats = await lire_json(self.session, "https://api.gateio.ws/api/v4/futures/usdt/contracts")
        self.multiplicateurs = {c["name"]: nombre(c.get("quanto_multiplier")) for c in contrats}
        self._relu_le = time.monotonic()

    def plans(self) -> list[Plan]:
        def abonnements():
            return [{"time": int(time.time()), "channel": "futures.public_liquidates",
                     "event": "subscribe", "payload": ["!all"]}]

        return [Plan("wss://fx-ws.gateio.ws/v4/ws/usdt", self.traiter, abonnements=abonnements,
                     ping=lambda: {"time": int(time.time()), "channel": "futures.ping"},
                     intervalle_ping=20, silence_max=75, nom="public_liquidates")]

    def traiter(self, brut):
        message = json.loads(brut)
        if message.get("channel") != "futures.public_liquidates" or message.get("event") != "update":
            if message.get("error"):
                self.signaler(f"Gate : {message['error']}")
            return None
        for x in message.get("result", []):
            taille = nombre(x.get("size"))
            if not taille:                      # Gate publie parfois des tailles nulles : rien à compter
                continue
            contrat = x["contract"]
            multiplicateur = self.multiplicateurs.get(contrat)
            if not multiplicateur:
                self._inconnu()
                continue
            actif, paquet = normaliser(contrat.split("_")[0])
            prix = nombre(x["price"])
            quantite = abs(taille) * multiplicateur
            self.emettre(Liquidation(
                t=int(x["time"]), plateforme=self.cle, marche=contrat, actif=actif,
                cote=LONG if taille < 0 else SHORT,
                prix=prix / paquet, quantite=quantite * paquet, valeur=quantite * prix))
        return None

    def _inconnu(self) -> None:
        if time.monotonic() - self._relu_le > 600:
            self._relu_le = time.monotonic()
            self._relecture = asyncio.get_running_loop().create_task(self.preparer())
