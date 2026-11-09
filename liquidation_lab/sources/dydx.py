"""
dYdX v4 : canal `v4_trades` de l'indexeur, un abonnement par marché.

Les transactions de type LIQUIDATED sont des liquidations ; `side` est le sens
de l'ordre de liquidation (SELL = un long est liquidé). L'indexeur limite à
32 abonnements `v4_trades` par connexion : les marchés sont répartis sur
plusieurs connexions.
"""

from __future__ import annotations

import json
from datetime import datetime

from ..modele import LONG, SHORT, Liquidation, nombre
from ..symboles import normaliser
from .base import Plan, Source, lire_json

PAR_CONNEXION = 30


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


class SourceDydx(Source):
    cle = "dydx"

    async def preparer(self) -> None:
        reponse = await lire_json(self.session, "https://indexer.dydx.trade/v4/perpetualMarkets")
        marches = reponse.get("markets", {})
        self.marches = sorted((m for m, v in marches.items() if v.get("status") == "ACTIVE"),
                              key=lambda m: -nombre(marches[m].get("volume24H") or 0))

    def plans(self) -> list[Plan]:
        plans = []
        for i in range(0, len(self.marches), PAR_CONNEXION):
            lot = self.marches[i:i + PAR_CONNEXION]
            plans.append(Plan("wss://indexer.dydx.trade/v4/ws", self.traiter,
                              abonnements=lambda lot=lot: [{"type": "subscribe", "channel": "v4_trades",
                                                            "id": m} for m in lot],
                              nom=f"v4_trades {i // PAR_CONNEXION + 1}"))
        return plans

    def traiter(self, brut):
        message = json.loads(brut)
        genre = message.get("type")
        if genre == "error":
            self.signaler(f"dYdX : {message.get('message')}")
            return None
        # « subscribed » apporte l'historique récent : ignoré
        if genre != "channel_data" or message.get("channel") != "v4_trades":
            return None
        marche = message["id"]
        actif, paquet = normaliser(marche.split("-")[0])
        for x in (message.get("contents") or {}).get("trades", []):
            if x.get("type") != "LIQUIDATED":
                continue
            prix, quantite = nombre(x["price"]), nombre(x["size"])
            self.emettre(Liquidation(
                t=_ms(x["createdAt"]), plateforme=self.cle, marche=marche, actif=actif,
                cote=LONG if x["side"] == "SELL" else SHORT,
                prix=prix / paquet, quantite=quantite * paquet, valeur=quantite * prix))
        return None
