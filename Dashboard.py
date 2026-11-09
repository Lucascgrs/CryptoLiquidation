"""
CryptoLiquidation — lancement de l'interface graphique.

    python Dashboard.py

Tout le code vit dans le paquet `liquidation_lab/` ; ce fichier ne fait qu'ouvrir
la fenêtre. La collecte peut aussi tourner sans l'interface : main.py.
"""

import os
import sys

RACINE = os.path.dirname(os.path.abspath(__file__))
os.chdir(RACINE)
if RACINE not in sys.path:
    sys.path.insert(0, RACINE)


def main():
    try:
        from liquidation_lab.interface import lancer
    except ImportError as erreur:
        print(f"Dépendance manquante : {erreur}")
        print("Installe le nécessaire : pip install -r requirements.txt")
        return 1
    lancer()
    return 0


if __name__ == "__main__":
    sys.exit(main())
