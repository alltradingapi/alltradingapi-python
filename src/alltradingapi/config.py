"""Package-wide constants and environment helpers.

Everything user-tunable lives here so tests and docs can point at one place.
"""

import os

# ---------------------------------------------------------------------------
# Service coordinates
# ---------------------------------------------------------------------------
BASE_URL = "https://alltradingapi.com"
PYPI_JSON_URL = "https://pypi.org/pypi/alltradingapi/json"
PYPI_PACKAGE = "alltradingapi"

# ---------------------------------------------------------------------------
# Request behaviour
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT = 30.0  # read timeout, seconds
DEFAULT_CONNECT_TIMEOUT = 10.0  # connect timeout, seconds
DEFAULT_MAX_RETRIES = 2  # extra attempts after the first
DEFAULT_BACKOFF_FACTOR = 0.5  # seconds, exponential with full jitter
DEFAULT_RETRY_STATUSES = (429, 500, 502, 503, 504)
DEFAULT_USER_AGENT = "alltradingapi-python/1.0.3"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024  # 32 MiB safety cap per response
DEFAULT_AUTH_MODE = "bearer"  # "bearer" (preferred) or "query"
DEFAULT_CACHE_TTL = 60.0  # seconds; assets()/timeframes() only
CONNECTION_POOL_SIZE = 8  # idle keep-alive connections per host

# ---------------------------------------------------------------------------
# Update gate
# ---------------------------------------------------------------------------
UPDATE_CHECK_TIMEOUT = 2.0  # hard cap so the gate can never hang
UPDATE_CACHE_TTL = 6 * 3600  # 6 hours between live PyPI checks
UPDATE_CACHE_FILE = "alltradingapi/update.json"

# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------
ENV_API_KEY = "ATA_API_KEY"
ENV_KEY_PREFIX = "ATA_KEY_"  # + PRODUCT.upper().replace("-", "_")
ENV_UPDATE_POLICY = "ATA_UPDATE_POLICY"
ENV_UPDATE_TTL = "ATA_UPDATE_TTL"
ENV_SKIP_UPDATE = "ATA_SKIP_UPDATE"
ENV_NO_PROMPT = "ATA_NO_PROMPT"
ENV_CI = "ATA_CI"
ENV_HTTP_PROXY = "ATA_HTTP_PROXY"
ENV_HTTPS_PROXY = "ATA_HTTPS_PROXY"
ENV_LIVE = "ATA_LIVE"  # gates the optional live probe script

CI_ENV_VARS = ("CI", "GITHUB_ACTIONS")

# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------
PRODUCTS = (
    "quotex-assets",
    "quotex-quotes",
    "quotex-realtime",
    "quotex-saved",
    "quotex-screenshots",
    "quotex-results",
)

# Asset symbols are validated before any request is made.
ASSET_PATTERN = r"^[A-Za-z0-9_]{2,40}$"

TIMEFRAME_LABELS = (
    "5s",
    "10s",
    "15s",
    "30s",
    "1m",
    "2m",
    "3m",
    "5m",
    "10m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
)

TIMEFRAME_SECONDS = {
    "5s": 5,
    "10s": 10,
    "15s": 15,
    "30s": 30,
    "1m": 60,
    "2m": 120,
    "3m": 180,
    "5m": 300,
    "10m": 600,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
}


def env(name: str, default: str = "") -> str:
    """Read an environment variable, returning *default* when unset/blank."""
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean-ish environment variable (1/true/yes/on)."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_float(name: str, default: float) -> float:
    """Read a float environment variable, falling back on garbage."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def is_ci() -> bool:
    """True when running inside CI (ATA_CI, CI, GITHUB_ACTIONS)."""
    if env_bool(ENV_CI):
        return True
    for name in CI_ENV_VARS:
        value = os.environ.get(name, "")
        if value and value.lower() not in ("0", "false", ""):
            return True
    return False


def is_interactive() -> bool:
    """True when stdin is a TTY and we are not inside CI."""
    import sys

    if is_ci():
        return False
    if env_bool(ENV_NO_PROMPT):
        return False
    try:
        return bool(sys.stdin.isatty())
    except Exception:  # pragma: no cover - exotic stdin wrappers
        return False


def cache_dir() -> str:
    """Best-effort per-user cache directory (Windows %LOCALAPPDATA% first)."""
    candidates = [
        os.environ.get("LOCALAPPDATA"),
        os.environ.get("APPDATA"),
        os.environ.get("XDG_CACHE_HOME"),
        os.path.join(os.path.expanduser("~"), ".cache"),
    ]
    for path in candidates:
        if path:
            return path
    import tempfile

    return tempfile.gettempdir()  # pragma: no cover - HOME-less systems
