"""Update system: comparator, cache TTL, all policies, gate blocking."""

from __future__ import annotations

import os
from typing import Any

import pytest

from alltradingapi import config as _config
from alltradingapi import update as update_module
from alltradingapi.errors import UpdateCheckError, UpdateRequiredError


# ---------------------------------------------------------------------------
# Version comparator
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("1.0.0", "1.0.0", 0),
        ("1.2.0", "1.10.0", -1),
        ("1.10.0", "1.2.0", 1),
        ("1.0.0", "1.0.1", -1),
        ("2.0", "1.9.9", 1),
        ("1.0.0", "1.0.0rc1", 1),
        ("1.0.0rc1", "1.0.0rc2", -1),
        ("1.0.0a1", "1.0.0b1", -1),
        ("1.0.0b1", "1.0.0rc1", -1),
        ("1.0.0.dev1", "1.0.0", -1),
        ("1.0.0", "1.0.0.post1", -1),
        ("1.0.0.post1", "1.0.0.post2", -1),
        ("1!1.0", "2.0", 1),  # epoch dominates
        ("1.0.0+local", "1.0.0", 0),  # local segment ignored
        ("garbage", "1.0.0", 0),  # unparsable fails open
        (None, "1.0.0", 0),
    ],
)
def test_compare_versions(a: str | None, b: str, expected: int) -> None:
    assert update_module.compare_versions(a, b) == expected


def test_is_outdated() -> None:
    assert update_module.is_outdated("1.0.0", "1.2.0") is True
    assert update_module.is_outdated("1.2.0", "1.0.0") is False
    assert update_module.is_outdated("1.0.0", "1.0.0") is False
    assert update_module.is_outdated(None, "1.0.0") is False
    assert update_module.is_outdated("1.0.0", None) is False
    assert update_module.is_outdated("junk", "1.0.0") is False


def test_parse_version_garbage() -> None:
    assert update_module.parse_version("not-a-version!!") is None
    assert update_module.parse_version("") is None


# ---------------------------------------------------------------------------
# fetch_latest fail-open
# ---------------------------------------------------------------------------
def test_fetch_latest_bad_url_is_none() -> None:
    assert update_module.fetch_latest("http://127.0.0.1:1/pypi/x/json", timeout=0.2) is None
    assert update_module.fetch_latest("ftp://nope", timeout=0.2) is None


# ---------------------------------------------------------------------------
# check_for_update with injected fetcher + fake clock
# ---------------------------------------------------------------------------
def _fake_fetcher(latest: str | None) -> Any:
    calls = {"count": 0}

    def fetcher(url: str, timeout: float) -> str | None:
        calls["count"] += 1
        return latest

    fetcher.calls = calls  # type: ignore[attr-defined]
    return fetcher


