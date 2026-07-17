"""Tests for starting unset weekly usage windows."""

import json
import os
import stat
import subprocess
from datetime import datetime, timezone

import codexauth.weekly as weekly_module
from codexauth.usage import UsageResult, UsageWindow


def test_weekly_window_needs_start_for_missing_or_zero_use_window():
    assert weekly_module.weekly_window_needs_start(UsageResult()) is True
    assert weekly_module.weekly_window_needs_start(
        UsageResult(windows={"secondary_window": UsageWindow("secondary_window")})
    ) is True
    assert weekly_module.weekly_window_needs_start(
        UsageResult(
            windows={
                "secondary_window": UsageWindow(
                    "secondary_window",
                    used_pct=0,
                    reset_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
                    limit_window_seconds=604800,
                    reset_after_seconds=604800,
                )
            }
        )
    ) is True
    assert weekly_module.weekly_window_needs_start(
        UsageResult(
            windows={
                "secondary_window": UsageWindow(
                    "secondary_window",
                    used_pct=25,
                    reset_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
                    limit_window_seconds=604800,
                    reset_after_seconds=604800,
                )
            }
        )
    ) is True
    assert weekly_module.weekly_window_needs_start(
        UsageResult(
            windows={
                "secondary_window": UsageWindow(
                    "secondary_window",
                    used_pct=0,
                    reset_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
                    limit_window_seconds=604800,
                    reset_after_seconds=500000,
                )
            }
        )
    ) is False
    assert weekly_module.weekly_window_needs_start(
        UsageResult(
            secondary_pct=1,
            secondary_reset_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
        )
    ) is False
    assert weekly_module.weekly_window_needs_start(UsageResult(error="n/a")) is False


def test_start_weekly_timer_uses_isolated_auth_and_preserves_refresh(
    monkeypatch, sample_profile
):
    monkeypatch.setattr(weekly_module.shutil, "which", lambda name: "/usr/bin/codex")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("CODEX_API_KEY", "must-not-leak")
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "must-not-leak")
    observed = {}

    def fake_run(command, **kwargs):
        codex_home = weekly_module.Path(kwargs["env"]["CODEX_HOME"])
        auth_path = codex_home / "auth.json"
        workspace = codex_home / "workspace"
        observed["command"] = command
        observed["env"] = kwargs["env"]
        observed["auth"] = json.loads(auth_path.read_text())
        observed["auth_mode"] = stat.S_IMODE(auth_path.stat().st_mode)
        observed["home_mode"] = stat.S_IMODE(codex_home.stat().st_mode)
        observed["workspace"] = workspace
        refreshed = json.loads(auth_path.read_text())
        refreshed["tokens"]["access_token"] = "refreshed-access"
        auth_path.write_text(json.dumps(refreshed))
        return subprocess.CompletedProcess(command, 0, stdout="hi\n", stderr="")

    monkeypatch.setattr(weekly_module.subprocess, "run", fake_run)

    result = weekly_module.start_weekly_timer(sample_profile, model="gpt-test")

    assert result.succeeded is True
    assert result.updated_profile["tokens"]["access_token"] == "refreshed-access"
    assert observed["auth"] == sample_profile
    assert observed["auth_mode"] == 0o600
    assert observed["home_mode"] == 0o700
    assert observed["workspace"].exists() is False
    assert observed["command"][0:2] == ["/usr/bin/codex", "exec"]
    assert "--ephemeral" in observed["command"]
    assert "--ignore-user-config" in observed["command"]
    assert "--ignore-rules" in observed["command"]
    assert "read-only" in observed["command"]
    assert observed["command"][-1] == weekly_module.WEEKLY_START_PROMPT
    assert observed["command"][observed["command"].index("-m") + 1] == "gpt-test"
    assert observed["env"]["CODEX_HOME"]
    assert "OPENAI_API_KEY" not in observed["env"]
    assert "CODEX_API_KEY" not in observed["env"]
    assert "CODEX_ACCESS_TOKEN" not in observed["env"]


def test_start_weekly_timer_reports_missing_codex(monkeypatch, sample_profile):
    monkeypatch.setattr(weekly_module.shutil, "which", lambda name: None)

    result = weekly_module.start_weekly_timer(sample_profile)

    assert result.succeeded is False
    assert "not found on PATH" in result.detail


def test_start_weekly_timer_preserves_refresh_when_request_times_out(
    monkeypatch, sample_profile
):
    monkeypatch.setattr(weekly_module.shutil, "which", lambda name: "/usr/bin/codex")

    def fake_run(command, **kwargs):
        auth_path = weekly_module.Path(kwargs["env"]["CODEX_HOME"]) / "auth.json"
        refreshed = json.loads(auth_path.read_text())
        refreshed["tokens"]["access_token"] = "refreshed-before-timeout"
        auth_path.write_text(json.dumps(refreshed))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(weekly_module.subprocess, "run", fake_run)

    result = weekly_module.start_weekly_timer(sample_profile, timeout=3)

    assert result.succeeded is False
    assert "timed out after 3 seconds" in result.detail
    assert result.updated_profile["tokens"]["access_token"] == "refreshed-before-timeout"
