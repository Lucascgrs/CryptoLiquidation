"""
Ramène les symboles des plateformes à un même nom d'actif.

Les petites cryptos sont souvent cotées par paquets : 1000PEPE (Binance),
kPEPE (Hyperliquid), SHIB1000 (Bybit), 1MBABYDOGE… `normaliser` rend le nom de
l'actif et la taille du paquet, pour ramener prix et quantités à une unité.
"""

from __future__ import annotations

ALIAS = {"XBT": "BTC"}

_PREFIXES = (("1000000", 1e6), ("100000", 1e5), ("10000", 1e4), ("1000", 1e3), ("1M", 1e6))


def normaliser(base: str) -> tuple[str, float]:
    """« 1000PEPE » -> (« PEPE », 1000) ; « kBONK » -> (« BONK », 1000) ; « XBT » -> (« BTC », 1)."""
    brut = (base or "").strip()
    if len(brut) > 1 and brut[0] == "k" and brut[1:].isupper():
        reste = brut[1:]
        return ALIAS.get(reste, reste), 1e3
    texte = brut.upper()
    for prefixe, taille in _PREFIXES:
        reste = texte[len(prefixe):]
        if texte.startswith(prefixe) and reste[:1].isalpha():
            return ALIAS.get(reste, reste), taille
    if texte.endswith("1000") and len(texte) > 4 and texte[-5].isalpha():
        reste = texte[:-4]
        return ALIAS.get(reste, reste), 1e3
    return ALIAS.get(texte, texte), 1.0


def retirer_cotation(symbole: str, cotations=("USDT", "USDC", "USD", "PERP", "BUSD", "FDUSD")) -> str:
    """« BTCUSDT » -> « BTC » ; « SOLPERP » -> « SOL ». Pour les plateformes qui ne donnent pas l'actif."""
    s = symbole.upper()
    for c in cotations:
        if s.endswith(c) and len(s) > len(c):
            return s[: -len(c)]
    return s
