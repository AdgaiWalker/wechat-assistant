#!/usr/bin/env python3
"""Hermes cron wake gate for WeChat local database availability.

When stdout's last non-empty line is {"wakeAgent": false}, Hermes skips the
LLM call and delivery. This prevents repeated noisy cron failures while local
WeChat is logged out or the local database is not ready after login.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


DEFAULT_SCRIPT_DIR = "~/.hermes/skills/social-media/wechat-assistant/scripts"


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(path)).resolve()


def _script_dir() -> Path:
    return _expand(os.environ.get("WECHAT_ASSISTANT_SCRIPT_DIR", DEFAULT_SCRIPT_DIR))


def _config_path() -> Path:
    return _expand(os.environ.get("WECHAT_ASSISTANT_CONFIG", "~/wechat-assistant/config.yaml"))


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, capture_output=True, timeout=timeout)


def _load_config(config: Path) -> dict:
    script_dir = _script_dir()
    sys.path.insert(0, str(script_dir / "decrypt"))
    from config import load_config

    return load_config(str(config))


def _wechat_running() -> bool:
    patterns = ("WeChat", "Weixin", "xinWeChat", "微信")
    try:
        proc = _run(["ps", "-axo", "comm="], timeout=10)
    except Exception:
        return False
    if proc.returncode != 0:
        return False
    return any(pattern in proc.stdout for pattern in patterns)


def _command_detail(proc: subprocess.CompletedProcess[str]) -> str:
    output = "\n".join(x.strip() for x in (proc.stderr, proc.stdout) if x.strip())
    return output[:800] if output else f"exit {proc.returncode}"


def _refresh(script_dir: Path, config: Path, full: bool = False) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(script_dir / "refresh_decrypt.py"), "--config", str(config)]
    if full:
        cmd.append("--full")
    return _run(cmd, cwd=script_dir, timeout=300 if full else 180)


def _sync_after_recovery(script_dir: Path, config: Path) -> tuple[bool, str]:
    recent_hours = os.environ.get("WECHAT_ASSISTANT_RECOVER_HOURS", "168")
    sync = _run(
        [
            sys.executable,
            str(script_dir / "collector.py"),
            "--config",
            str(config),
            "--sync",
            "--recent-hours",
            recent_hours,
        ],
        cwd=script_dir,
        timeout=300,
    )
    if sync.returncode == 0:
        return True, sync.stdout.strip()[:1000]
    return False, _command_detail(sync)


def _gate(wake: bool, status: str, detail: str = "") -> int:
    if detail:
        print(f"[wechat-health] {status}: {detail}")
    else:
        print(f"[wechat-health] {status}")
    print(json.dumps({"wakeAgent": wake, "status": status}, ensure_ascii=False))
    return 0


def main() -> int:
    config = _config_path()
    script_dir = _script_dir()

    if not config.exists():
        return _gate(False, "config_missing", str(config))

    try:
        cfg = _load_config(config)
    except Exception as exc:
        return _gate(False, "config_error", str(exc))

    db_dir = Path(cfg.get("db_dir") or "")
    keys_file = Path(cfg.get("keys_file") or "")

    if not _wechat_running():
        return _gate(False, "wechat_not_running", "local WeChat process is not active")
    if not db_dir.is_dir():
        return _gate(False, "db_dir_missing", str(db_dir))
    if not keys_file.exists():
        return _gate(False, "keys_missing", f"stored key file not found: {keys_file}")

    refresh = _refresh(script_dir, config)
    output = "\n".join(x for x in [refresh.stderr.strip(), refresh.stdout.strip()] if x)

    if refresh.returncode == 2:
        refresh = _refresh(script_dir, config, full=True)
        if refresh.returncode == 0:
            synced, sync_detail = _sync_after_recovery(script_dir, config)
            if synced:
                return _gate(True, "ok_after_full_refresh", sync_detail)
            return _gate(False, "sync_failed_after_full_refresh", sync_detail)
        detail = _command_detail(refresh) or output
        return _gate(
            False,
            "wechat_db_not_ready",
            "stored key did not validate yet; wait for local WeChat login/sync to finish. " + detail[:700],
        )

    if refresh.returncode != 0:
        return _gate(False, "refresh_failed", output[:1000])

    return _gate(True, "ok", refresh.stdout.strip()[:1000])


if __name__ == "__main__":
    raise SystemExit(main())
