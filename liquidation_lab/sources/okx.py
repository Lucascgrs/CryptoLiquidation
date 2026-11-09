"""
OKX : canal `liquidation-orders` (perpétuels et contrats à terme), complété par
l'API REST.

Le flux en direct ne publie qu'environ une liquidation sur deux (mesuré le
30/10/2026 : 17 sur 30 pour ETH-USDT en 13 minutes, toutes présentes dans
l'API REST). L'API REST `public/liquidation-orders` rend les 100 dernières de
chaque famille de contrats : elle est relue toutes les 15 secondes pour les
40 familles les plus échangées, et par roulement pour les autres. Une même
liquidation reçue des deux côtés n'est comptée qu'une fois.

La taille est donnée en contrats : sa valeur (ctVal) vient de la liste des
instruments. Contrat linéaire : ctVal en unités de l'actif ; contrat inverse :
ctVal en dollars. Le prix publié est le prix de faillite (bkPx).
"""

from __future__ import annotations

import asyncio
import json
import time

from ..modele import LONG, SHORT, Liquidation, nombre
from ..symboles import normaliser
from .base import Plan, Source, lire_json

TYPES = ("SWAP", "FUTURES")
URL_REST = "https://www.okx.com/api/v5/public/liquidation-orders"
FAMILLES_RAPIDES = 40           # relues à chaque tour
FAMILLES_LENTES_PAR_TOUR = 8    # les autres, par roulement
TOUR = 15.0                     # secondes
PAUSE_REQUETE = 0.25            # OKX permet 20 requêtes par seconde : on en fait 4


class SourceOKX(Source):
    cle = "okx"

    async def preparer(self) -> None:
        contrats = {}
        for type_instrument in TYPES:
            reponse = await lire_json(self.session, "https://www.okx.com/api/v5/public/instruments",
                                      params={"instType": type_instrument})
            for i in reponse.get("data", []):
                famille = i.get("instFamily") or i.get("uly") or i["instId"]
                contrats[i["instId"]] = (nombre(i.get("ctVal")) * (nombre(i.get("ctMult")) or 1.0),
                                         i.get("ctType", "linear"), famille.split("-")[0], famille, type_instrument)
        self.contrats = contrats
        self._relu_le = time.monotonic()
        self._inconnus = set()

    def plans(self) -> list[Plan]:
        def abonnements():
            return [{"op": "subscribe",
                     "args": [{"channel": "liquidation-orders", "instType": t} for t in TYPES]}]

        return [Plan("wss://ws.okx.com/ws/v5/public", self.traiter, abonnements=abonnements,
                     ping=lambda: "ping", intervalle_ping=20, silence_max=75, nom="liquidation-orders")]

    async def taches_annexes(self) -> list:
        return [self._complement_rest()]

    # ------------------------------------------------------------------
    def traiter(self, brut):
        if brut == "pong":
            return None
        message = json.loads(brut)
        if message.get("event") == "error":
            self.signaler(f"OKX : {message.get('msg')}")
            return None
        if (message.get("arg") or {}).get("channel") != "liquidation-orders":
            return None
        self._blocs(message.get("data", []))
        return None

    def _blocs(self, blocs: list[dict]) -> None:
        for bloc in blocs:
            instrument = bloc["instId"]
            contrat = self.contrats.get(instrument)
            if contrat is None:
                self._inconnu(instrument)
                continue
            taille_contrat, genre, base = contrat[:3]
            actif, paquet = normaliser(base)
            for d in bloc.get("details", []):
                contrats, prix = nombre(d["sz"]), nombre(d["bkPx"])
                if genre == "inverse":
                    valeur = contrats * taille_contrat
                    quantite = valeur / prix if prix > 0 else float("nan")
                else:
                    quantite = contrats * taille_contrat
                    valeur = quantite * prix
                cote_position = d.get("posSide")
                if cote_position not in ("long", "short"):
                    cote_position = LONG if d.get("side") == "sell" else SHORT
                self.emettre(Liquidation(
                    t=int(d["ts"]), plateforme=self.cle, marche=instrument, actif=actif,
                    cote=cote_position, prix=prix / paquet, quantite=quantite * paquet, valeur=valeur))

    # ------------------------------------------------------------------
    async def _familles(self) -> list[str]:
        """Les familles de perpétuels, des plus échangées aux moins échangées."""
        familles = {c[3] for c in self.contrats.values() if c[4] == "SWAP"}
        volumes = {}
        try:
            tickers = await lire_json(self.session, "https://www.okx.com/api/v5/market/tickers",
                                      params={"instType": "SWAP"})
            for t in tickers.get("data", []):
                contrat = self.contrats.get(t["instId"])
                if contrat:
                    volumes[contrat[3]] = volumes.get(contrat[3], 0.0) + nombre(t.get("volCcy24h")) * \
                        nombre(t.get("last"))
        except Exception as erreur:                     # noqa: BLE001
            self.journal.info("okx : volumes illisibles (%s)", erreur)
        return sorted(familles, key=lambda f: -(volumes.get(f) or 0.0))

    async def _complement_rest(self) -> None:
        await asyncio.sleep(5)
        familles = await self._familles()
        rapides, lentes = familles[:FAMILLES_RAPIDES], familles[FAMILLES_RAPIDES:]
        i = 0
        while True:
            debut = time.monotonic()
            tour = list(rapides)
            for _ in range(min(FAMILLES_LENTES_PAR_TOUR, len(lentes))):
                tour.append(lentes[i % len(lentes)])
                i += 1
            for famille in tour:
                try:
                    reponse = await lire_json(self.session, URL_REST, essais=1, params={
                        "instType": "SWAP", "instFamily": famille, "state": "filled", "limit": 100})
                    self._blocs(reponse.get("data", []))
                except asyncio.CancelledError:
                    raise
                except Exception as erreur:             # noqa: BLE001
                    self.journal.debug("okx : REST %s illisible (%s)", famille, erreur)
                await asyncio.sleep(PAUSE_REQUETE)
            await asyncio.sleep(max(0.0, TOUR - (time.monotonic() - debut)))

    # ------------------------------------------------------------------
    def _inconnu(self, instrument: str) -> None:
        """Contrat apparu en cours de route : on relit la liste (au plus toutes les 10 minutes)."""
        if instrument not in self._inconnus:
            self._inconnus.add(instrument)
            self.journal.info("okx : contrat inconnu %s", instrument)
        if time.monotonic() - self._relu_le > 600:
            self._relu_le = time.monotonic()
            self._relecture = asyncio.get_running_loop().create_task(self._relire())

    async def _relire(self) -> None:
        try:
            await self.preparer()
        except Exception as erreur:                     # noqa: BLE001
            self.signaler(f"relecture des contrats impossible : {erreur}")
