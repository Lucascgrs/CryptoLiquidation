"""
Reconnaissance des liquidations dans le flux public d'Hyperliquid.

Le flux des transactions d'Hyperliquid ne dit pas lesquelles sont des
liquidations ; l'agresseur de chaque transaction est en revanche connu
(`users` = [acheteur, vendeur], `side` = côté de l'agresseur). Une liquidation
est un ordre au marché envoyé au nom du liquidé : il en est l'agresseur, et
toutes ses transactions de cette liquidation partagent le même hash.

Une liquidation n'est publiée que si l'un de ces trois indices la confirme :
1. « HLP » : les coffres HLP et quelques gros teneurs de marché sont suivis en
   direct. Leurs transactions portent le champ `liquidation` (le liquidé, la
   méthode) quand l'autre partie est liquidée. La taille complète est la somme
   des transactions du liquidé dans ce hash.
2. « prix » : une liquidation est déclenchée par la mise à jour des prix de
   marque, qui déclenche dans le même bloc les stops et les autres
   liquidations : un même hash, plusieurs agresseurs. Si l'agresseur ferme une
   position connue (lue par le scanner) à un prix qui a franchi son prix de
   liquidation, c'est elle.
3. « vérif » : sinon, pour un montant notable, l'historique de l'agresseur est
   lu (userFillsByTime), où la plateforme marque la liquidation.

Un stop déclenché dans le même bloc ressemble à une liquidation : c'est pour
cela qu'aucun cas douteux n'est publié sans la vérification 3.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from ..modele import LONG, SHORT, Liquidation
from ..symboles import normaliser
from .api import InfoHyperliquid, LimiteHyperliquid
from .scanner import Scanner

HASH_NUL = "0x" + "0" * 64          # tranches de TWAP : jamais des liquidations
ATTENTE = 3.0                       # secondes avant d'examiner un hash (les messages arrivent en désordre)
GARDE = 60.0                        # durée de conservation des hash examinés
SEUIL_VERIFICATION = 2_000          # dollars : en dessous, un cas inconnu n'est pas vérifié
MARGE_DOUTE = 0.003                 # 0,3 % au-dessus du prix de liquidation : cas douteux, vérifié
DUREE_CANDIDAT = 180.0              # au-delà, une vérification en attente est abandonnée


@dataclass(slots=True)
class Groupe:
    """Les transactions d'un même agresseur, sur une même crypto, dans un même hash."""
    adresse: str
    coin: str
    cote: str                       # côté de la position fermée : l'agresseur vend -> LONG
    taille: float = 0.0
    notionnel: float = 0.0
    t: int = 0

    @property
    def prix(self) -> float:
        return self.notionnel / self.taille if self.taille else 0.0


@dataclass(slots=True)
class Paquet:
    premiere: float
    transactions: list = field(default_factory=list)
    groupes: dict | None = None     # rempli à l'examen


@dataclass(slots=True)
class Signal:
    """Une transaction d'un compte suivi marquée « liquidation »."""
    methode: str
    recu: float
    fills: list = field(default_factory=list)
    resolu: bool = False


