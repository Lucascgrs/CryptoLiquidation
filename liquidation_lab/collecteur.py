"""
Le collecteur : toutes les sources en même temps, le registre des
liquidations et l'état publié pour l'interface.

Il tourne dans son propre processus, lancé par le bouton « Démarrer » de
l'interface ou par `python main.py collecter`, et s'arrête :
- quand le fichier `donnees/collecteur.arret` apparaît (bouton « Arrêter ») ;
- au bout de la durée demandée (`--duree`) ;
- sur Ctrl+C dans la console.
Un seul collecteur à la fois : un second refuse de démarrer.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler

import aiohttp

from . import config, stockage
from .sources import CLASSES
from .sources.hyperliquid import SourceHyperliquid

ETAT_PERIME = 6.0           # secondes : au-delà, un état non rafraîchi signale un collecteur arrêté


def configurer_journal(console: bool = True) -> logging.Logger:
    config.preparer_dossiers()
    journal = logging.getLogger("collecteur")
    journal.setLevel(logging.INFO)
    journal.handlers.clear()
    fichier = RotatingFileHandler(config.FICHIER_JOURNAL, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fichier.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    journal.addHandler(fichier)
    if console:
        ecran = logging.StreamHandler(sys.stdout)
        ecran.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        journal.addHandler(ecran)
    return journal


def processus_vivant(pid: int) -> bool:
    if not pid:
        return False
    if sys.platform == "win32":
        # surtout pas os.kill(pid, 0) : sous Windows, cela termine le processus
        noyau = ctypes.windll.kernel32
        poignee = noyau.OpenProcess(0x1000, False, int(pid))      # PROCESS_QUERY_LIMITED_INFORMATION
        if not poignee:
            return False
        code = ctypes.c_ulong()
        ok = noyau.GetExitCodeProcess(poignee, ctypes.byref(code))
        noyau.CloseHandle(poignee)
        return bool(ok) and code.value == 259                       # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def etat_collecteur() -> dict | None:
    """L'état publié, s'il vient d'un collecteur en marche ; None sinon."""
    etat = stockage.lire_json(config.FICHIER_ETAT)
    if not etat or not etat.get("en_marche"):
        return None
    if time.time() * 1000 - etat.get("maj", 0) > ETAT_PERIME * 1000:
        return None
    return etat if processus_vivant(etat.get("pid", 0)) else None


def demander_arret() -> None:
    config.preparer_dossiers()
    with open(config.FICHIER_ARRET, "w", encoding="utf-8") as f:
        f.write(str(time.time()))


class Collecteur:
    def __init__(self, sources: list[str], positions: bool = True, poids_minute: float = 1100,
                 duree: float | None = None, journal: logging.Logger | None = None,
                 resume_console: bool = False):
        self.cles = [c for c in config.ORDRE_PLATEFORMES if c in sources]
        self.positions = positions
        self.poids_minute = poids_minute
        self.duree = duree
        self.journal = journal or configurer_journal()
        self.resume_console = resume_console
        self.registre = stockage.Registre()
        self.sources = []
        self.debut = int(time.time() * 1000)

    def executer(self) -> int:
        actif = etat_collecteur()
        if actif:
            self.journal.error("un collecteur tourne déjà (processus %s) : rien à faire", actif.get("pid"))
            return 1
        try:
            os.remove(config.FICHIER_ARRET)
        except OSError:
            pass
        stockage.ecrire_json(config.FICHIER_VERROU, {"pid": os.getpid(), "debut": self.debut})
        try:
            asyncio.run(self._principal())
        except KeyboardInterrupt:
            self.journal.info("arrêt demandé (Ctrl+C)")
        finally:
            self._terminer()
        return 0

    async def _principal(self) -> None:
        self.journal.info("collecte : %s%s", ", ".join(self.cles),
                          "" if self.positions or "hyperliquid" not in self.cles else " (sans les positions)")
        connecteur = aiohttp.TCPConnector(limit=32)
        entetes = {"User-Agent": "CryptoLiquidation/1.0"}
        async with aiohttp.ClientSession(connector=connecteur, headers=entetes,
                                         timeout=aiohttp.ClientTimeout(total=60)) as session:
            for cle in self.cles:
                if cle == "hyperliquid":
                    source = SourceHyperliquid(self.registre.ajouter, self.journal, self.positions,
                                               self.poids_minute)
                else:
                    source = CLASSES[cle](self.registre.ajouter, self.journal)
                self.sources.append(source)
            taches = [asyncio.create_task(s.tourner(session), name=s.cle) for s in self.sources]
            try:
                await self._veiller()
            finally:
                for tache in taches:
                    tache.cancel()
                await asyncio.gather(*taches, return_exceptions=True)

    async def _veiller(self) -> None:
        """Chaque demi-seconde : vider le registre ; chaque seconde : publier l'état et guetter l'arrêt."""
        dernier_etat = 0.0
        dernier_resume = time.monotonic()
        while True:
            await asyncio.sleep(0.5)
            self.registre.vider()
            maintenant = time.monotonic()
            if maintenant - dernier_etat >= 1.0:
                dernier_etat = maintenant
                stockage.ecrire_json(config.FICHIER_ETAT, self.etat(True))
                if os.path.exists(config.FICHIER_ARRET):
                    self.journal.info("arrêt demandé (bouton de l'interface ou main.py arreter)")
                    return
                if self.duree and time.time() * 1000 - self.debut > self.duree * 1000:
                    self.journal.info("durée écoulée")
                    return
            if self.resume_console and maintenant - dernier_resume >= 60:
                dernier_resume = maintenant
                self.journal.info(self.resume())

    def etat(self, en_marche: bool) -> dict:
        etat = {"pid": os.getpid(), "debut": self.debut, "maj": int(time.time() * 1000),
                "en_marche": en_marche, "positions": self.positions,
                "registre": {"ecrites": self.registre.ecrites}, "sources": {}}
        for source in self.sources:
            etat["sources"][source.cle] = source.etat.vers_dict()
            if isinstance(source, SourceHyperliquid) and hasattr(source, "detecteur"):
                etat["hyperliquid"] = source.resume()
        return etat

    def resume(self) -> str:
        morceaux = []
        for source in self.sources:
            e = source.etat
            marque = "" if e.connexions == e.prevues and e.prevues else " (déconnectée)"
            morceaux.append(f"{config.PLATEFORMES[source.cle].nom} {e.liquidations}{marque}")
        texte = f"{self.registre.ecrites} liquidations enregistrées — " + ", ".join(morceaux)
        for source in self.sources:
            if isinstance(source, SourceHyperliquid) and hasattr(source, "scanner") and self.positions:
                s = source.scanner.statistiques()
                texte += (f" — Hyperliquid : {s['lus']} comptes lus, {s['positions']} positions, "
                          f"{s['lectures_minute']} lectures/min")
        return texte

    def _terminer(self) -> None:
        self.registre.fermer()
        for source in self.sources:
            if isinstance(source, SourceHyperliquid):
                source.fermer()
        stockage.ecrire_json(config.FICHIER_ETAT, self.etat(False))
        for chemin in (config.FICHIER_VERROU, config.FICHIER_ARRET):
            try:
                os.remove(chemin)
            except OSError:
                pass
        self.journal.info("collecte arrêtée : %d liquidations enregistrées", self.registre.ecrites)
