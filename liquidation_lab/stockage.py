"""
Écriture et relecture du registre des liquidations.

Un fichier CSV par jour (UTC) dans `donnees/liquidations/`, avec les colonnes
de `modele.COLONNES`. Le collecteur ajoute ses lignes toutes les demi-secondes ;
l'interface relit les fichiers puis suit les lignes ajoutées (`Suivi`), sans
jamais rien effacer.
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import config
from .modele import COLONNES, Liquidation

JOUR_MS = 86_400_000


def jour_utc(t_ms: int) -> str:
    return datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def chemin_jour(jour: str) -> str:
    return os.path.join(config.DOSSIER_LIQUIDATIONS, f"{jour}.csv")


# ----------------------------------------------------------------------
# ÉCRITURE (collecteur)
# ----------------------------------------------------------------------
class Registre:
    """Tampon des liquidations reçues, vidé dans le fichier du jour par `vider()`."""

    def __init__(self):
        config.preparer_dossiers()
        self._tampon: list[Liquidation] = []
        self._fichiers: dict[str, tuple] = {}        # jour -> (fichier, écrivain csv)
        self.ecrites = 0

    def ajouter(self, liquidation: Liquidation) -> None:
        self._tampon.append(liquidation)

    def _ecrivain(self, jour: str):
        if jour not in self._fichiers:
            chemin = chemin_jour(jour)
            nouveau = not os.path.exists(chemin) or os.path.getsize(chemin) == 0
            fichier = open(chemin, "a", newline="", encoding="utf-8")
            ecrivain = csv.writer(fichier)
            if nouveau:
                ecrivain.writerow(COLONNES)
            self._fichiers[jour] = (fichier, ecrivain)
            # on ne garde ouverts que le jour courant et la veille
            for ancien in sorted(self._fichiers)[:-2]:
                self._fichiers.pop(ancien)[0].close()
        return self._fichiers[jour][1]

    def vider(self) -> int:
        if not self._tampon:
            return 0
        lot, self._tampon = self._tampon, []
        jours = set()
        for liq in lot:
            jour = jour_utc(liq.t)
            self._ecrivain(jour).writerow(liq.ligne())
            jours.add(jour)
        for jour in jours:
            self._fichiers[jour][0].flush()
        self.ecrites += len(lot)
        return len(lot)

    def fermer(self) -> None:
        self.vider()
        for fichier, _ in self._fichiers.values():
            fichier.close()
        self._fichiers.clear()


# ----------------------------------------------------------------------
# RELECTURE (interface, ligne de commande)
# ----------------------------------------------------------------------
def jours_entre(t0_ms: int, t1_ms: int) -> list[str]:
    d0 = datetime.fromtimestamp(t0_ms / 1000, tz=timezone.utc).date()
    d1 = datetime.fromtimestamp(t1_ms / 1000, tz=timezone.utc).date()
    jours = []
    while d0 <= d1:
        jours.append(d0.strftime("%Y-%m-%d"))
        d0 += timedelta(days=1)
    return jours


def jours_disponibles() -> list[str]:
    try:
        noms = os.listdir(config.DOSSIER_LIQUIDATIONS)
    except OSError:
        return []
    return sorted(n[:-4] for n in noms if n.endswith(".csv") and len(n) == 14)


def _lire_octets(octets: bytes) -> pd.DataFrame:
    if not octets.strip():
        return pd.DataFrame(columns=COLONNES)
    # tout en texte, puis conversion : la ligne d'en-tête de chaque fichier devient nan et disparaît
    tableau = pd.read_csv(io.BytesIO(octets), header=None, names=COLONNES, dtype=str,
                          keep_default_na=False, on_bad_lines="skip")
    for colonne in ("t", "prix", "quantite", "valeur"):
        tableau[colonne] = pd.to_numeric(tableau[colonne], errors="coerce")
    tableau = tableau.dropna(subset=["t", "valeur"])
    tableau["t"] = tableau["t"].astype("int64")
    return tableau


def lire(t0_ms: int, t1_ms: int | None = None) -> pd.DataFrame:
    """Toutes les liquidations enregistrées entre t0 et t1 (millisecondes)."""
    t1_ms = t1_ms or int(time.time() * 1000)
    morceaux = []
    for jour in jours_entre(t0_ms, t1_ms):
        try:
            with open(chemin_jour(jour), "rb") as f:
                morceaux.append(_lire_octets(f.read()))
        except OSError:
            continue
    if not morceaux:
        return pd.DataFrame(columns=COLONNES)
    tableau = pd.concat(morceaux, ignore_index=True)
    return tableau[(tableau["t"] >= t0_ms) & (tableau["t"] <= t1_ms)].reset_index(drop=True)


class Suivi:
    """
    Lit les fichiers du registre puis rend, à chaque appel de `nouvelles()`, les
    lignes ajoutées depuis. Une ligne incomplète (en cours d'écriture) attend
    l'appel suivant.
    """

    def __init__(self):
        self._positions: dict[str, int] = {}

    def charger(self, t0_ms: int) -> pd.DataFrame:
        """Lit tout depuis t0 et place les curseurs à la fin de ce qui a été lu."""
        maintenant = int(time.time() * 1000)
        morceaux = []
        for jour in jours_entre(t0_ms, maintenant):
            chemin = chemin_jour(jour)
            try:
                with open(chemin, "rb") as f:
                    octets = f.read()
            except OSError:
                continue
            fin = octets.rfind(b"\n") + 1
            self._positions[chemin] = fin
            morceaux.append(_lire_octets(octets[:fin]))
        if not morceaux:
            return pd.DataFrame(columns=COLONNES)
        tableau = pd.concat(morceaux, ignore_index=True)
        return tableau[tableau["t"] >= t0_ms].reset_index(drop=True)

    def nouvelles(self) -> pd.DataFrame | None:
        maintenant = int(time.time() * 1000)
        morceaux = []
        for jour in jours_entre(maintenant - JOUR_MS, maintenant):
            chemin = chemin_jour(jour)
            try:
                taille = os.path.getsize(chemin)
            except OSError:
                continue
            debut = self._positions.get(chemin, 0)
            if taille < debut:          # fichier remplacé : on repart du début
                debut = 0
            if taille == debut:
                continue
            with open(chemin, "rb") as f:
                f.seek(debut)
                octets = f.read(taille - debut)
            fin = octets.rfind(b"\n") + 1
            if fin == 0:
                continue
            self._positions[chemin] = debut + fin
            morceaux.append(_lire_octets(octets[:fin]))
        if not morceaux:
            return None
        return pd.concat(morceaux, ignore_index=True)


# ----------------------------------------------------------------------
# PETITS FICHIERS JSON (état du collecteur)
# ----------------------------------------------------------------------
def ecrire_json(chemin: str, objet) -> bool:
    """Écrit d'un coup (fichier temporaire puis remplacement) ; False si le fichier était occupé."""
    temporaire = chemin + ".tmp"
    with open(temporaire, "w", encoding="utf-8") as f:
        json.dump(objet, f, ensure_ascii=False, separators=(",", ":"))
    for _ in range(5):
        try:
            os.replace(temporaire, chemin)
            return True
        except PermissionError:         # Windows : le lecteur tenait le fichier ouvert
            time.sleep(0.02)
    return False


def lire_json(chemin: str):
    try:
        with open(chemin, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
