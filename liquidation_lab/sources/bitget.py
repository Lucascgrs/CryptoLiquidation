"""
Bitget : sujet `liquidation` de l'API v3 publique.

Bitget ne publie, par contrat et par seconde, que la plus grosse liquidation
long et la plus grosse short. `side` = buy : un long a été liquidé (convention
de Bitget, vérifiée sur une vague de ventes). `amount` est en dollars.
"""

from __future__ import annotations

import json
import re

from ..modele import LONG, SHORT, Liquidation, nombre
from ..symboles import normaliser
from .base import Plan, Source

FAMILLES = ("usdt-futures", "usdc-futures", "coin-futures")
_SYMBOLE = re.compile(r"^([A-Z0-9]+?)(USDT|USDC|PERP|USD)")


class SourceBitget(Source):
    cle = "bitget"

    def plans(self) -> list[Plan]:
        def abonnements():
            return [{"op": "subscribe", "args": [{"instType": f, "topic": "liquidation"} for f in FAMILLES]}]

        self._plan = Plan("wss://ws.bitget.com/v3/ws/public", self.traiter, abonnements=abonnements,
                          ping=lambda: "ping", intervalle_ping=25, silence_max=80, nom="liquidation")
        return [self._plan]

    def traiter(self, brut):
        if brut == "pong":
            return None
        message = json.loads(brut)
        if message.get("event") == "error":
            self.signaler(f"Bitget : {message.get('msg')}")
            return None
        if (message.get("arg") or {}).get("topic") != "liquidation":
            return None
        for x in message.get("data", []):
            t = int(x["ts"])
            symbole = x["symbol"]
            trouve = _SYMBOLE.match(symbole)
            actif, paquet = normaliser(trouve.group(1) if trouve else symbole)
            prix, valeur = nombre(x["price"]), nombre(x["amount"])
            self.emettre(Liquidation(
                t=t, plateforme=self.cle, marche=symbole, actif=actif,
                cote=LONG if x["side"] == "buy" else SHORT,
                prix=prix / paquet, quantite=valeur / prix * paquet if prix > 0 else float("nan"),
                valeur=valeur))
        return None
