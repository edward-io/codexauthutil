"""Start an unset Codex weekly usage window with a minimal isolated request."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from codexauth.usage import UsageResult, WEEKLY_WINDOW_SECONDS

DEFAULT_WEEKLY_START_MODEL = "gpt-5.4"
WEEKLY_START_TIMEOUT_SECONDS = 120
WEEKLY_START_PROMPT = "Reply with exactly: hi. Do not use tools."


@dataclass
class WeeklyStartResult:
    succeeded: bool
    detail: str | None = None
    updated_profile: dict | None = None


def weekly_window_needs_start(usage: UsageResult) -> bool:
    """Return whether the weekly reset is null or its full seven-day duration remains."""
    if usage.error is not None:
        return False
    weekly_window = usage.windows.get("secondary_window")
    if weekly_window is None or weekly_window.reset_at is None:
        return True
    return weekly_window.reset_after_seconds == WEEKLY_WINDOW_SECONDS


def _last_output_line(completed: subprocess.CompletedProcess[str]) -> str | None:
    output = "\n".join(part for part in (completed.stderr, completed.stdout) if part)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return None
    return lines[-1][:300]


def start_weekly_timer(
    profile: dict,
    *,
    model: str = DEFAULT_WEEKLY_START_MODEL,
    codex_binary: str = "codex",
    timeout: int = WEEKLY_START_TIMEOUT_SECONDS,
) -> WeeklyStartResult:
    """Run one minimal Codex request using a profile without changing active auth."""
    binary = shutil.which(codex_binary)
    if binary is None:
        return WeeklyStartResult(False, f"{codex_binary!r} was not found on PATH")

    updated_profile = None
    with tempfile.TemporaryDirectory(prefix="codexauth-weekly-") as temp_dir:
        codex_home = Path(temp_dir)
        codex_home.chmod(0o700)
        workspace = codex_home / "workspace"
        workspace.mkdir(mode=0o700)
        auth_path = codex_home / "auth.json"
        auth_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        auth_path.chmod(0o600)

        env = os.environ.copy()
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"):
            env.pop(key, None)
        env["CODEX_HOME"] = str(codex_home)

        command = [
            binary,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "-C",
            str(workspace),
            "-m",
            model,
            "-c",
            'model_reasoning_effort="low"',
            "-c",
            'cli_auth_credentials_store="file"',
            WEEKLY_START_PROMPT,
        ]

        completed = None
        failure_detail = None
        try:
            completed = subprocess.run(
                command,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            failure_detail = f"Codex request timed out after {timeout} seconds"
        except OSError as exc:
            failure_detail = f"Could not run Codex: {exc}"
        finally:
            try:
                candidate = json.loads(auth_path.read_text(encoding="utf-8"))
                if isinstance(candidate, dict) and candidate != profile:
                    updated_profile = candidate
            except (OSError, json.JSONDecodeError):
                pass

        if failure_detail is not None:
            return WeeklyStartResult(False, failure_detail, updated_profile)
        assert completed is not None
        if completed.returncode != 0:
            detail = _last_output_line(completed) or f"Codex exited with status {completed.returncode}"
            return WeeklyStartResult(False, detail, updated_profile)
        return WeeklyStartResult(True, updated_profile=updated_profile)
