"""
Démarrage et arrêt du collecteur depuis l'interface.

Le collecteur tourne dans son propre processus (`main.py collecter`), sans
fenêtre de console : l'interface peut être fermée et rouverte sans couper la
collecte, si l'option « Arrêter la collecte en fermant l'interface » est
décochée.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from .. import config
from ..collecteur import demander_arret, etat_collecteur

SANS_FENETRE = 0x08000000           # CREATE_NO_WINDOW (Windows)


class Gestion:
    def __init__(self):
        self.processus: subprocess.Popen | None = None
        self.lance_le = 0.0

    @property
    def lance_ici(self) -> bool:
        return self.processus is not None and self.processus.poll() is None

    def demarrer(self) -> str | None:
        """Lance le collecteur ; rend un message d'erreur, ou None."""
        if etat_collecteur():
            return "La collecte est déjà en marche."
        if self.lance_ici:
            return "La collecte démarre…"
        executable = sys.executable
        # pythonw.exe (sans console) si disponible : rien ne s'ouvre à l'écran
        sans_console = os.path.join(os.path.dirname(executable), "pythonw.exe")
        if os.path.exists(sans_console):
            executable = sans_console
        options = {"creationflags": SANS_FENETRE} if sys.platform == "win32" else {"start_new_session": True}
        try:
            self.processus = subprocess.Popen(
                [executable, os.path.join(config.RACINE, "main.py"), "collecter", "--depuis-interface"],
                cwd=config.RACINE, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, **options)
        except OSError as erreur:
            return f"Lancement impossible : {erreur}"
        self.lance_le = time.monotonic()
        return None

    def arreter(self) -> None:
        demander_arret()

    def demarrage_en_cours(self) -> bool:
        return self.lance_ici and time.monotonic() - self.lance_le < 30 and not etat_collecteur()

    def a_echoue(self) -> bool:
        """Le processus lancé s'est arrêté tout seul avant d'avoir publié son état."""
        return self.processus is not None and self.processus.poll() is not None and \
            time.monotonic() - self.lance_le < 60 and not etat_collecteur()

    def fermer(self, arreter: bool) -> None:
        """À la fermeture de l'interface : seule une collecte lancée par elle peut être arrêtée."""
        if arreter and self.lance_ici:
            demander_arret()
