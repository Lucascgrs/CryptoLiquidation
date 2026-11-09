"""
Kraken Futures : flux `trade` des contrats PF_ (linéaires en dollars).

Les transactions de type « liquidation » et « partialLiquidation » sont des
liquidations. `side` est le côté de l'agresseur, ici l'ordre de liquidation :
sell = un long est liquidé. Quantité en unités de l'actif.
"""

from __future__ import annotations

import json

from ..modele import LONG, SHORT, Liquidation, nombre
from ..symboles import normaliser
from .base import Plan, Source, lire_json

TYPES_LIQUIDATION = {"liquidation", "partialLiquidation"}


class SourceKraken(Source):
    cle = "kraken"

    async def preparer(self) -> None:
        reponse = await lire_json(self.session, "https://futures.kraken.com/derivatives/api/v3/instruments")
        self.produits = sorted(i["symbol"] for i in reponse.get("instruments", [])
                               if i.get("tradeable") and str(i.get("symbol", "")).startswith("PF_"))

    def plans(self) -> list[Plan]:
        def abonnements():
            return [{"event": "subscribe", "feed": "heartbeat"},
                    {"event": "subscribe", "feed": "trade", "product_ids": self.produits}]

        return [Plan("wss://futures.kraken.com/ws/v1", self.traiter, abonnements=abonnements,
                     silence_max=75, nom="trade")]

    def traiter(self, brut):
        message = json.loads(brut)
        if message.get("event") == "error":
            self.signaler(f"Kraken : {message.get('message')}")
            return None
        # « trade_snapshot » (historique envoyé à l'abonnement) est ignoré
        if message.get("feed") != "trade" or message.get("type") not in TYPES_LIQUIDATION:
            return None
        produit = message["product_id"]
        actif, paquet = normaliser(produit[3:].removesuffix("USD"))
        prix, quantite = nombre(message["price"]), nombre(message["qty"])
        self.emettre(Liquidation(
            t=int(message["time"]), plateforme=self.cle, marche=produit, actif=actif,
            cote=LONG if message["side"] == "sell" else SHORT,
            prix=prix / paquet, quantite=quantite * paquet, valeur=quantite * prix,
            detail="partielle" if message["type"] == "partialLiquidation" else ""))
        return None