def test_check_for_update_outdated(update_cache_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = _fake_fetcher("2.0.0")
    info = update_module.check_for_update(
        installed="1.0.0", cache_dir=update_cache_dir, fetcher=fetcher
    )
    assert info.is_outdated is True
    assert info.latest == "2.0.0"
    assert info.known is True
    assert fetcher.calls["count"] == 1
    # disk cache written
    cached = update_module.read_cache(update_cache_dir)
    assert cached is not None and cached["latest"] == "2.0.0"


def test_check_for_update_cache_ttl(
    update_cache_dir: str, fake_clock: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetcher = _fake_fetcher("2.0.0")
    monkeypatch.setattr(update_module.time, "time", fake_clock)
    first = update_module.check_for_update(
        installed="1.0.0", cache_dir=update_cache_dir, fetcher=fetcher, ttl=3600
    )
    assert first.is_outdated is True
    fake_clock.advance(100)
    second = update_module.check_for_update(
        installed="1.0.0", cache_dir=update_cache_dir, fetcher=fetcher, ttl=3600
    )
    assert second.latest == "2.0.0"
    assert fetcher.calls["count"] == 1  # served from cache
    fake_clock.advance(4000)  # beyond TTL
    update_module.check_for_update(
        installed="1.0.0", cache_dir=update_cache_dir, fetcher=fetcher, ttl=3600
    )
    assert fetcher.calls["count"] == 2


def test_check_for_update_offline_fail_open(update_cache_dir: str) -> None:
    fetcher = _fake_fetcher(None)
    info = update_module.check_for_update(
        installed="1.0.0", cache_dir=update_cache_dir, fetcher=fetcher
    )
    assert info.known is False
    assert info.is_outdated is False  # fail-open
    assert fetcher.calls["count"] == 1


def test_check_for_update_fallback_version(update_cache_dir: str) -> None:
    # installed=None resolves through importlib.metadata with the packaged
    # fallback version; the check still works and never explodes.
    info = update_module.check_for_update(
        installed=None, cache_dir=update_cache_dir, fetcher=_fake_fetcher("9.9.9")
    )
    from alltradingapi._version import __version__ as expected

    assert info.installed == expected
    assert info.is_outdated is True


def test_update_check_error_construction() -> None:
    error = UpdateCheckError("bad local version config")
    assert error.code == "update_check_error"


def test_resolve_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_config.ENV_UPDATE_POLICY, raising=False)
    assert update_module.resolve_policy() == update_module.UpdatePolicy.STRICT
    assert update_module.resolve_policy("warn") == update_module.UpdatePolicy.WARN
    monkeypatch.setenv(_config.ENV_UPDATE_POLICY, "off")
    assert update_module.resolve_policy() == update_module.UpdatePolicy.OFF
    monkeypatch.setenv(_config.ENV_UPDATE_POLICY, "garbage")
    assert update_module.resolve_policy() == update_module.UpdatePolicy.STRICT


# ---------------------------------------------------------------------------
# Gate behaviour
# ---------------------------------------------------------------------------
def _gate(monkeypatch: pytest.MonkeyPatch, latest: str, cache_dir: str, **kwargs: Any) -> Any:
    monkeypatch.delenv(_config.ENV_SKIP_UPDATE, raising=False)
    monkeypatch.delenv(_config.ENV_UPDATE_POLICY, raising=False)
    monkeypatch.setenv("ATA_UPDATE_TTL", "0")
    monkeypatch.setattr(update_module, "fetch_latest", lambda url, timeout: latest, raising=False)
    kwargs.setdefault("cache_dir", cache_dir)
    kwargs.setdefault("interactive", False)
    return update_module.ensure_usable("1.0.0", **kwargs)


def test_gate_strict_non_interactive_blocks(
    update_cache_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(UpdateRequiredError) as excinfo:
        _gate(monkeypatch, "2.0.0", update_cache_dir)
    error = excinfo.value
    assert error.installed == "1.0.0"
    assert error.latest == "2.0.0"
    assert error.installed_now is False
    assert "pip install --upgrade alltradingapi" in error.message
    assert "ATA_SKIP_UPDATE" in error.message


def test_gate_up_to_date_passes(update_cache_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
    info = _gate(monkeypatch, "1.0.0", update_cache_dir)
    assert info is not None and info.is_outdated is False


def test_gate_off_policy_no_network(update_cache_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(url: str, timeout: float) -> None:
        raise AssertionError("network check must not happen when policy=off")

    monkeypatch.delenv(_config.ENV_SKIP_UPDATE, raising=False)
    monkeypatch.setattr(update_module, "fetch_latest", explode, raising=False)
    result = update_module.ensure_usable("1.0.0", policy="off", cache_dir=update_cache_dir)
    assert result is None


def test_gate_skip_update_env(update_cache_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_config.ENV_SKIP_UPDATE, "1")
    result = update_module.ensure_usable("1.0.0", cache_dir=update_cache_dir)
    assert result is None


def test_gate_warn_continues(
    update_cache_dir: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    printed: list[str] = []

    def printer(text: str) -> None:
        printed.append(text)

    info = _gate(monkeypatch, "2.0.0", update_cache_dir, policy="warn", printer=printer)
    assert info is not None and info.is_outdated is True
    assert any("newer" in p for p in printed)


def test_gate_allow_outdated_equivalent_to_warn(
    update_cache_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    printed: list[str] = []
    info = _gate(
        monkeypatch,
        "2.0.0",
        update_cache_dir,
        allow_outdated=True,
        printer=lambda t: printed.append(t),
    )
    assert info is not None and info.is_outdated is True
    assert printed  # warned, not blocked


def test_gate_interactive_yes_installs(
    update_cache_dir: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    installer_calls: list[list[str]] = []

    def installer(command: list[str]) -> Any:
        installer_calls.append(command)
        return (0, "Successfully installed alltradingapi-2.0.0")

    with pytest.raises(UpdateRequiredError) as excinfo:
        _gate(
            monkeypatch,
            "2.0.0",
            update_cache_dir,
            interactive=True,
            installer=installer,
            input_fn=lambda p: "y",
        )
    assert excinfo.value.installed_now is True
    assert len(installer_calls) == 1
    assert "pip" in installer_calls[0]
    captured = capsys.readouterr()
    assert "Update now? [Y/n]" in captured.err


def test_gate_interactive_declined_strict_blocks(
    update_cache_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer_calls: list[list[str]] = []

    def installer(command: list[str]) -> Any:
        installer_calls.append(command)
        return (0, "")

    with pytest.raises(UpdateRequiredError):
        _gate(
            monkeypatch,
            "2.0.0",
            update_cache_dir,
            interactive=True,
            installer=installer,
            input_fn=lambda p: "n",
        )
    assert installer_calls == []


def test_gate_interactive_declined_ask_continues(
    update_cache_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    info = _gate(
        monkeypatch,
        "2.0.0",
        update_cache_dir,
        policy="ask",
        interactive=True,
        installer=lambda c: (0, ""),
        input_fn=lambda p: "n",
    )
    assert info is not None and info.is_outdated is True


def test_gate_interactive_default_yes(
    update_cache_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(UpdateRequiredError) as excinfo:
        _gate(
            monkeypatch,
            "2.0.0",
            update_cache_dir,
            interactive=True,
            installer=lambda c: (0, ""),
            input_fn=lambda p: "",
        )
    assert excinfo.value.installed_now is True


def test_gate_install_failure_blocks(
    update_cache_dir: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    with pytest.raises(UpdateRequiredError) as excinfo:
        _gate(
            monkeypatch,
            "2.0.0",
            update_cache_dir,
            interactive=True,
            installer=lambda c: (1, "ERROR: permission denied"),
            input_fn=lambda p: "y",
        )
    assert excinfo.value.installed_now is False
    captured = capsys.readouterr()
    assert "permission denied" in captured.err


def test_gate_non_interactive_never_prompts(
    update_cache_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(prompt: str) -> None:
        raise AssertionError("must not prompt when non-interactive")

    with pytest.raises(UpdateRequiredError):
        _gate(monkeypatch, "2.0.0", update_cache_dir, interactive=False, input_fn=explode)


def test_gate_blocked_state_persists(
    update_cache_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(UpdateRequiredError):
        _gate(monkeypatch, "2.0.0", update_cache_dir)
    # Second call must fail fast from cached gate state (no further check).
    with pytest.raises(UpdateRequiredError):
        update_module.ensure_usable("1.0.0", cache_dir=update_cache_dir)


def test_gate_pass_state_cached(update_cache_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = _fake_fetcher("1.0.0")
    monkeypatch.delenv(_config.ENV_SKIP_UPDATE, raising=False)
    monkeypatch.setattr(update_module, "fetch_latest", fetcher, raising=False)
    first = update_module.ensure_usable(
        "1.0.0", cache_dir=update_cache_dir, policy="strict", interactive=False
    )
    second = update_module.ensure_usable(
        "1.0.0", cache_dir=update_cache_dir, policy="strict", interactive=False
    )
    assert first is not None and second is not None
    assert fetcher.calls["count"] == 1


def test_gate_ci_env_never_prompts(update_cache_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")
    with pytest.raises(UpdateRequiredError):
        _gate(monkeypatch, "2.0.0", update_cache_dir, interactive=True)


def test_install_update_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    class Completed:
        returncode = 0
        stdout = "Successfully installed alltradingapi-2.0.0"
        stderr = ""

    monkeypatch.setattr(update_module.subprocess, "run", lambda *a, **kw: Completed())
    ok, output = update_module.install_update()
    assert ok is True
    assert "2.0.0" in output


def test_install_update_subprocess_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class Completed:
        returncode = 1
        stdout = ""
        stderr = "ERROR: no permission"

    monkeypatch.setattr(update_module.subprocess, "run", lambda *a, **kw: Completed())
    ok, output = update_module.install_update()
    assert ok is False
    assert "no permission" in output


def test_upgrade_command_shape() -> None:
    command = update_module.upgrade_command()
    assert command[-1] == "alltradingapi"
    assert "pip" in command


def test_cache_write_read_roundtrip(update_cache_dir: str) -> None:
    assert update_module.write_cache(
        {"checked_at": 1.0, "latest": "1.2.3", "source": "pypi"}, update_cache_dir
    )
    record = update_module.read_cache(update_cache_dir)
    assert record is not None and record["latest"] == "1.2.3"
    assert update_module.read_cache(os.path.join(update_cache_dir, "nope")) is None


def test_cache_corrupt_file_fails_open(update_cache_dir: str) -> None:
    path = update_module.cache_path(update_cache_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert update_module.read_cache(update_cache_dir) is None


def test_update_info_to_dict() -> None:
    info = update_module.UpdateInfo("1.0.0", "2.0.0", True, 123.0, "pypi", True)
    data = info.to_dict()
    assert data["installed"] == "1.0.0"
    assert data["latest"] == "2.0.0"
    assert data["is_outdated"] is True


def test_client_check_updates_never_raises_for_network() -> None:
    from alltradingapi.client import Client

    from .conftest import FakeTransport

    client = Client(api_key="x", transport=FakeTransport(), run_update_gate=False)

    def broken_fetch(url: str, timeout: float) -> str | None:
        raise RuntimeError("exploded")

    # check_for_update catches all exceptions internally via fetch_latest's
    # own guard, but ensure the client wrapper also survives a broken fetch.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(update_module, "fetch_latest", broken_fetch, raising=False)
    try:
        info = client.check_updates(force=True)
        assert info.installed == "1.0.1"
        assert info.known is False
    finally:
        monkeypatch.undo()
