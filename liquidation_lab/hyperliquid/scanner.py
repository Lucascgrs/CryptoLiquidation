"""
Lecture des comptes Hyperliquid, pour connaître le prix de liquidation réel de
chaque position.

Hyperliquid n'offre pas de liste de toutes les positions : il faut lire les
comptes un par un (clearinghouseState), au plus 7 à 8 par seconde dans la
limite de débit. Trois files se partagent ce débit :
- « sale » : les comptes qu'on vient de voir trader (flux des transactions),
  les plus gros montants d'abord. Toute position qui change passe par une
  transaction : c'est ce qui garde la base à jour ;
- « échéance » : les relectures prévues. Une position proche de son prix de
  liquidation est relue toutes les minutes, une grosse position toutes les
  5 minutes, une petite toutes les 30 minutes ;
- « nouveau » : les comptes du classement public jamais lus, des plus riches
  aux plus modestes.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from .api import InfoHyperliquid, LimiteHyperliquid
from .base import BaseEcriture

CLASSEMENT_VALEUR_MIN = 1_000          # comptes du classement retenus (dollars)
CLASSEMENT_VALIDITE = 24 * 3600        # le classement est relu au plus une fois par jour
ATTENTE_APRES_TRANSACTION = 2.0        # les ordres partent souvent en plusieurs morceaux
TRAVAILLEURS = 6


@dataclass(slots=True)
class PositionHL:
    actif: str
    taille: float               # signée
    prix_entree: float
    prix_liquidation: float | None
    valeur: float
    levier: float
    marge: str
    marge_utilisee: float
    pnl: float

    @property
    def long(self) -> bool:
        return self.taille > 0

    def ligne(self) -> tuple:
        return (self.actif, self.taille, self.prix_entree, self.prix_liquidation, self.valeur, self.levier,
                self.marge, self.marge_utilisee, self.pnl)


@dataclass(slots=True)
class Compte:
    adresse: str
    origine: str
    valeur_compte: float = 0.0
    lue_le: int = 0                     # ms ; 0 : jamais lu
    prochaine: float = 0.0              # relecture prévue (secondes, epoch)
    notionnel: float = 0.0
    sale: float = 0.0                   # dollars échangés depuis la dernière lecture
    sale_depuis: float = 0.0
    en_cours: bool = False
    positions: dict = field(default_factory=dict)


def _f(x, defaut=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return defaut


def lire_positions(etat: dict) -> list[PositionHL]:
    positions = []
    for bloc in etat.get("assetPositions") or []:
        p = bloc.get("position") or {}
        taille = _f(p.get("szi"))
        if not taille:
            continue
        levier = p.get("leverage") or {}
        liq = p.get("liquidationPx")
        positions.append(PositionHL(
            actif=p.get("coin", "?"), taille=taille, prix_entree=_f(p.get("entryPx")),
            prix_liquidation=_f(liq, None) if liq not in (None, "") else None,
            valeur=abs(_f(p.get("positionValue"))), levier=_f(levier.get("value")),
            marge=levier.get("type", "cross"), marge_utilisee=_f(p.get("marginUsed")),
            pnl=_f(p.get("unrealizedPnl"))))
    return positions


class Scanner:
    def __init__(self, info: InfoHyperliquid, base: BaseEcriture, journal: logging.Logger):
        self.info = info
        self.base = base
        self.journal = journal
        self.comptes: dict[str, Compte] = {}
        self.mids: dict[str, float] = {}            # prix courants, tenus par la source
        self._echeances: list[tuple[float, str]] = []
        self._nouveaux: list[str] = []
        self._i_nouveau = 0
        self._sales: set[str] = set()
        self._sales_triees: list[str] = []
        self._sales_triees_le = 0.0
        self._ordre = itertools.cycle(["sale", "echeance", "sale", "nouveau", "echeance",
                                       "sale", "echeance", "nouveau", "sale", "echeance"])
        self._lectures = deque(maxlen=4000)        # heures des lectures, pour le débit
        self.erreurs = 0
        self.classement = 0

    # ------------------------------------------------------------------
    # CHARGEMENT
    # ------------------------------------------------------------------
    def charger(self) -> None:
        maintenant = time.time()
        for adresse, origine, valeur, lue_le, nb, notionnel in self.base.comptes():
            self.comptes[adresse] = Compte(adresse, origine, valeur or 0.0, lue_le or 0,
                                           notionnel=notionnel or 0.0)
        for (adresse, actif, taille, entree, liq, valeur, levier, marge, utilisee, pnl,
             _valeur_compte, _lue) in self.base.positions():
            compte = self.comptes.get(adresse)
            if compte is not None:
                compte.positions[actif] = PositionHL(actif, taille, entree or 0.0, liq, valeur or 0.0,
                                                     levier or 0.0, marge or "cross", utilisee or 0.0, pnl or 0.0)
        # relectures : d'abord les comptes à positions, les plus anciens d'abord
        for compte in self.comptes.values():
            if compte.lue_le:
                retard = 0 if compte.positions else self._delai(compte)
                compte.prochaine = min(maintenant, compte.lue_le / 1000 + retard)
                heapq.heappush(self._echeances, (compte.prochaine, compte.adresse))
        self._trier_nouveaux()
        self.journal.info("hyperliquid : %d comptes connus, %d déjà lus",
                          len(self.comptes), sum(1 for c in self.comptes.values() if c.lue_le))

    def _trier_nouveaux(self) -> None:
        self._nouveaux = sorted((a for a, c in self.comptes.items() if not c.lue_le),
                                key=lambda a: -self.comptes[a].valeur_compte)
        self._i_nouveau = 0

    async def importer_classement(self) -> None:
        derniere = float(self.base.meta("classement_le", "0") or 0)
        if time.time() - derniere < CLASSEMENT_VALIDITE:
            self.classement = int(self.base.meta("classement_comptes", "0") or 0)
            return
        try:
            lignes = await self.info.classement()
        except Exception as erreur:                     # noqa: BLE001
            self.journal.warning("hyperliquid : classement illisible (%s)", erreur)
            return
        retenues = [(a, "classement", v) for a, v in lignes if v >= CLASSEMENT_VALEUR_MIN]
        self.base.ajouter_adresses(retenues)
        self.base.ecrire_meta("classement_le", str(time.time()))
        self.base.ecrire_meta("classement_comptes", str(len(retenues)))
        self.base.valider()
        for adresse, origine, valeur in retenues:
            compte = self.comptes.get(adresse)
            if compte is None:
                self.comptes[adresse] = Compte(adresse, origine, valeur)
            elif not compte.lue_le:
                compte.valeur_compte = valeur
        self.classement = len(retenues)
        self._trier_nouveaux()
        self.journal.info("hyperliquid : classement importé, %d comptes de plus de %d $",
                          len(retenues), CLASSEMENT_VALEUR_MIN)

    # ------------------------------------------------------------------
    # SIGNAUX VENUS DU FLUX
    # ------------------------------------------------------------------
    def transaction(self, adresse: str, notionnel: float, t: float) -> None:
        compte = self.comptes.get(adresse)
        if compte is None:
            compte = self.comptes[adresse] = Compte(adresse, "transactions")
        compte.sale += notionnel
        compte.sale_depuis = t
        self._sales.add(adresse)

    def relire_vite(self, adresse: str) -> None:
        """Après une liquidation : relire le compte au plus tôt."""
        compte = self.comptes.get(adresse)
        if compte is not None:
            compte.sale += 1e12
            compte.sale_depuis = time.time()
            self._sales.add(adresse)

    def position(self, adresse: str, actif: str) -> PositionHL | None:
        compte = self.comptes.get(adresse)
        return compte.positions.get(actif) if compte else None

    # ------------------------------------------------------------------
    # CHOIX DU COMPTE SUIVANT
    # ------------------------------------------------------------------
    def _suivant(self) -> Compte | None:
        maintenant = time.time()
        premiere = next(self._ordre)
        for file in [premiere] + [f for f in ("sale", "echeance", "nouveau") if f != premiere]:
            compte = getattr(self, f"_prendre_{file}")(maintenant)
            if compte is not None:
                return compte
        return None

    def _prendre_sale(self, maintenant: float) -> Compte | None:
        if maintenant - self._sales_triees_le > 0.5:
            pretes = [a for a in self._sales
                      if maintenant - self.comptes[a].sale_depuis >= ATTENTE_APRES_TRANSACTION]
            pretes.sort(key=lambda a: self.comptes[a].sale)
            self._sales_triees, self._sales_triees_le = pretes, maintenant
        while self._sales_triees:
            compte = self.comptes[self._sales_triees.pop()]
            if compte.en_cours or compte.sale <= 0 or compte.adresse not in self._sales:
                continue
            # un compte déjà lu il y a peu, pour un petit montant, peut attendre son tour
            if compte.lue_le and compte.sale < 2_000 and maintenant - compte.lue_le / 1000 < 600:
                continue
            return compte
        return None

    def _prendre_echeance(self, maintenant: float) -> Compte | None:
        while self._echeances and self._echeances[0][0] <= maintenant:
            prochaine, adresse = heapq.heappop(self._echeances)
            compte = self.comptes.get(adresse)
            if compte is None or compte.en_cours or abs(compte.prochaine - prochaine) > 1e-6:
                continue
            return compte
        return None

    def _prendre_nouveau(self, maintenant: float) -> Compte | None:
        while self._i_nouveau < len(self._nouveaux):
            compte = self.comptes[self._nouveaux[self._i_nouveau]]
            self._i_nouveau += 1
            if not compte.lue_le and not compte.en_cours:
                return compte
        return None

    @staticmethod
    def _delai(compte: Compte) -> float:
        if not compte.positions:
            return 6 * 3600
        delai = 1800 if compte.notionnel < 1e5 else 900 if compte.notionnel < 1e6 else 300
        distance = None
        for p in compte.positions.values():
            if p.prix_liquidation and p.taille:
                marque = p.valeur / abs(p.taille)
                if marque > 0:
                    d = abs(marque - p.prix_liquidation) / marque
                    distance = d if distance is None else min(distance, d)
        if distance is not None:
            if distance < 0.02:
                delai = min(delai, 60)
            elif distance < 0.05:
                delai = min(delai, 180)
            elif distance < 0.10:
                delai = min(delai, 600)
        return delai

    def _planifier(self, compte: Compte, delai: float) -> None:
        compte.prochaine = time.time() + delai
        heapq.heappush(self._echeances, (compte.prochaine, compte.adresse))

    # ------------------------------------------------------------------
    # LECTURE
    # ------------------------------------------------------------------
    async def tourner(self) -> None:
        self.charger()
        await self.importer_classement()
        await asyncio.gather(self._surveiller_proximite(), *(self._travailleur() for _ in range(TRAVAILLEURS)))

    async def _travailleur(self) -> None:
        while True:
            compte = self._suivant()
            if compte is None:
                await asyncio.sleep(0.5)
                continue
            compte.en_cours = True
            try:
                await self._lire(compte)
            except LimiteHyperliquid:
                self._planifier(compte, 30)
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception as erreur:                 # noqa: BLE001
                self.erreurs += 1
                if self.erreurs <= 5 or self.erreurs % 200 == 0:
                    self.journal.warning("hyperliquid : lecture de %s impossible (%s)", compte.adresse, erreur)
                self._planifier(compte, 300)
            finally:
                compte.en_cours = False

    async def _lire(self, compte: Compte) -> None:
        sale_avant = compte.sale
        etat = await self.info.compte(compte.adresse)
        positions = lire_positions(etat)
        lue_le = int(etat.get("time") or time.time() * 1000)
        resume = etat.get("marginSummary") or {}
        compte.valeur_compte = _f(resume.get("accountValue"))
        compte.positions = {p.actif: p for p in positions}
        compte.notionnel = sum(p.valeur for p in positions)
        compte.lue_le = lue_le
        compte.sale = max(0.0, compte.sale - sale_avant)     # ce qui a été échangé pendant la lecture reste à lire
        if compte.sale <= 0:
            self._sales.discard(compte.adresse)
        self.base.remplacer(compte.adresse, compte.valeur_compte, [p.ligne() for p in positions], lue_le)
        self._planifier(compte, self._delai(compte))
        self._lectures.append(time.time())

    async def _surveiller_proximite(self) -> None:
        """Toutes les 10 s : une position que le prix vient d'approcher (moins de 2 %) est relue."""
        while True:
            await asyncio.sleep(10)
            if not self.mids:
                continue
            maintenant = time.time()
            for compte in self.comptes.values():
                if not compte.positions or compte.en_cours or maintenant - compte.lue_le / 1000 < 60:
                    continue
                for p in compte.positions.values():
                    prix = self.mids.get(p.actif)
                    if prix and p.prix_liquidation and abs(prix - p.prix_liquidation) / prix < 0.02:
                        if compte.prochaine > maintenant + 1:
                            self._planifier(compte, 0)
                        break

    # ------------------------------------------------------------------
    def statistiques(self) -> dict:
        """Recalculées au plus toutes les 5 s (elles parcourent tous les comptes)."""
        maintenant = time.time()
        cache = getattr(self, "_stats", None)
        if cache and maintenant - cache[0] < 5:
            return cache[1]
        stats = {
            "comptes": len(self.comptes),
            "lus": sum(1 for c in self.comptes.values() if c.lue_le),
            "avec_positions": sum(1 for c in self.comptes.values() if c.positions),
            "positions": sum(len(c.positions) for c in self.comptes.values()),
            "file_sale": len(self._sales),
            "file_echeance": sum(1 for p, _ in self._echeances if p <= maintenant),
            "file_nouveau": max(0, len(self._nouveaux) - self._i_nouveau),
            "lectures_minute": sum(1 for t in self._lectures if maintenant - t < 60),
            "erreurs": self.erreurs, "classement": self.classement,
        }
        self._stats = (maintenant, stats)
        return stats
