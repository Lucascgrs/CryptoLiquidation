"""
Deribit : toutes les transactions sur contrats à terme et perpétuels
(`trades.future.any.100ms`), dont on garde celles qui portent le champ
`liquidation`.

`liquidation` vaut « T » (l'agresseur était liquidé), « M » (le teneur d'ordre
l'était) ou « MT » (les deux) ; `direction` est le sens de l'agresseur.
Contrats inverses (BTC-PERPETUAL…) : quantité en dollars ; contrats linéaires
(SOL_USDC-PERPETUAL…) : quantité en unités de l'actif.
"""

from __future__ import annotations

import json

from ..modele import LONG, SHORT, Liquidation, nombre
from ..symboles import normaliser
from .base import Plan, Source


class SourceDeribit(Source):
    cle = "deribit"

    def plans(self) -> list[Plan]:
        def abonnements():
            return [{"jsonrpc": "2.0", "id": 1, "method": "public/subscribe",
                     "params": {"channels": ["trades.future.any.100ms"]}},
                    {"jsonrpc": "2.0", "id": 2, "method": "public/set_heartbeat", "params": {"interval": 30}}]

        return [Plan("wss://www.deribit.com/ws/api/v2", self.traiter, abonnements=abonnements,
                     silence_max=90, nom="trades")]

    def traiter(self, brut):
        message = json.loads(brut)
        methode = message.get("method")
        if methode == "heartbeat":
            if (message.get("params") or {}).get("type") == "test_request":
                return {"jsonrpc": "2.0", "id": 3, "method": "public/test", "params": {}}
            return None
        if "error" in message:
            self.signaler(f"Deribit : {message['error']}")
            return None
        if methode != "subscription":
            return None
        for x in message["params"]["data"]:
            drapeau = x.get("liquidation")
            if not drapeau:
                continue
            instrument = x["instrument_name"]
            base = instrument.split("-")[0]
            lineaire = "_" in base
            actif, paquet = normaliser(base.split("_")[0])
            prix, montant = nombre(x["price"]), nombre(x["amount"])
            if lineaire:
                quantite, valeur = montant, montant * prix
            else:
                valeur, quantite = montant, montant / prix if prix > 0 else float("nan")
            vend = x["direction"] == "sell"
            cotes = []
            if "T" in drapeau:              # l'agresseur liquidé : il vend s'il était long
                cotes.append(LONG if vend else SHORT)
            if "M" in drapeau:              # le teneur d'ordre liquidé : il achète s'il était long
                cotes.append(SHORT if vend else LONG)
            for cote in cotes:
                self.emettre(Liquidation(
                    t=int(x["timestamp"]), plateforme=self.cle, marche=instrument, actif=actif,
                    cote=cote, prix=prix / paquet, quantite=quantite * paquet, valeur=valeur))
        return None
