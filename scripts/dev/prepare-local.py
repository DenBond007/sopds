#!/usr/bin/env python3
"""Create dev-only local directories and credentials without touching a database.

Run as the repository owner, not root. Existing credentials are never overwritten.
No dependencies beyond Python's standard library; --root is useful for validation.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import sys


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.PIPE
    ).strip()


def write_private(path: Path, content: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError("Run as the ordinary repository owner, without sudo.")
    root = args.root.expanduser().resolve()
    if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise RuntimeError("--root must point to the Git repository root.")
    if git(root, "ls-files", "--", ".local"):
        raise RuntimeError("Tracked .local files found. Review them before creating secrets.")

    local = root / ".local"
    for path in [local, *(local / name for name in ("library", "tmp", "logs", "uv-cache", "backups"))]:
        if path.is_symlink():
            raise RuntimeError(f"Refusing symlink: {path}")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)

    # Local Git exclusion also protects files when a project .gitignore is absent.
    exclude = Path(git(root, "rev-parse", "--git-path", "info/exclude"))
    if not exclude.is_absolute():
        exclude = root / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    marker = "# SOPDS modernization local data"
    if marker not in text:
        with exclude.open("a", encoding="utf-8") as stream:
            stream.write("\n" + marker + "\n.local/\n.venv/\n.venv-*/\n.env\n.env.*\n!.env.example\n")

    db_env, app_env = local / "postgres.env", local / "dev.env"
    if db_env.is_symlink() or app_env.is_symlink():
        raise RuntimeError("Refusing a symlink in credential paths.")
    if db_env.exists() != app_env.exists():
        raise RuntimeError("Only one env file exists. Restore the matching file; do not regenerate database passwords.")
    if db_env.exists():
        print("Existing .local/postgres.env and .local/dev.env left unchanged.")
        print("If the checkout moved, review non-secret path values in dev.env locally.")
        return 0

    app_password = secrets.token_hex(24)
    db_content = (
        f"SOPDS_PG_ADMIN_PASSWORD={secrets.token_hex(32)}\n"
        f"SOPDS_DEV_PASSWORD={app_password}\n"
    )
    app_vars = {
        "DB_NAME": "sopds_dev",
        "DB_USER": "sopds_dev",
        "DB_PASS": app_password,
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "5433",
        "TIME_ZONE": "Europe/Berlin",
        # These two names are instructions for a future dev launcher, not old SOPDS settings.
        "SOPDS_DEV_LIBRARY": str(local / "library"),
        "SOPDS_DEV_TMP": str(local / "tmp"),
        "UV_CACHE_DIR": str(local / "uv-cache"),
    }
    app_content = "# Development-only variables. Never print or commit this file.\n"
    app_content += "".join(f"{key}={shlex.quote(value)}\n" for key, value in app_vars.items())
    write_private(db_env, db_content)
    write_private(app_env, app_content)
    print("Created local directories and two dev-only env files (mode 0600).")
    print("No database has been started or changed; no credentials printed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Preparation stopped: {exc}", file=sys.stderr)
        raise SystemExit(1)
