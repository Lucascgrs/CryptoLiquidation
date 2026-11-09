"""
CryptoLiquidation — ligne de commande.

    python main.py collecter                  la collecte, dans la console (Ctrl+C pour arrêter)
    python main.py collecter --duree 2h       … pendant deux heures
    python main.py etat                       le collecteur tourne-t-il ? que contient la base ?
    python main.py arreter                    arrête le collecteur en marche
    python main.py liquidations --depuis 1h   bilan des liquidations enregistrées
    python main.py positions --crypto BTC     niveaux de liquidation réels (Hyperliquid)

L'interface graphique : python Dashboard.py
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time

RACINE = os.path.dirname(os.path.abspath(__file__))
os.chdir(RACINE)
if RACINE not in sys.path:
    sys.path.insert(0, RACINE)

from liquidation_lab import config  # noqa: E402
from liquidation_lab.formats import dollars, entier, prix  # noqa: E402

UNITES = {"s": 1, "m": 60, "h": 3600, "j": 86400, "d": 86400}


def duree(texte: str) -> float:
    """« 90s », « 30m », « 2h », « 1j » -> secondes."""
    trouve = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*([smhjd]?)\s*", texte.lower())
    if not trouve:
        raise argparse.ArgumentTypeError(f"durée illisible : {texte} (exemples : 90s, 30m, 2h, 1j)")
    return float(trouve.group(1).replace(",", ".")) * UNITES[trouve.group(2) or "s"]


def commande_collecter(args) -> int:
    from liquidation_lab.collecteur import Collecteur, configurer_journal

    reglages = config.charger_reglages()
    if args.sources:
        sources = [s.strip().lower() for s in args.sources.split(",") if s.strip()]
        inconnues = [s for s in sources if s not in config.PLATEFORMES]
        if inconnues:
            print(f"Plateformes inconnues : {', '.join(inconnues)}. Possibles : {', '.join(config.PLATEFORMES)}")
            return 2
    else:
        sources = [c for c, actif in reglages["sources"].items() if actif]
    positions = reglages.get("positions_hyperliquid", True) and not args.sans_positions
    journal = configurer_journal(console=not args.depuis_interface)
    collecteur = Collecteur(sources, positions, float(reglages.get("poids_minute_hyperliquid", 1100)),
                            args.duree, journal, resume_console=not args.depuis_interface)
    return collecteur.executer()


def commande_etat(_args) -> int:
    from liquidation_lab import stockage
    from liquidation_lab.collecteur import etat_collecteur

    etat = etat_collecteur()
    if etat:
        depuis = (time.time() * 1000 - etat["debut"]) / 60000
        print(f"Collecteur en marche (processus {etat['pid']}) depuis {depuis:.0f} min, "
              f"{etat['registre']['ecrites']} liquidations enregistrées.")
        for cle, e in etat["sources"].items():
            liaison = "connectée" if e["connexions"] == e["prevues"] and e["prevues"] else "DÉCONNECTÉE"
            print(f"  {config.PLATEFORMES[cle].nom:<12} {liaison:<12} {e['liquidations']:>7} liquidations "
                  f"{dollars(e['valeur']):>12}" + (f"   dernière erreur : {e['erreur']}" if e["erreur"] else ""))
    else:
        print("Aucun collecteur en marche.")
    jours = stockage.jours_disponibles()
    if jours:
        print(f"Registre : {len(jours)} jour(s), du {jours[0]} au {jours[-1]}.")
    if os.path.exists(config.BASE_HYPERLIQUID):
        from liquidation_lab.hyperliquid import base
        s = base.statistiques()
        derniere = f", dernière lecture il y a {(s['maintenant'] - s['derniere_lecture']) / 60000:.0f} min" \
            if s["derniere_lecture"] else ""
        print(f"Hyperliquid : {entier(s['adresses'])} comptes connus, {entier(s['lues'])} lus, "
              f"{entier(s['positions'])} positions sur {entier(s['comptes_avec_positions'])} comptes{derniere}.")
    return 0


def commande_arreter(_args) -> int:
    from liquidation_lab.collecteur import demander_arret, etat_collecteur

    if not etat_collecteur():
        print("Aucun collecteur en marche.")
        return 0
    demander_arret()
    print("Arrêt demandé : le collecteur s'arrête dans la seconde.")
    return 0


def commande_liquidations(args) -> int:
    from liquidation_lab import stockage

    fin = int(time.time() * 1000)
    tableau = stockage.lire(fin - int(args.depuis * 1000), fin)
    if args.crypto:
        tableau = tableau[tableau["actif"] == args.crypto.upper()]
    if tableau.empty:
        print("Aucune liquidation enregistrée sur cette période.")
        return 0
    longs = tableau.loc[tableau["cote"] == "long", "valeur"].sum()
    shorts = tableau.loc[tableau["cote"] == "short", "valeur"].sum()
    print(f"{len(tableau)} liquidations, {dollars(longs + shorts)} : longs {dollars(longs)}, shorts {dollars(shorts)}")
    print("\nPar plateforme :")
    for cle, groupe in tableau.groupby("plateforme"):
        print(f"  {config.PLATEFORMES.get(cle, cle).nom if cle in config.PLATEFORMES else cle:<12} "
              f"{len(groupe):>7}  {dollars(groupe['valeur'].sum()):>12}")
    print("\nPar crypto :")
    for actif, valeur in tableau.groupby("actif")["valeur"].sum().sort_values(ascending=False).head(10).items():
        print(f"  {actif:<10} {dollars(valeur):>12}")
    print("\nLes plus grosses :")
    for _, ligne in tableau.nlargest(10, "valeur").iterrows():
        heure = time.strftime("%d/%m %H:%M:%S", time.localtime(ligne["t"] / 1000))
        print(f"  {heure}  {ligne['plateforme']:<11} {ligne['actif']:<8} {ligne['cote']:<5} "
              f"{dollars(ligne['valeur']):>12} à {prix(ligne['prix'])} $")
    return 0


def commande_positions(args) -> int:
    if not os.path.exists(config.BASE_HYPERLIQUID):
        print("Aucune position lue pour l'instant : lance d'abord la collecte.")
        return 0
    from liquidation_lab.hyperliquid import base
    from liquidation_lab.niveaux import Carte

    actif = args.crypto if args.crypto.startswith("k") else args.crypto.upper()
    positions = base.lire_positions(actif)
    marches = base.lire_marches().set_index("actif")
    if positions.empty or actif not in marches.index:
        print(f"Aucune position connue sur {actif}.")
        return 0
    carte = Carte(positions, float(marches.loc[actif, "prix"]), float(marches.loc[actif, "open_interest"]))
    print(carte.texte(args.plage / 100))
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parseur = argparse.ArgumentParser(description="CryptoLiquidation : liquidations en direct et niveaux réels.")
    sous = parseur.add_subparsers(dest="commande", required=True)

    p = sous.add_parser("collecter", help="lancer la collecte")
    p.add_argument("--duree", type=duree, help="arrêt automatique au bout de cette durée (30m, 2h…)")
    p.add_argument("--sources", help=f"plateformes, séparées par des virgules ({', '.join(config.PLATEFORMES)})")
    p.add_argument("--sans-positions", action="store_true", help="ne pas lire les positions Hyperliquid")
    p.add_argument("--depuis-interface", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(fonction=commande_collecter)

    sous.add_parser("etat", help="état du collecteur et des données").set_defaults(fonction=commande_etat)
    sous.add_parser("arreter", help="arrêter le collecteur en marche").set_defaults(fonction=commande_arreter)

    p = sous.add_parser("liquidations", help="bilan des liquidations enregistrées")
    p.add_argument("--depuis", type=duree, default=3600, help="période (défaut : 1h)")
    p.add_argument("--crypto", help="une seule crypto (BTC, ETH…)")
    p.set_defaults(fonction=commande_liquidations)

    p = sous.add_parser("positions", help="niveaux de liquidation réels d'une crypto (Hyperliquid)")
    p.add_argument("--crypto", default="BTC")
    p.add_argument("--plage", type=float, default=10, help="écart au prix, en %% (défaut : 10)")
    p.set_defaults(fonction=commande_positions)

    args = parseur.parse_args()
    return args.fonction(args)


if __name__ == "__main__":
    sys.exit(main())
