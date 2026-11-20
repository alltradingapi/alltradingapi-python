"""alltradingapi — Python SDK for the All Trading API.

Independent, unofficial API provider. Not affiliated with, endorsed by, or
connected to Quotex. This SDK provides market data only; it cannot place
trades and is not financial advice.

Quick start::

    from alltradingapi import Client

    with Client(api_key="ap_...") as ata:
        candles = ata.candles("EURUSD_otc")
        print(candles[-1].close)
"""

from ._version import __version__
from .async_client import AsyncClient
from .client import ApiKey, Client
from .errors import (
    ALLError,
    AuthenticationError,
    ConfigurationError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    TimeoutError_,
    TransportError,
    UpdateCheckError,
    UpdateRequiredError,
    UpstreamError,
    ValidationError,
)
from .logging_ import logger, set_log_level
from .models import (
    APIObject,
    Asset,
    Candle,
    SavedCandles,
    TradeOutcome,
    TradeResult,
)
from .update import UpdateInfo, UpdatePolicy
from .utils import is_valid_asset as validate_asset  # noqa: F401
from .utils import redact_url  # noqa: F401

__all__ = [
    # clients
    "Client",
    "AsyncClient",
    "ApiKey",
    # models
    "APIObject",
    "Asset",
    "Candle",
    "SavedCandles",
    "TradeResult",
    "TradeOutcome",
    "UpdatePolicy",
    "UpdateInfo",
    # errors
    "ALLError",
    "ConfigurationError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "ValidationError",
    "RateLimitError",
    "ServerError",
    "UpstreamError",
    "TransportError",
    "TimeoutError_",
    "UpdateRequiredError",
    "UpdateCheckError",
    # utilities
    "set_log_level",
    "logger",
    "validate_asset",
    "redact_url",
    "__version__",
]
