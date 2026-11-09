"""
HTX : sujet `public.*.liquidation_orders` des contrats USDT (messages compressés en gzip).

`direction` est le sens de l'ordre de liquidation : sell = un long est liquidé.
`amount` est en unités de l'actif, `trade_turnover` en dollars.
"""

from __future__ import annotations

import gzip
import json

from ..modele import LONG, SHORT, Liquidation, nombre
from ..symboles import normaliser
from .base import Plan, Source


class SourceHTX(Source):
    cle = "htx"

    def plans(self) -> list[Plan]:
        def abonnements():
            return [{"op": "sub", "cid": "liquidations", "topic": "public.*.liquidation_orders"}]

        return [Plan("wss://api.hbdm.com/linear-swap-notification", self.traiter,
                     abonnements=abonnements, decodeur=lambda b: gzip.decompress(b).decode()
                     if isinstance(b, bytes) else b, silence_max=90, nom="liquidation_orders")]

    def traiter(self, brut):
        message = json.loads(brut)
        operation = message.get("op")
        if operation == "ping":
            return {"op": "pong", "ts": message.get("ts")}
        if "ping" in message:
            return {"pong": message["ping"]}
        if operation == "sub" and message.get("err-code"):
            self.signaler(f"HTX : {message.get('err-msg')}")
        if operation != "notify" or not str(message.get("topic", "")).endswith("liquidation_orders"):
            return None
        for x in message.get("data", []):
            actif, paquet = normaliser(x.get("symbol") or x["contract_code"].split("-")[0])
            prix, quantite = nombre(x["price"]), nombre(x.get("amount"))
            valeur = nombre(x.get("trade_turnover"))
            if not valeur > 0:
                valeur = quantite * prix
            self.emettre(Liquidation(
                t=int(x["created_at"]), plateforme=self.cle, marche=x["contract_code"], actif=actif,
                cote=LONG if x["direction"] == "sell" else SHORT,
                prix=prix / paquet, quantite=quantite * paquet, valeur=valeur))
        return None
