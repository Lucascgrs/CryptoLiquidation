"""
Chemins, réglages enregistrés et description des plateformes.

Tout ce que le collecteur écrit vit dans `donnees/` :
- `liquidations/AAAA-MM-JJ.csv` : une ligne par liquidation, un fichier par jour (UTC) ;
- `hyperliquid.sqlite` : les adresses et les positions Hyperliquid lues ;
- `etat_collecteur.json` : l'état du collecteur, réécrit chaque seconde ;
- `collecteur.log` : son journal.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DONNEES = os.path.join(RACINE, "donnees")
DOSSIER_LIQUIDATIONS = os.path.join(DONNEES, "liquidations")
BASE_HYPERLIQUID = os.path.join(DONNEES, "hyperliquid.sqlite")
CLASSEMENT_HYPERLIQUID = os.path.join(DONNEES, "classement_hyperliquid.json.gz")
FICHIER_ETAT = os.path.join(DONNEES, "etat_collecteur.json")
FICHIER_VERROU = os.path.join(DONNEES, "collecteur.verrou")
FICHIER_ARRET = os.path.join(DONNEES, "collecteur.arret")
FICHIER_JOURNAL = os.path.join(DONNEES, "collecteur.log")
FICHIER_REGLAGES = os.path.join(DONNEES, "reglages.json")


def preparer_dossiers() -> None:
    os.makedirs(DOSSIER_LIQUIDATIONS, exist_ok=True)


# ----------------------------------------------------------------------
# PLATEFORMES
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Plateforme:
    cle: str
    nom: str
    sigle: str          # trois lettres, pour les étiquettes
    couleur: str        # pastille d'identification (le texte reste toujours écrit)
    couverture: str     # ce que la plateforme publie réellement
    complete: bool      # toutes les liquidations, ou seulement une partie


PLATEFORMES: dict[str, Plateforme] = {p.cle: p for p in (
    Plateforme("binance", "Binance", "BIN", "#f0b90b",
               "Contrats USDT et USDC. Binance ne publie que la plus grosse liquidation de chaque "
               "contrat par seconde : les autres n'apparaissent nulle part.", False),
    Plateforme("bybit", "Bybit", "BYB", "#f7a600",
               "Toutes les liquidations (contrats USDT, USDC et inverses), regroupées toutes "
               "les 500 ms.", True),
    Plateforme("okx", "OKX", "OKX", "#c9cdd4",
               "Le flux en direct n'en publie qu'environ la moitié : il est complété toutes les 15 s par "
               "l'API REST (les 100 dernières de chaque contrat). Lors des plus fortes cascades, une partie "
               "peut encore manquer.", True),
    Plateforme("bitget", "Bitget", "BGT", "#4fd1e8",
               "Seulement la plus grosse liquidation long et la plus grosse short de chaque "
               "contrat, par seconde.", False),
    Plateforme("gate", "Gate", "GTE", "#5b7cfa",
               "Toutes les liquidations des contrats USDT, regroupées par seconde.", True),
    Plateforme("htx", "HTX", "HTX", "#7fb2ff",
               "Les liquidations publiées par HTX sur les contrats USDT. HTX ne dit pas si son flux "
               "les contient toutes.", True),
    Plateforme("kraken", "Kraken", "KRK", "#a78bfa",
               "Toutes les transactions de liquidation, totale ou partielle (contrats PF_).", True),
    Plateforme("deribit", "Deribit", "DRB", "#f472b6",
               "Toutes les transactions marquées « liquidation » (perpétuels et contrats à terme).", True),
    Plateforme("dydx", "dYdX", "DYX", "#8b8cff",
               "Toutes les transactions de liquidation.", True),
    Plateforme("hyperliquid", "Hyperliquid", "HYP", "#97fce4",
               "Hyperliquid ne signale pas les liquidations dans son flux public. Elles sont "
               "reconnues et confirmées une à une : par les coffres HLP qui les reçoivent, par le "
               "prix de liquidation connu de la position, ou par une vérification. Les petites "
               "liquidations de comptes jamais lus peuvent manquer.", False),
)}
ORDRE_PLATEFORMES = list(PLATEFORMES)


# ----------------------------------------------------------------------
# RÉGLAGES (partagés par l'interface et le collecteur)
# ----------------------------------------------------------------------
REGLAGES_DEFAUT = {
    "sources": {cle: True for cle in ORDRE_PLATEFORMES},
    "positions_hyperliquid": True,      # lire les positions (page « Niveaux »)
    "arreter_en_fermant": True,         # arrêter la collecte lancée par l'interface à sa fermeture
    "poids_minute_hyperliquid": 1100,   # budget de l'API Hyperliquid (limite : 1200 par minute et par IP)
}


def charger_reglages() -> dict:
    reglages = json.loads(json.dumps(REGLAGES_DEFAUT))
    try:
        with open(FICHIER_REGLAGES, encoding="utf-8") as f:
            lus = json.load(f)
    except (OSError, ValueError):
        return reglages
    for cle, valeur in lus.items():
        if isinstance(valeur, dict) and isinstance(reglages.get(cle), dict):
            reglages[cle].update(valeur)
        else:
            reglages[cle] = valeur
    return reglages


def enregistrer_reglages(reglages: dict) -> None:
    preparer_dossiers()
    temporaire = FICHIER_REGLAGES + ".tmp"
    with open(temporaire, "w", encoding="utf-8") as f:
        json.dump(reglages, f, ensure_ascii=False, indent=2)
    os.replace(temporaire, FICHIER_REGLAGES)
