"""
Binance : flux `!forceOrder@arr`.

Binance ne publie, pour chaque contrat, que la plus grosse liquidation de chaque
seconde. L'ordre de liquidation vend quand un long est liquidé (S = SELL).

Depuis l'intégration des deux marchés (2026), ce flux porte aussi les contrats
inverses (BTCUSD_PERP, ETHUSD_PERP…). Leur quantité est un nombre de contrats
de 100 $ (BTC) ou 10 $ (les autres) : la valeur en dollars en découle
directement, pas du prix.
"""

from __future__ import annotations

import json

from ..modele import LONG, SHORT, Liquidation, nombre
from ..symboles import normaliser, retirer_cotation
from .base import Plan, Source, lire_json

URL = "wss://fstream.binance.com/market/ws/!forceOrder@arr"     # nouvelle route « /market » (avril 2026)


class SourceBinance(Source):
    cle = "binance"

    async def preparer(self) -> None:
        info = await lire_json(self.session, "https://fapi.binance.com/fapi/v1/exchangeInfo")
        self.bases = {s["symbol"]: s["baseAsset"] for s in info.get("symbols", [])}
        self.inverses = {}                              # symbole -> (actif, dollars par contrat)
        try:
            info = await lire_json(self.session, "https://dapi.binance.com/dapi/v1/exchangeInfo")
            self.inverses = {s["symbol"]: (s["baseAsset"], nombre(s.get("contractSize")))
                             for s in info.get("symbols", [])}
        except Exception as erreur:                     # noqa: BLE001
            self.journal.info("binance : contrats inverses illisibles (%s), tailles par défaut", erreur)

    def plans(self) -> list[Plan]:
        return [Plan(URL, self.traiter, nom="forceOrder")]

    def traiter(self, brut):
        ordre = json.loads(brut).get("o")
        if not ordre:
            return None
        symbole = ordre["s"]
        quantite = nombre(ordre.get("z"))
        if not quantite > 0:
            quantite = nombre(ordre.get("q"))
        prix = nombre(ordre.get("ap"))
        if not prix > 0:
            prix = nombre(ordre.get("p"))
        if "_" in symbole:                              # contrat inverse : quantité en contrats
            base, taille = self.inverses.get(symbole, (symbole.split("USD")[0], 0.0))
            if not taille > 0:
                taille = 100.0 if base == "BTC" else 10.0
            actif, paquet = normaliser(base)
            valeur = quantite * taille
            quantite = valeur / prix if prix > 0 else float("nan")
        else:
            actif, paquet = normaliser(self.bases.get(symbole) or retirer_cotation(symbole))
            valeur = quantite * prix
        self.emettre(Liquidation(
            t=int(ordre["T"]), plateforme=self.cle, marche=symbole, actif=actif,
            cote=LONG if ordre["S"] == "SELL" else SHORT,
            prix=prix / paquet, quantite=quantite * paquet, valeur=valeur))
        return None
