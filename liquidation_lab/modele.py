"""
Une liquidation, telle que toutes les plateformes la décrivent une fois traduite.

`cote` est toujours le côté de la POSITION liquidée : « long » quand un acheteur
à levier a été liquidé (son ordre de liquidation vend), « short » dans l'autre
cas. Chaque plateforme l'exprime à sa façon ; la traduction est faite par sa
source (dossier `sources/`).

Le prix est donné pour une unité de l'actif, et la quantité en unités de
l'actif : 1000PEPEUSDT (Binance), kPEPE (Hyperliquid) et PEPE-USDT-SWAP (OKX)
se comparent ainsi directement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

COLONNES = ["t", "plateforme", "marche", "actif", "cote", "prix", "quantite", "valeur", "detail"]
LONG, SHORT = "long", "short"


@dataclass(slots=True)
class Liquidation:
    t: int              # heure donnée par la plateforme, en millisecondes (UTC)
    plateforme: str     # clé de config.PLATEFORMES
    marche: str         # symbole du contrat sur la plateforme
    actif: str          # BTC, ETH, PEPE…
    cote: str           # LONG ou SHORT : la position liquidée
    prix: float         # en dollars, pour une unité de l'actif
    quantite: float     # en unités de l'actif (nan si la plateforme ne la donne pas)
    valeur: float       # en dollars
    detail: str = ""    # information propre à la plateforme (adresse Hyperliquid…)

    def ligne(self) -> list:
        quantite = "" if math.isnan(self.quantite) else f"{self.quantite:.10g}"
        return [self.t, self.plateforme, self.marche, self.actif, self.cote,
                f"{self.prix:.10g}", quantite, f"{self.valeur:.2f}", self.detail]


def nombre(texte) -> float:
    """float tolérant : '', None ou un texte illisible donnent nan."""
    try:
        return float(texte)
    except (TypeError, ValueError):
        return math.nan
