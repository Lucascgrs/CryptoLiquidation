"""
Bybit : sujet `allLiquidation.<symbole>`, toutes les liquidations, toutes les 500 ms.

Particularité : Bybit donne le côté de la POSITION (S = Buy : un long a été
liquidé), à l'inverse des autres plateformes qui donnent le côté de l'ordre.
Contrats linéaires (USDT, USDC) : quantité en unités de l'actif. Contrats
inverses (BTCUSD…) : quantité en dollars.
"""

from __future__ import annotations

import json
import math

from ..modele import LONG, SHORT, Liquidation, nombre
from ..symboles import normaliser
from .base import Plan, Source, lire_json

CATEGORIES = ("linear", "inverse")


class SourceBybit(Source):
    cle = "bybit"

    async def preparer(self) -> None:
        self.contrats = {}
        for categorie in CATEGORIES:
            curseur = ""
            while True:
                page = await lire_json(self.session, "https://api.bybit.com/v5/market/instruments-info",
                                       params={"category": categorie, "limit": 1000, "cursor": curseur})
                resultat = page.get("result") or {}
                for instrument in resultat.get("list", []):
                    if instrument.get("status") == "Trading":
                        self.contrats[instrument["symbol"]] = (categorie, instrument.get("baseCoin", ""))
                curseur = resultat.get("nextPageCursor")
                if not curseur:
                    break

    def plans(self) -> list[Plan]:
        plans = []
        for categorie in CATEGORIES:
            symboles = sorted(s for s, (c, _) in self.contrats.items() if c == categorie)

            def abonnements(symboles=symboles):
                return [{"op": "subscribe", "args": [f"allLiquidation.{s}" for s in symboles[i:i + 10]]}
                        for i in range(0, len(symboles), 10)]

            plans.append(Plan(f"wss://stream.bybit.com/v5/public/{categorie}", self.traiter,
                              abonnements=abonnements, ping=lambda: {"op": "ping"},
                              intervalle_ping=20, silence_max=75, nom=categorie))
        return plans

    def traiter(self, brut):
        message = json.loads(brut)
        sujet = message.get("topic", "")
        if not sujet.startswith("allLiquidation."):
            if message.get("op") == "subscribe" and not message.get("success", True):
                self.signaler(f"abonnement refusé : {message.get('ret_msg')}")
            return None
        for x in message.get("data", []):
            symbole = x["s"]
            categorie, base = self.contrats.get(symbole, ("linear", symbole.removesuffix("USDT")))
            actif, paquet = normaliser(base)
            volume, prix = nombre(x["v"]), nombre(x["p"])
            if categorie == "inverse":
                valeur, quantite = volume, volume / prix if prix > 0 else math.nan
            else:
                valeur, quantite = volume * prix, volume
            self.emettre(Liquidation(
                t=int(x["T"]), plateforme=self.cle, marche=symbole, actif=actif,
                cote=LONG if x["S"] == "Buy" else SHORT,
                prix=prix / paquet, quantite=quantite * paquet, valeur=valeur))
        return None