class Detecteur:
    def __init__(self, scanner: Scanner, info: InfoHyperliquid, emettre: Callable[[Liquidation], None],
                 journal: logging.Logger):
        self.scanner = scanner
        self.info = info
        self._emettre = emettre
        self.journal = journal
        self.suivis: dict[str, str] = {}            # adresse -> nom (coffres HLP, teneurs de marché)
        self.liquidateurs: set[str] = set()         # coffres qui reprennent les positions (« backstop »)
        self._paquets: dict[str, Paquet] = {}
        self._signaux: dict[tuple[str, str], Signal] = {}
        self._emis: dict[tuple[str, str, str], float] = {}
        self._candidats: dict[tuple[str, str], tuple[float, float, int]] = {}   # -> (notionnel, reçu, t)
        self._sans_suite: dict[tuple[str, str], float] = {}
        self.stats = Counter()

    # ------------------------------------------------------------------
    # ENTRÉES
    # ------------------------------------------------------------------
    def transactions(self, lot: list[dict]) -> None:
        maintenant = time.monotonic()
        for t in lot:
            h = t.get("hash")
            if not h or h == HASH_NUL:
                continue
            paquet = self._paquets.get(h)
            if paquet is None:
                paquet = self._paquets[h] = Paquet(maintenant)
            if paquet.groupes is None:
                paquet.transactions.append(t)
            else:                                   # transaction tardive d'un hash déjà examiné
                self._ajouter(paquet.groupes, t)

    def fills_suivis(self, adresse: str, fills: list[dict]) -> None:
        maintenant = time.monotonic()
        self.stats["fills_suivis"] += len(fills)
        for f in fills:
            info = f.get("liquidation")
            if not info or not info.get("liquidatedUser"):
                continue
            cle = (f["hash"], info["liquidatedUser"].lower())
            signal = self._signaux.get(cle)
            if signal is None:
                self.stats["signaux"] += 1
                signal = self._signaux[cle] = Signal(info.get("method", "?"), maintenant)
            signal.fills.append({**f, "_suivi": adresse})

    # ------------------------------------------------------------------
    # EXAMEN
    # ------------------------------------------------------------------
    @staticmethod
    def _ajouter(groupes: dict, t: dict) -> None:
        acheteur, vendeur = t["users"]
        achat = t["side"] == "B"
        adresse = acheteur if achat else vendeur
        cle = (adresse, t["coin"])
        groupe = groupes.get(cle)
        if groupe is None:
            groupe = groupes[cle] = Groupe(adresse, t["coin"], SHORT if achat else LONG, t=int(t["time"]))
        taille = float(t["sz"])
        groupe.taille += taille
        groupe.notionnel += taille * float(t["px"])
        groupe.t = min(groupe.t, int(t["time"]))

    def _examiner(self, h: str, paquet: Paquet) -> None:
        groupes: dict = {}
        for t in paquet.transactions:
            self._ajouter(groupes, t)
        paquet.groupes, paquet.transactions = groupes, []
        agresseurs = {a for a, _ in groupes}
        signales = {a for a in agresseurs if (h, a) in self._signaux}
        systeme = len(agresseurs) >= 2 or bool(signales)
        for (adresse, coin), groupe in groupes.items():
            if adresse in self.liquidateurs or (h, adresse, coin) in self._emis:
                continue
            if adresse in signales:
                continue                            # publié par la résolution du signal
            position = self.scanner.position(adresse, coin)
            if position is not None and position.prix_liquidation and (position.long == (groupe.cote == LONG)):
                liq = position.prix_liquidation
                ecart = (groupe.prix - liq) / liq if groupe.cote == LONG else (liq - groupe.prix) / liq
                if ecart <= 0:
                    self._publier(h, groupe, "?", "prix")
                elif systeme and ecart <= MARGE_DOUTE:
                    self._proposer(h, groupe)
                continue
            if systeme and groupe.notionnel >= SEUIL_VERIFICATION and position is None:
                self._proposer(h, groupe)

    def _proposer(self, h: str, groupe: Groupe) -> None:
        cle = (h, groupe.adresse)
        if cle in self._sans_suite:
            return
        notionnel, recu, t = self._candidats.get(cle, (0.0, time.monotonic(), groupe.t))
        self._candidats[cle] = (notionnel + groupe.notionnel, recu, min(t, groupe.t))
        self.stats["candidats"] += 1

    def _resoudre(self, cle: tuple[str, str], signal: Signal, paquet: Paquet | None) -> None:
        h, liquide = cle
        signal.resolu = True
        self._candidats.pop(cle, None)
        trouves = [g for (a, _), g in (paquet.groupes or {}).items() if a == liquide] if paquet else []
        if signal.methode == "backstop" or not trouves:
            # reprise par le coffre liquidateur, ou transactions publiques manquées :
            # la liquidation se lit dans les transactions du compte suivi
            par_coin: dict[str, Groupe] = {}
            for f in signal.fills:
                sens = f.get("dir", "")
                if sens.startswith("Liquidated"):       # « Liquidated Cross Long » : la position reprise
                    cote = LONG if sens.endswith("Long") else SHORT
                else:                                   # le compte suivi a servi le liquidé : il achète s'il vend
                    cote = LONG if f["side"] == "B" else SHORT
                g = par_coin.get(f["coin"])
                if g is None:
                    g = par_coin[f["coin"]] = Groupe(liquide, f["coin"], cote, t=int(f["time"]))
                taille = float(f["sz"])
                g.taille += taille
                g.notionnel += taille * float(f["px"])
            for g in par_coin.values():
                self._publier(h, g, signal.methode, "HLP" if trouves or signal.methode == "backstop" else "HLP partiel")
            return
        for g in trouves:
            self._publier(h, g, signal.methode, "HLP")

    def _publier(self, h: str, groupe: Groupe, methode: str, preuve: str) -> None:
        cle = (h, groupe.adresse, groupe.coin)
        if cle in self._emis or groupe.taille <= 0:
            return
        self._emis[cle] = time.monotonic()
        actif, paquet = normaliser(groupe.coin)
        self.stats[f"preuve_{preuve}"] += 1
        self.stats["liquidations"] += 1
        self._emettre(Liquidation(
            t=groupe.t, plateforme="hyperliquid", marche=groupe.coin, actif=actif, cote=groupe.cote,
            prix=groupe.prix / paquet, quantite=groupe.taille * paquet, valeur=groupe.notionnel,
            detail=f"{groupe.adresse} {methode} {preuve}"))
        self.scanner.relire_vite(groupe.adresse)

    # ------------------------------------------------------------------
    # BOUCLES
    # ------------------------------------------------------------------
    async def tourner(self) -> None:
        await asyncio.gather(self._boucle_examen(), self._boucle_verification())

    async def _boucle_examen(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            maintenant = time.monotonic()
            for h, paquet in list(self._paquets.items()):
                age = maintenant - paquet.premiere
                if paquet.groupes is None and age >= ATTENTE:
                    self._examiner(h, paquet)
                elif age > GARDE:
                    del self._paquets[h]
            for cle, signal in list(self._signaux.items()):
                if signal.resolu:
                    if maintenant - signal.recu > GARDE:
                        del self._signaux[cle]
                    continue
                paquet = self._paquets.get(cle[0])
                if paquet is not None and paquet.groupes is not None:
                    self._resoudre(cle, signal, paquet)
                elif signal.methode == "backstop" and maintenant - signal.recu > 1.0:
                    self._resoudre(cle, signal, None)
                elif maintenant - signal.recu > ATTENTE + 3:
                    self._resoudre(cle, signal, paquet)
            for dico in (self._emis, self._sans_suite):
                for cle, t in list(dico.items()):
                    if maintenant - t > GARDE * 3:
                        del dico[cle]
            for cle, (_, recu, _) in list(self._candidats.items()):
                if maintenant - recu > DUREE_CANDIDAT:
                    del self._candidats[cle]
                    self.stats["abandonnes"] += 1

    async def _boucle_verification(self) -> None:
        while True:
            if not self._candidats:
                await asyncio.sleep(0.5)
                continue
            cle = max(self._candidats, key=lambda c: self._candidats[c][0])
            notionnel, _, t = self._candidats.pop(cle)
            h, adresse = cle
            try:
                fills = await self.info.transactions(adresse, t - 2000, t + 5000)
            except LimiteHyperliquid:
                await asyncio.sleep(5)
                continue
            except asyncio.CancelledError:
                raise
            except Exception as erreur:                 # noqa: BLE001
                self.stats["erreurs_verification"] += 1
                self.journal.debug("hyperliquid : vérification de %s impossible (%s)", adresse, erreur)
                continue
            self.stats["verifications"] += 1
            par_coin: dict[str, Groupe] = {}
            methode = "?"
            for f in fills or []:
                info = f.get("liquidation")
                if f.get("hash") != h or not info:
                    continue
                methode = info.get("method", "?")
                g = par_coin.get(f["coin"])
                if g is None:
                    g = par_coin[f["coin"]] = Groupe(adresse, f["coin"], LONG if f["side"] == "A" else SHORT,
                                                     t=int(f["time"]))
                taille = float(f["sz"])
                g.taille += taille
                g.notionnel += taille * float(f["px"])
            if par_coin:
                for g in par_coin.values():
                    self._publier(h, g, methode, "vérif")
            else:
                self.stats["fausses_pistes"] += 1
                self._sans_suite[cle] = time.monotonic()

    def statistiques(self) -> dict:
        return {**self.stats, "en_attente": len(self._candidats), "suivis": len(self.suivis),
                "hash_en_memoire": len(self._paquets)}
