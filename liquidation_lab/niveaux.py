"""
Carte des niveaux de liquidation réels d'une crypto, calculée à partir des
positions Hyperliquid lues par le collecteur.

Rien n'est estimé : chaque position a son prix de liquidation, donné par
Hyperliquid. Un long est liquidé si le prix descend jusqu'à son prix de
liquidation, un short s'il monte jusqu'au sien. La valeur d'une position est
recalculée au prix courant (taille × prix), pour que des positions lues à des
moments différents restent comparables.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from .formats import dollars, pourcent, prix


class Carte:
    def __init__(self, positions: pd.DataFrame, prix: float, open_interest: float | None = None):
        self.prix = float(prix)
        self.open_interest = float(open_interest or 0.0)
        p = positions.copy()
        p["long"] = p["taille"] > 0
        p["notionnel"] = p["taille"].abs() * self.prix
        p["distance"] = (p["prix_liquidation"] - self.prix) / self.prix     # < 0 : sous le prix
        self.positions = p.reset_index(drop=True)

    # ------------------------------------------------------------------
    def filtrer(self, valeur_min: float = 0.0, marge: str | None = None, age_max_ms: float | None = None,
                maintenant_ms: float | None = None) -> "Carte":
        p = self.positions
        masque = p["notionnel"] >= valeur_min
        if marge in ("cross", "isolated"):
            masque &= p["marge"] == marge
        if age_max_ms:
            maintenant_ms = maintenant_ms or time.time() * 1000
            masque &= (maintenant_ms - p["lue_le"]) <= age_max_ms
        carte = Carte.__new__(Carte)
        carte.prix, carte.open_interest = self.prix, self.open_interest
        carte.positions = p[masque].reset_index(drop=True)
        return carte

    @property
    def avec_prix(self) -> pd.DataFrame:
        return self.positions.dropna(subset=["prix_liquidation"])

    def totaux(self) -> dict:
        p = self.positions
        longs, shorts = p[p["long"]], p[~p["long"]]
        oi_valeur = self.open_interest * self.prix
        return {
            "positions": len(p), "longs": len(longs), "shorts": len(shorts),
            "valeur_longs": float(longs["notionnel"].sum()), "valeur_shorts": float(shorts["notionnel"].sum()),
            "sans_prix": int(p["prix_liquidation"].isna().sum()),
            "oi_valeur": oi_valeur,
            "couverture_longs": float(longs["notionnel"].sum()) / oi_valeur if oi_valeur else None,
            "couverture_shorts": float(shorts["notionnel"].sum()) / oi_valeur if oi_valeur else None,
        }

    # ------------------------------------------------------------------
    def tranches(self, plage: float, n: int = 80):
        """Valeur à liquider par tranche de prix sur [prix × (1 − plage), prix × (1 + plage)]."""
        bords = np.linspace(self.prix * (1 - plage), self.prix * (1 + plage), n + 1)
        p = self.avec_prix
        resultat = {}
        for cote, masque in (("longs", p["long"]), ("shorts", ~p["long"])):
            x = p.loc[masque, "prix_liquidation"].to_numpy()
            v = p.loc[masque, "notionnel"].to_numpy()
            valeurs, _ = np.histogram(x, bins=bords, weights=v)
            nombres, _ = np.histogram(x, bins=bords)
            resultat[cote] = (valeurs, nombres)
        return bords, resultat

    def cumul(self, prix_cible: float) -> tuple[float, int]:
        """Ce qui serait liquidé si le prix allait jusqu'à `prix_cible` (longs en dessous, shorts au-dessus)."""
        p = self.avec_prix
        if prix_cible < self.prix:
            touches = p[p["long"] & (p["prix_liquidation"] >= prix_cible)]
        else:
            touches = p[~p["long"] & (p["prix_liquidation"] <= prix_cible)]
        return float(touches["notionnel"].sum()), len(touches)

    def courbes(self, plage: float, n: int = 400):
        """Cumul des longs liquidés en descendant, des shorts en montant, sur n points de chaque côté."""
        p = self.avec_prix
        bas = np.linspace(self.prix, self.prix * (1 - plage), n)
        haut = np.linspace(self.prix, self.prix * (1 + plage), n)
        longs = p[p["long"]].sort_values("prix_liquidation", ascending=False)
        shorts = p[~p["long"]].sort_values("prix_liquidation")
        cl = np.concatenate([[0.0], np.cumsum(longs["notionnel"].to_numpy())])
        cs = np.concatenate([[0.0], np.cumsum(shorts["notionnel"].to_numpy())])
        # nombre de longs dont le prix de liquidation est >= x (liste décroissante)
        idx_l = np.searchsorted(-longs["prix_liquidation"].to_numpy(), -bas, side="right")
        idx_s = np.searchsorted(shorts["prix_liquidation"].to_numpy(), haut, side="right")
        return bas, cl[idx_l], haut, cs[idx_s]

    # ------------------------------------------------------------------
    def texte(self, plage: float = 0.10) -> str:
        t = self.totaux()
        lignes = [f"Prix {prix(self.prix)} $ — {t['positions']} positions connues "
                  f"({t['longs']} longs, {t['shorts']} shorts, {t['sans_prix']} sans prix de liquidation)"]
        if t["couverture_longs"] is not None:
            lignes.append(f"Open interest {dollars(t['oi_valeur'])} : positions connues = "
                          f"{pourcent(t['couverture_longs'])} des longs, {pourcent(t['couverture_shorts'])} des shorts")
        lignes.append("\nSi le prix allait jusqu'à…")
        pas = max(plage / 10, 0.005)
        for k in range(1, int(round(plage / pas)) + 1):
            e = k * pas
            v_bas, n_bas = self.cumul(self.prix * (1 - e))
            v_haut, n_haut = self.cumul(self.prix * (1 + e))
            lignes.append(f"  {pourcent(-e, signe=True):>8} ({prix(self.prix * (1 - e)):>10}) : {dollars(v_bas):>10} "
                          f"de longs ({n_bas})    {pourcent(e, signe=True):>8} ({prix(self.prix * (1 + e)):>10}) : "
                          f"{dollars(v_haut):>10} de shorts ({n_haut})")
        bords, par_cote = self.tranches(plage, 40)
        lignes.append("\nLes plus gros niveaux :")
        niveaux = []
        for cote, (valeurs, nombres) in par_cote.items():
            for i in np.argsort(valeurs)[::-1][:5]:
                if valeurs[i] > 0:
                    niveaux.append((valeurs[i], cote, bords[i], bords[i + 1], nombres[i]))
        for v, cote, a, b, n in sorted(niveaux, reverse=True)[:8]:
            lignes.append(f"  {cote:<6} {prix(a):>10} – {prix(b):<10} {dollars(v):>10}  ({n} positions)")
        p = self.avec_prix
        p = p[p["distance"].abs() <= plage].nlargest(8, "notionnel")
        lignes.append("\nLes plus grosses positions dans la plage :")
        for _, x in p.iterrows():
            marge = "isolée" if x["marge"] == "isolated" else "croisée"
            lignes.append(f"  {'long ' if x['long'] else 'short'} {dollars(x['notionnel']):>10}  liquidation à "
                          f"{prix(x['prix_liquidation'])} ({pourcent(x['distance'], signe=True)}), "
                          f"levier {x['levier']:.0f}, marge {marge}, {x['adresse'][:10]}…")
        return "\n".join(lignes)
