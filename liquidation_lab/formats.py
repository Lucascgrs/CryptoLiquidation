"""Nombres, prix, durées, écrits à la française (interface et ligne de commande)."""

from __future__ import annotations

import math
import time


def _fr(texte: str) -> str:
    return texte.replace(",", " ").replace(".", ",")


def dollars(v: float, precis: bool = False) -> str:
    """1 240 000 -> « 1,24 M$ » ; 350 -> « 350 $ »."""
    if v != v:                                  # nan
        return "—"
    a = abs(v)
    unites = ((1e9, " Md$"), (1e6, " M$"), (1e3, " k$"))
    for k, (seuil, suffixe) in enumerate(unites):
        # 999 999 s'écrit « 1,00 M$ » et non « 1 000 k$ »
        if a >= seuil or (k + 1 < len(unites) and round(a / unites[k + 1][0]) >= 1000):
            x = v / seuil
            chiffres = 2 if abs(x) < 9.995 else 1 if abs(x) < 99.95 else 0
            return _fr(f"{x:,.{chiffres}f}") + suffixe
    if round(a) >= 1000:
        return _fr(f"{v / 1e3:,.2f}") + " k$"
    return _fr(f"{v:,.{2 if precis and a < 10 else 0}f}") + " $"


def prix(p: float) -> str:
    """Autant de décimales qu'il en faut pour lire le prix : 77 952 ; 2 246,5 ; 0,004545."""
    if p != p or p <= 0:
        return "—"
    if p >= 10_000:
        decimales = 0
    elif p >= 100:
        decimales = 1
    elif p >= 10:
        decimales = 2
    elif p >= 1:
        decimales = 3
    else:                                       # quatre chiffres significatifs
        decimales = 3 - int(math.floor(math.log10(p)))
    return _fr(f"{p:,.{decimales}f}")


def entier(n: int) -> str:
    return f"{int(n):,}".replace(",", " ")


def quantite(q: float) -> str:
    """Une quantité d'actif : 16 628 ; 975,708 ; 0,0054."""
    q = abs(q)
    if q >= 1000:
        return entier(round(q))
    if q >= 1:
        return _fr(f"{q:,.3f}").rstrip("0").rstrip(",")
    return f"{q:.4g}".replace(".", ",")


def pourcent(x: float, signe: bool = False) -> str:
    """0,42 -> « 42 % » ; avec signe, une décimale : -0,042 -> « -4,2 % »."""
    if x != x:
        return "—"
    return (f"{x:+.1%}" if signe else f"{x:.0%}").replace(".", ",").replace("%", " %")


def il_y_a(t_ms: float, maintenant_ms: float | None = None) -> str:
    secondes = max(0.0, ((maintenant_ms or time.time() * 1000) - t_ms) / 1000)
    if secondes < 60:
        return f"il y a {secondes:.0f} s"
    if secondes < 3600:
        return f"il y a {secondes / 60:.0f} min"
    if secondes < 86400 * 2:
        return f"il y a {secondes / 3600:.0f} h"
    return f"il y a {secondes / 86400:.0f} j"


def duree(secondes: float) -> str:
    if secondes < 60:
        return f"{secondes:.0f} s"
    if secondes < 3600:
        return f"{secondes / 60:.0f} min"
    heures = int(secondes // 3600)
    return f"{heures} h {int(secondes % 3600 // 60):02d}"


def heure(t_ms: float, secondes: bool = True) -> str:
    return time.strftime("%H:%M:%S" if secondes else "%H:%M", time.localtime(t_ms / 1000))
