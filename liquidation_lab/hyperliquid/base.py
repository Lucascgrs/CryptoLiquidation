"""
Base SQLite des comptes et positions Hyperliquid (`donnees/hyperliquid.sqlite`).

Le collecteur l'écrit ; l'interface la lit en même temps (mode WAL). Chaque
lecture d'un compte remplace toutes ses positions : la base reflète donc
l'état de chaque compte à l'heure de sa dernière lecture (`lue_le`).
"""

from __future__ import annotations

import sqlite3
import time

import pandas as pd

from .. import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS adresses (
    adresse        TEXT PRIMARY KEY,
    origine        TEXT NOT NULL,       -- classement, transactions
    valeur_compte  REAL,
    lue_le         INTEGER,             -- dernière lecture (ms), NULL : jamais lue
    nb_positions   INTEGER DEFAULT 0,
    notionnel      REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS positions (
    adresse          TEXT NOT NULL,
    actif            TEXT NOT NULL,     -- nom Hyperliquid : BTC, ETH, kPEPE…
    taille           REAL NOT NULL,     -- signée : > 0 long, < 0 short
    prix_entree      REAL,
    prix_liquidation REAL,              -- NULL : la position n'a pas de prix de liquidation
    valeur           REAL,              -- valeur de la position, en dollars
    levier           REAL,
    marge            TEXT,              -- cross ou isolated
    marge_utilisee   REAL,
    pnl              REAL,
    valeur_compte    REAL,
    lue_le           INTEGER NOT NULL,
    PRIMARY KEY (adresse, actif)
);
CREATE INDEX IF NOT EXISTS positions_par_actif ON positions(actif);
CREATE TABLE IF NOT EXISTS marches (
    actif          TEXT PRIMARY KEY,
    prix           REAL,                -- prix de marque
    open_interest  REAL,                -- en unités de l'actif
    maj            INTEGER
);
CREATE TABLE IF NOT EXISTS meta (cle TEXT PRIMARY KEY, valeur TEXT);
"""


def ouvrir(lecture_seule: bool = False) -> sqlite3.Connection:
    config.preparer_dossiers()
    if lecture_seule:
        connexion = sqlite3.connect(f"file:{config.BASE_HYPERLIQUID}?mode=ro", uri=True, timeout=10,
                                    check_same_thread=False)
    else:
        connexion = sqlite3.connect(config.BASE_HYPERLIQUID, timeout=30, check_same_thread=False)
        connexion.execute("PRAGMA journal_mode=WAL")
        connexion.execute("PRAGMA synchronous=NORMAL")
        connexion.executescript(SCHEMA)
        connexion.commit()
    return connexion


class BaseEcriture:
    """Côté collecteur : les écritures sont groupées et validées par `valider()` (chaque seconde)."""

    def __init__(self):
        self.cx = ouvrir()
        self._en_attente = 0

    def meta(self, cle: str, defaut: str | None = None) -> str | None:
        ligne = self.cx.execute("SELECT valeur FROM meta WHERE cle = ?", (cle,)).fetchone()
        return ligne[0] if ligne else defaut

    def ecrire_meta(self, cle: str, valeur: str) -> None:
        self.cx.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (cle, valeur))
        self._en_attente += 1

    def comptes(self) -> list[tuple]:
        return self.cx.execute(
            "SELECT adresse, origine, valeur_compte, lue_le, nb_positions, notionnel FROM adresses").fetchall()

    def positions(self) -> list[tuple]:
        return self.cx.execute(
            "SELECT adresse, actif, taille, prix_entree, prix_liquidation, valeur, levier, marge, "
            "marge_utilisee, pnl, valeur_compte, lue_le FROM positions").fetchall()

    def ajouter_adresses(self, lignes: list[tuple[str, str, float]]) -> None:
        """(adresse, origine, valeur du compte) ; met à jour la valeur des adresses déjà connues."""
        self.cx.executemany(
            "INSERT INTO adresses (adresse, origine, valeur_compte) VALUES (?, ?, ?) "
            "ON CONFLICT(adresse) DO UPDATE SET valeur_compte = COALESCE(excluded.valeur_compte, valeur_compte)",
            lignes)
        self._en_attente += len(lignes)

    def remplacer(self, adresse: str, valeur_compte: float, positions: list[tuple], lue_le: int) -> None:
        notionnel = sum(abs(p[4]) for p in positions)
        self.cx.execute("DELETE FROM positions WHERE adresse = ?", (adresse,))
        if positions:
            self.cx.executemany(
                "INSERT INTO positions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(adresse, *p, valeur_compte, lue_le) for p in positions])
        self.cx.execute(
            "INSERT INTO adresses (adresse, origine, valeur_compte, lue_le, nb_positions, notionnel) "
            "VALUES (?, 'transactions', ?, ?, ?, ?) ON CONFLICT(adresse) DO UPDATE SET "
            "valeur_compte = excluded.valeur_compte, lue_le = excluded.lue_le, "
            "nb_positions = excluded.nb_positions, notionnel = excluded.notionnel",
            (adresse, valeur_compte, lue_le, len(positions), notionnel))
        self._en_attente += 1

    def marches(self, lignes: list[tuple[str, float, float, int]]) -> None:
        self.cx.executemany("INSERT OR REPLACE INTO marches VALUES (?, ?, ?, ?)", lignes)
        self._en_attente += 1

    def valider(self) -> None:
        if self._en_attente:
            self.cx.commit()
            self._en_attente = 0

    def fermer(self) -> None:
        self.valider()
        self.cx.close()


# ----------------------------------------------------------------------
# LECTURES POUR L'INTERFACE
# ----------------------------------------------------------------------
COLONNES_POSITIONS = ["adresse", "actif", "taille", "prix_entree", "prix_liquidation", "valeur", "levier",
                      "marge", "marge_utilisee", "pnl", "valeur_compte", "lue_le"]


def lire_positions(actif: str, connexion: sqlite3.Connection | None = None) -> pd.DataFrame:
    cx = connexion or ouvrir(lecture_seule=True)
    try:
        return pd.read_sql_query(f"SELECT {', '.join(COLONNES_POSITIONS)} FROM positions WHERE actif = ?",
                                 cx, params=(actif,))
    finally:
        if connexion is None:
            cx.close()


def resume_actifs(connexion: sqlite3.Connection | None = None) -> pd.DataFrame:
    """Par actif : nombre de positions, notionnel long et short suivis, prix et open interest."""
    cx = connexion or ouvrir(lecture_seule=True)
    try:
        return pd.read_sql_query(
            "SELECT p.actif, COUNT(*) AS positions, "
            "SUM(CASE WHEN p.taille > 0 THEN p.valeur ELSE 0 END) AS longs, "
            "SUM(CASE WHEN p.taille < 0 THEN p.valeur ELSE 0 END) AS shorts, "
            "MAX(p.lue_le) AS lue_le, m.prix, m.open_interest, m.maj "
            "FROM positions p LEFT JOIN marches m ON m.actif = p.actif "
            "GROUP BY p.actif ORDER BY (longs + shorts) DESC", cx)
    finally:
        if connexion is None:
            cx.close()


def lire_marches(connexion: sqlite3.Connection | None = None) -> pd.DataFrame:
    cx = connexion or ouvrir(lecture_seule=True)
    try:
        return pd.read_sql_query("SELECT actif, prix, open_interest, maj FROM marches", cx)
    finally:
        if connexion is None:
            cx.close()


def statistiques(connexion: sqlite3.Connection | None = None) -> dict:
    cx = connexion or ouvrir(lecture_seule=True)
    try:
        adresses, lues = cx.execute("SELECT COUNT(*), COUNT(lue_le) FROM adresses").fetchone()
        positions, comptes = cx.execute("SELECT COUNT(*), COUNT(DISTINCT adresse) FROM positions").fetchone()
        derniere = cx.execute("SELECT MAX(lue_le) FROM adresses").fetchone()[0]
        return {"adresses": adresses, "lues": lues, "positions": positions, "comptes_avec_positions": comptes,
                "derniere_lecture": derniere, "maintenant": int(time.time() * 1000)}
    finally:
        if connexion is None:
            cx.close()
