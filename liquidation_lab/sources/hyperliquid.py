"""
Hyperliquid : une connexion pour tout.

- `trades` de chaque crypto : les adresses qui tradent (pour le scanner) et la
  reconnaissance des liquidations (détecteur) ;
- `allMids` : les prix courants ;
- `allDexsAssetCtxs` : prix de marque et open interest de chaque crypto ;
- `userFills` des coffres HLP, puis des plus gros teneurs de marché repérés
  dans le flux (Hyperliquid autorise 10 comptes suivis par adresse IP) : leurs
  transactions marquent les liquidations dont ils sont la contrepartie.

Le scanner (lecture des comptes) et le détecteur tournent avec la connexion.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter

from ..hyperliquid.api import InfoHyperliquid
from ..hyperliquid.base import BaseEcriture
from ..hyperliquid.detecteur import Detecteur
from ..hyperliquid.scanner import Scanner
from .base import Plan, Source

URL = "wss://api.hyperliquid.xyz/ws"
COFFRE_HLP = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"
# coffres enfants du HLP au 30/10/2026, si la liste ne peut pas être relue
HLP_CONNUS = {
    "0x010461c14e146ac35fe42271bdc1134ee31c703a": "HLP Strategy A",
    "0x31ca8395cf837de08b24da3f660e77761dfb974b": "HLP Strategy B",
    "0x2e3d94f0562703b25c83308a05046ddaf9a8dd14": "HLP Liquidator",
    "0xb0a55f13d22f66e6d495ac98113841b2326e9540": "HLP Liquidator 2",
    "0x5e177e5e39c0f4e421f5865a6d8beed8d921cb70": "HLP Liquidator 3",
    "0x2ed5c4484ea3ff8b57d5f2fb152a40d9f2b68308": "HLP Liquidator 4",
}
MAX_SUIVIS = 10
OBSERVATION_TENEURS = 120.0         # secondes de flux avant de choisir les teneurs de marché suivis


class SourceHyperliquid(Source):
    cle = "hyperliquid"

    def __init__(self, emettre, journal, positions: bool = True, poids_minute: float = 1100):
        super().__init__(emettre, journal)
        self.lire_positions = positions
        self.poids_minute = poids_minute
        self.mids: dict[str, float] = {}
        self.marques: dict[str, tuple[float, float]] = {}       # coin -> (prix de marque, open interest)
        self.transactions_vues = 0
        self._teneurs = Counter()
        self._debut = time.monotonic()
        self._teneurs_choisis = False

    async def preparer(self) -> None:
        self.info = InfoHyperliquid(self.session, self.poids_minute)
        meta = await self.info.meta()
        self.coins = [u["name"] for u in meta.get("universe", []) if not u.get("isDelisted")]
        self.base = BaseEcriture()
        self.scanner = Scanner(self.info, self.base, self.journal)
        self.scanner.mids = self.mids
        self.detecteur = Detecteur(self.scanner, self.info, self.emettre, self.journal)
        suivis = await self._coffres_hlp()
        self.detecteur.suivis.update(suivis)
        self.detecteur.liquidateurs.update(a for a, nom in suivis.items() if "Liquidator" in nom)

    async def _coffres_hlp(self) -> dict[str, str]:
        """Les coffres enfants du HLP : seuls ceux qui ne sont pas déjà connus sont interrogés."""
        suivis = dict(HLP_CONNUS)
        try:
            details = await self.info.coffre(COFFRE_HLP)
            enfants = ((details.get("relationship") or {}).get("data") or {}).get("childAddresses", [])
            for adresse in (a.lower() for a in enfants):
                if adresse in HLP_CONNUS or adresse in suivis:
                    continue
                nom = (await self.info.coffre(adresse)).get("name", "")
                if "Strategy A" in nom or "Strategy B" in nom or "Liquidator" in nom:
                    suivis[adresse] = nom
        except Exception as erreur:                     # noqa: BLE001
            self.journal.warning("hyperliquid : coffres HLP illisibles (%s), liste connue utilisée", erreur)
        return dict(list(suivis.items())[:MAX_SUIVIS])

    def plans(self) -> list[Plan]:
        def abonnements():
            messages = [{"method": "subscribe", "subscription": {"type": "trades", "coin": c}} for c in self.coins]
            messages.append({"method": "subscribe", "subscription": {"type": "allMids"}})
            messages.append({"method": "subscribe", "subscription": {"type": "allDexsAssetCtxs"}})
            messages += [{"method": "subscribe", "subscription": {"type": "userFills", "user": a}}
                         for a in self.detecteur.suivis]
            return messages

        self._plan = Plan(URL, self.traiter, abonnements=abonnements, ping=lambda: {"method": "ping"},
                          intervalle_ping=30, silence_max=60, nom="flux")
        return [self._plan]

    async def taches_annexes(self) -> list:
        taches = [self.detecteur.tourner(), self._entretien()]
        if self.lire_positions:
            taches.append(self.scanner.tourner())
        return taches

    # ------------------------------------------------------------------
    def traiter(self, brut):
        message = json.loads(brut)
        canal = message.get("channel")
        donnees = message.get("data")
        if canal == "trades":
            self._transactions(donnees)
        elif canal == "allMids":
            for coin, prix in (donnees.get("mids") or {}).items():
                if not coin.startswith("@"):
                    try:
                        self.mids[coin] = float(prix)
                    except (TypeError, ValueError):
                        pass
        elif canal == "userFills":
            if not donnees.get("isSnapshot"):
                self.detecteur.fills_suivis(donnees.get("user", "").lower(), donnees.get("fills", []))
        elif canal == "allDexsAssetCtxs":
            for dex, contextes in donnees.get("ctxs", []):
                if dex:                                 # seulement le marché principal
                    continue
                for coin, ctx in zip(self.coins, contextes):
                    try:
                        self.marques[coin] = (float(ctx["markPx"]), float(ctx["openInterest"]))
                    except (KeyError, TypeError, ValueError):
                        pass
        elif canal == "error":
            self.signaler(f"Hyperliquid : {donnees}")
        return None

    def _transactions(self, lot: list[dict]) -> None:
        self.transactions_vues += len(lot)
        maintenant = time.time()
        observer = not self._teneurs_choisis
        for t in lot:
            try:
                notionnel = float(t["px"]) * float(t["sz"])
                acheteur, vendeur = t["users"]
            except (KeyError, TypeError, ValueError):
                continue
            self.scanner.transaction(acheteur, notionnel, maintenant)
            self.scanner.transaction(vendeur, notionnel, maintenant)
            if observer:
                self._teneurs[vendeur if t["side"] == "B" else acheteur] += notionnel
        self.detecteur.transactions(lot)

    async def _entretien(self) -> None:
        """Choix des teneurs de marché suivis, puis prix et open interest dans la base toutes les 15 s."""
        while True:
            await asyncio.sleep(15)
            if not self._teneurs_choisis and time.monotonic() - self._debut > OBSERVATION_TENEURS:
                await self._suivre_teneurs()
            if self.marques:
                maintenant = int(time.time() * 1000)
                self.base.marches([(c, p, oi, maintenant) for c, (p, oi) in self.marques.items()])
            self.base.valider()

    async def _suivre_teneurs(self) -> None:
        ws = self._plan.ws
        if ws is None:
            return
        places = MAX_SUIVIS - len(self.detecteur.suivis)
        choisis = [a for a, _ in self._teneurs.most_common(places + len(self.detecteur.suivis))
                   if a not in self.detecteur.suivis][:places]
        for adresse in choisis:
            self.detecteur.suivis[adresse] = "teneur de marché"
            await ws.send(json.dumps({"method": "subscribe",
                                      "subscription": {"type": "userFills", "user": adresse}}))
        self._teneurs_choisis = True
        self._teneurs.clear()
        self.journal.info("hyperliquid : %d teneurs de marché suivis en plus des coffres HLP", len(choisis))

    # ------------------------------------------------------------------
    def resume(self) -> dict:
        scanner = self.scanner.statistiques() if self.lire_positions else {}
        return {"transactions": self.transactions_vues, "scanner": scanner,
                "detecteur": self.detecteur.statistiques(),
                "poids_minute": round(self.info.poids_par_minute()), "refus": self.info.refus,
                "mids": {c: self.mids[c] for c in self.coins if c in self.mids}}

    def fermer(self) -> None:
        base = getattr(self, "base", None)
        if base is not None:
            base.fermer()
