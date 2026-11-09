"""Une source par plateforme ; `CLASSES` les associe aux clés de config.PLATEFORMES."""

from .binance import SourceBinance
from .bitget import SourceBitget
from .bybit import SourceBybit
from .deribit import SourceDeribit
from .dydx import SourceDydx
from .gate import SourceGate
from .htx import SourceHTX
from .hyperliquid import SourceHyperliquid
from .kraken import SourceKraken
from .okx import SourceOKX

CLASSES = {c.cle: c for c in (SourceBinance, SourceBybit, SourceOKX, SourceBitget, SourceGate, SourceHTX,
                              SourceKraken, SourceDeribit, SourceDydx, SourceHyperliquid)}
