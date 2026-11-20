"""The must-update system — detect newer PyPI releases and gate the SDK.

Owner requirement: *when a newer version exists on PyPI, the SDK must detect
it, ask the user, and refuse to operate until updated — unless the user
explicitly opts out.*

Design:

* Source of truth: ``https://pypi.org/pypi/alltradingapi/json``.
* One network check per process, guarded by a module-level lock.
* Hard 2.0 s timeout; **fail-open**: offline / PyPI errors / 404 (package not
  yet published) all mean "unknown, continue".
* Disk-cached result (default TTL 6 h, override ``ATA_UPDATE_TTL`` seconds).
* Policies: ``STRICT`` (default; gate), ``ASK`` (prompt, continue on decline),
  ``WARN`` (warn once, continue), ``OFF`` (no network at all).
* Escape hatches: ``ATA_UPDATE_POLICY=off``, ``ATA_SKIP_UPDATE=1``,
  ``Client(allow_outdated=True)``, CLI ``--no-update-check``.

This module is import-safe: importing it performs no network I/O.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from enum import Enum
from typing import Any, cast

from . import config as _config
from .errors import UpdateCheckError, UpdateRequiredError
from .logging_ import logger
from .utils import redact_url

__all__ = [
    "UpdatePolicy",
    "UpdateInfo",
    "parse_version",
    "compare_versions",
    "is_outdated",
    "fetch_latest",
    "check_for_update",
    "ensure_usable",
    "install_update",
    "upgrade_command",
    "reset_gate_state",
]

PYPI_CHECK_URL = _config.PYPI_JSON_URL


# ---------------------------------------------------------------------------
# Update policy enum (defined here; re-exported by the package __init__)
# ---------------------------------------------------------------------------
class UpdatePolicy(str, Enum):
    """Update-gate policies.

    * ``STRICT`` (default) — refuse to operate when outdated.
    * ``ASK`` — prompt interactively; a declined prompt continues with a warning.
    * ``WARN`` — print one warning, continue.
    * ``OFF`` — no check, no network.
    """

    STRICT = "strict"
    ASK = "ask"
    WARN = "warn"
    OFF = "off"

    @classmethod
    def coerce(cls, value: Any, default: UpdatePolicy | None = None) -> UpdatePolicy | None:
        """Tolerant coercion from strings/instances; ``None`` passes through."""
        if value is None:
            return default
        if isinstance(value, cls):
            return value
        text = str(value).strip().lower()
        for member in cls:
            if text == member.value:
                return member
        return default

    def __str__(self) -> str:
        return self.value


class UpdateInfo:
    """Result of an update check — safe to display and serialise."""

    __slots__ = ("installed", "latest", "is_outdated", "checked_at", "source", "known")

    def __init__(
        self,
        installed: str,
        latest: str | None,
        is_outdated: bool,
        checked_at: float,
        source: str,
        known: bool,
    ) -> None:
        self.installed = installed
        self.latest = latest
        self.is_outdated = is_outdated
        self.checked_at = checked_at
        self.source = source
        self.known = known  # False when the check failed / was offline (fail-open)

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "latest": self.latest,
            "is_outdated": self.is_outdated,
            "checked_at": self.checked_at,
            "source": self.source,
            "known": self.known,
        }

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"<UpdateInfo installed={self.installed} latest={self.latest} outdated={self.is_outdated}>"


# ---------------------------------------------------------------------------
# PEP 440-ish version comparison (local, no `packaging` dependency)
# ---------------------------------------------------------------------------
_VERSION_RE = re.compile(
    r"^\s*v?"
    r"(?:(\d+)!)?"  # epoch
    r"(\d+(?:\.\d+)*)"  # release
    r"(?:[._-]?(a|b|c|rc|alpha|beta|pre|preview)(?:[._-]?(\d+))?)?"  # pre
    r"(?:[._-]?(post|rev|r)(?:[._-]?(\d+))?)?"  # post
    r"(?:[._-]?dev(?:[._-]?(\d+))?)?"  # dev
    r"(?:\+.*)?"  # local version -> ignored
    r"\s*$",
    re.IGNORECASE,
)

_PRE_RANK = {
    "a": 1,
    "alpha": 1,
    "b": 2,
    "beta": 2,
    "c": 3,
    "pre": 3,
    "preview": 3,
    "rc": 3,
}

_RELEASE_PAD = 8
# (epoch, padded release, stage [0=dev-only,1=pre,2=final], pre-rank, pre-num, post)
_MainKey = tuple[int, tuple[int, ...], int, int, int, int]
_VersionKey = tuple[_MainKey, int]  # (main key, dev number)


def parse_version(text: str | None) -> _VersionKey | None:
    """Parse a version string into a sortable key.

    Returns ``None`` when the text is not a recognisable version, which makes
    comparisons fail open (treated as equal) instead of crashing.
    """
    if text is None:
        return None
    match = _VERSION_RE.match(str(text))
    if not match:
        return None
    epoch = int(match.group(1) or 0)
    release_raw = match.group(2) or "0"
    release = tuple(int(p) for p in release_raw.split("."))
    padded = release + (0,) * (_RELEASE_PAD - len(release))

    pre_label = (match.group(3) or "").lower()
    pre_num = int(match.group(4) or 0)
    if pre_label:
        stage = 1
        stage_rank = _PRE_RANK.get(pre_label, 1)
    else:
        stage = 2
        stage_rank = 0

    post_label = (match.group(5) or "").lower()
    post = int(match.group(6) or 0) if post_label else 0
    dev = int(match.group(7) or 0)

    # A dev suffix on a final release sorts *before* the plain release.
    effective_stage = 0 if (dev and not pre_label) else stage
    return (epoch, padded, effective_stage, stage_rank, pre_num, post), dev


def _key(v: str | None) -> _VersionKey | None:
    """Alias of :func:`parse_version` kept for internal readability."""
    return parse_version(v)


def compare_versions(a: str | None, b: str | None) -> int:
    """Compare two version strings PEP-440-style.

    Returns ``-1`` if ``a < b``, ``0`` if equal/unparsable, ``1`` if ``a > b``.
    Unparsable versions fail open (equal) by design.
    """
    key_a = _key(a)
    key_b = _key(b)
    if key_a is None or key_b is None:
        return 0
    # Compare the six release components first; `dev` breaks ties.
    main_a, dev_a = key_a
    main_b, dev_b = key_b
    if main_a < main_b:
        return -1
    if main_a > main_b:
        return 1
    # Same main key: a dev build sorts below the plain release.
    if dev_a != dev_b:
        return -1 if dev_a > dev_b else 1
    return 0


def is_outdated(installed: str | None, latest: str | None) -> bool:
    """True when *latest* is strictly greater than *installed*."""
    if not installed or not latest:
        return False
    return compare_versions(installed, latest) < 0


# ---------------------------------------------------------------------------
# PyPI check (single attempt, hard timeout, fail-open)
# ---------------------------------------------------------------------------
def fetch_latest(
    check_url: str = PYPI_CHECK_URL,
    timeout: float = _config.UPDATE_CHECK_TIMEOUT,
) -> str | None:
    """Fetch the latest version string from PyPI, or ``None`` on any failure."""
    try:
        import http.client
        import ssl
        from urllib.parse import urlsplit

        parts = urlsplit(check_url)
        if parts.scheme != "https":
            return None
        host = parts.hostname or ""
        path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
        ssl.create_default_context()
        conn = http.client.HTTPSConnection(host, parts.port or 443, timeout=timeout)
        try:
            conn.request(
                "GET",
                path,
                headers={
                    "User-Agent": _config.DEFAULT_USER_AGENT,
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            raw = conn.getresponse()
            body = raw.read(1 << 20)
        finally:
            try:
                conn.close()
            except Exception:
                pass
        if raw.status != 200:
            return None
        payload = json.loads(body.decode("utf-8", errors="replace"))
        version = payload.get("info", {}).get("version")
        return str(version) if version else None
    except Exception:
        # Fail-open by design: offline, DNS failure, 404, timeout, garbage.
        return None


def cache_path(cache_dir: str | None = None) -> str:
    """Absolute path of the on-disk update cache file."""
    base = cache_dir if cache_dir is not None else _config.cache_dir()
    return os.path.join(base, _config.UPDATE_CACHE_FILE.replace("/", os.sep))


def read_cache(cache_dir: str | None = None) -> dict[str, Any] | None:
    """Read the cached update record, or ``None`` when missing/corrupt."""
    path = cache_path(cache_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "checked_at" in data:
            return data
    except (OSError, ValueError):
        pass
    return None


def write_cache(record: dict[str, Any], cache_dir: str | None = None) -> bool:
    """Persist the update record; best-effort, returns success."""
    path = cache_path(cache_dir)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        os.replace(tmp_path, path)
        return True
    except OSError:
        return False


def resolve_policy(explicit: Any = None) -> UpdatePolicy:
    """Resolve the effective policy from arg, env, then STRICT default."""
    env_policy = _config.env(_config.ENV_UPDATE_POLICY)
    chosen = UpdatePolicy.coerce(explicit, None)
    if chosen is None and env_policy:
        chosen = UpdatePolicy.coerce(env_policy, None)
    return chosen or UpdatePolicy.STRICT


# ---------------------------------------------------------------------------
# One-check-per-process gate state
# ---------------------------------------------------------------------------
_gate_lock = threading.Lock()
_gate_state: dict[str, Any] = {"done": False, "info": None, "blocked": False}


def reset_gate_state() -> None:
    """Forget the in-process gate result (used by tests)."""
    with _gate_lock:
        _gate_state["done"] = False
        _gate_state["info"] = None
        _gate_state["blocked"] = False


def check_for_update(
    force: bool = False,
    installed: str | None = None,
    *,
    check_url: str = PYPI_CHECK_URL,
    cache_dir: str | None = None,
    ttl: float | None = None,
    now: float | None = None,
    fetcher: Callable[[str, float], str | None] | None = None,
) -> UpdateInfo:
    """Check PyPI for a newer release, honouring the disk cache.

    Never raises for network problems; a failed check returns ``known=False``
    with ``latest=None`` (fail-open). Raises :class:`UpdateCheckError` only
    when the *installed* version cannot be determined.
    """
    from ._version import __version__ as fallback_version

    installed_version = installed or _installed_version(fallback_version)
    if installed_version is None:
        raise UpdateCheckError(
            "Could not determine the installed SDK version; reinstall with: "
            "pip install --upgrade alltradingapi"
        )
    ttl_seconds = (
        ttl
        if ttl is not None
        else _config.env_float(_config.ENV_UPDATE_TTL, _config.UPDATE_CACHE_TTL)
    )
    clock: Callable[[], float] = time.time if now is None else (lambda: now)
    current = clock()

    fetch = fetcher if fetcher is not None else fetch_latest

    with _gate_lock:
        cached_inproc = _gate_state["info"]
    if (
        not force
        and isinstance(cached_inproc, UpdateInfo)
        and current - cached_inproc.checked_at < ttl_seconds
    ):
        return cached_inproc

    cached = None if force else read_cache(cache_dir)
    if cached is not None:
        try:
            age = current - float(cached.get("checked_at", 0))
        except (TypeError, ValueError):
            age = ttl_seconds + 1
        if age < ttl_seconds:
            latest = cached.get("latest")
            outdated = is_outdated(installed_version, latest)
            return UpdateInfo(
                installed=installed_version,
                latest=latest,
                is_outdated=outdated,
                checked_at=float(cached.get("checked_at", 0)),
                source=str(cached.get("source", "cache")),
                known=bool(latest),
            )

    latest = fetch(check_url, _config.UPDATE_CHECK_TIMEOUT)
    checked_at = current
    info = UpdateInfo(
        installed=installed_version,
        latest=latest,
        is_outdated=is_outdated(installed_version, latest),
        checked_at=checked_at,
        source="pypi",
        known=bool(latest),
    )
    if info.known:
        write_cache(
            {
                "checked_at": checked_at,
                "latest": latest,
                "source": "pypi",
            },
            cache_dir,
        )
        with _gate_lock:
            _gate_state["info"] = info
    return info


def _installed_version(fallback: str) -> str | None:
    """Resolve the installed version; importlib.metadata first, file fallback."""
    try:
        from importlib import metadata as importlib_metadata
    except ImportError:  # pragma: no cover - Python <3.8
        importlib_metadata = None  # type: ignore[assignment]
    if importlib_metadata is not None:
        try:
            return str(importlib_metadata.version("alltradingapi"))
        except Exception:
            pass
    return fallback


def upgrade_command() -> list[str]:
    """The exact command used (and printed on failure) to self-upgrade."""
    return [sys.executable, "-m", "pip", "install", "--upgrade", "alltradingapi"]


def install_update(
    *,
    installer: Callable[[list[str]], tuple[int, str]] | None = None,
    timeout: float = 300.0,
) -> tuple[bool, str]:
    """Run ``pip install --upgrade alltradingapi`` and report (ok, output tail).

    The output tail is redacted of any URL credentials, just in case.
    """
    if installer is not None:
        code, output = installer(upgrade_command())
        return code == 0, str(redact_url(output or "") or "")

    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            upgrade_command(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"pip failed: {redact_url(str(exc))}"
    output = ((completed.stdout or "") + (completed.stderr or "")).strip()
    tail = output[-800:] if len(output) > 800 else output
    return completed.returncode == 0, str(redact_url(tail) or "")


# ---------------------------------------------------------------------------
# Interactive prompt
# ---------------------------------------------------------------------------
def _timed_input(prompt_text: str, timeout: float = 30.0) -> str | None:
    """Read a line from stdin with a timeout; ``None`` on timeout/EOF.

    Uses a daemon thread so no platform-specific select() is needed.
    """
    import threading as _threading

    result: dict[str, str] = {}

    def _reader() -> None:
        try:
            result["text"] = input(prompt_text)
        except (EOFError, KeyboardInterrupt, OSError):
            result["text"] = ""

    thread = _threading.Thread(target=_reader, daemon=True)
    thread.start()
    thread.join(timeout)
    return result.get("text")


def _print_notice(lines: list[str]) -> None:
    width = max(len(line) for line in lines) + 2
    print("-" * width, file=sys.stderr)
    for line in lines:
        print(line, file=sys.stderr)
    print("-" * width, file=sys.stderr)


def ensure_usable(
    installed: str,
    *,
    policy: Any = None,
    allow_outdated: bool = False,
    interactive: bool | None = None,
    check_url: str = PYPI_CHECK_URL,
    cache_dir: str | None = None,
    ttl: float | None = None,
    now: float | None = None,
    fetcher: Callable[[str, float], str | None] | None = None,
    installer: Callable[[list[str]], tuple[int, str]] | None = None,
    input_fn: Callable[[str], str | None] | None = None,
    printer: Callable[[str], None] | None = None,
) -> UpdateInfo | None:
    """Run the update gate; return :class:`UpdateInfo` when usable.

    Raises :class:`UpdateRequiredError` when the strict gate blocks operation.
    Runs at most one network check per process (cached afterwards).

    Args:
        installed: Installed version string.
        policy: :class:`UpdatePolicy` or policy string; default STRICT.
        allow_outdated: Equivalent to WARN for this call.
        interactive: Force interactive prompting on/off; auto by default.
        check_url: Override the PyPI JSON URL (tests / mirrors).
        installer/inout_fn/printer/fetcher: Injection points for tests.
    """
    with _gate_lock:
        if _gate_state["done"]:
            if _gate_state["blocked"]:
                info = _gate_state["info"]
                raise _build_error(info, installed_now=False)
            return cast(UpdateInfo | None, _gate_state["info"])

    if _config.env_bool(_config.ENV_SKIP_UPDATE):
        return None

    effective = resolve_policy(policy)
    if allow_outdated and effective in (UpdatePolicy.STRICT, UpdatePolicy.ASK):
        effective = UpdatePolicy.WARN

    if effective is UpdatePolicy.OFF:
        with _gate_lock:
            _gate_state["done"] = True
            _gate_state["info"] = None
        return None

    info = check_for_update(
        force=False,
        installed=installed,
        check_url=check_url,
        cache_dir=cache_dir,
        ttl=ttl,
        now=now,
        fetcher=fetcher,
    )

    if not info.is_outdated or not info.known:
        with _gate_lock:
            _gate_state["done"] = True
            _gate_state["info"] = info
        return info

    emit = printer or (lambda text: print(text, file=sys.stderr))

    def _notify() -> None:
        emit(
            "alltradingapi: a newer SDK is available "
            f"({info.installed} -> {info.latest}). Run: pip install --upgrade alltradingapi"
        )

    if effective is UpdatePolicy.WARN:
        logger.info("update available: %s -> %s (policy=warn)", info.installed, info.latest)
        _notify()
        with _gate_lock:
            _gate_state["done"] = True
            _gate_state["info"] = info
        return info

    # STRICT / ASK
    is_interactive_mode = interactive if interactive is not None else _config.is_interactive()
    logger.info(
        "update available: %s -> %s (policy=%s interactive=%s)",
        info.installed,
        info.latest,
        effective.value,
        is_interactive_mode,
    )

    if is_interactive_mode:
        latest_display = info.latest or "unknown"
        _print_notice(
            [
                f"A newer All Trading API SDK is available: {info.installed} -> {latest_display}",
                "This SDK requires the update to operate. Update now? [Y/n]",
            ]
        )
        reader = input_fn or _timed_input
        answer = (reader("") or "").strip().lower()
        if answer in ("", "y", "yes"):
            ok, tail = install_update(installer=installer)
            if ok:
                emit(f"Updated to {info.latest}. Restart your program to load the new code.")
                with _gate_lock:
                    _gate_state["done"] = True
                    _gate_state["info"] = info
                    _gate_state["blocked"] = True
                raise _build_error(info, installed_now=True)
            emit(
                "Automatic update failed. Update manually:\n"
                "    pip install --upgrade alltradingapi\n"
                f"pip output tail:\n{tail}"
            )
            _block(info)
            raise _build_error(info, installed_now=False)
        # Declined
        if effective is UpdatePolicy.ASK:
            logger.warning("update declined; continuing (policy=ask)")
            _notify()
            with _gate_lock:
                _gate_state["done"] = True
                _gate_state["info"] = info
            return info
        _block(info)
        raise _build_error(info, installed_now=False)

    # Non-interactive STRICT/ASK: block immediately, never wait on stdin.
    _block(info)
    raise _build_error(info, installed_now=False)


def _block(info: UpdateInfo) -> None:
    with _gate_lock:
        _gate_state["done"] = True
        _gate_state["info"] = info
        _gate_state["blocked"] = True


def _build_error(info: UpdateInfo | None, installed_now: bool) -> UpdateRequiredError:
    latest = info.latest if info and info.latest else "the latest version"
    installed = info.installed if info else "the installed version"
    if installed_now:
        message = (
            f"Updated to {latest} successfully, but this process still runs the old "
            f"code ({installed}). Restart your program to load the updated SDK."
        )
    else:
        message = (
            f"A newer All Trading API SDK is required: installed {installed} but {latest} is "
            "available. This SDK refuses to operate when outdated.\n"
            "Update now:\n"
            "    pip install --upgrade alltradingapi\n"
            "Escape hatches (documented, not silent): set ATA_UPDATE_POLICY=off, "
            "ATA_SKIP_UPDATE=1, or pass Client(allow_outdated=True)."
        )
    return UpdateRequiredError(
        message,
        installed=installed,
        latest=info.latest if info else None,
        installed_now=installed_now,
    )
