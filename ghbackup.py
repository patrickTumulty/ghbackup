#!/usr/bin/env python3
"""Mirror every GitHub repo the authenticated `gh` user can access."""

import argparse
import json
import os
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "ghbackup" / "config.toml"
DEFAULT_BACKUP_DIR = Path.home() / "github-backups"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "ghbackup"
STATUS_PATH = STATE_DIR / "last_run.json"


def run(*cmd):
    return subprocess.run(cmd, check=True, text=True, capture_output=True).stdout


def get_repositories():
    out = run(
        "gh",
        "api",
        "--paginate",
        "/user/repos?affiliation=owner,organization_member&per_page=100",
        "--jq",
        ".[] | [.full_name, .clone_url] | @tsv",
    )
    return [line.split("\t") for line in out.splitlines()]


def backup(full_name, clone_url, backup_dir):
    dest = backup_dir / f"{full_name}.git"
    try:
        if dest.exists():
            # --git-dir (not -C) so a broken mirror errors instead of touching a parent repo.
            run("git", "--git-dir", dest, "remote", "set-url", "origin", clone_url)
            run("git", "--git-dir", dest, "remote", "update", "--prune")
            status = "updated"
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            run("git", "clone", "--mirror", clone_url, dest)
            status = "cloned"
    except subprocess.CalledProcessError as e:
        error = e.stderr.strip()
        print(f"  {full_name}: ERROR {error}")
        return {"name": full_name, "ok": False, "status": "error", "error": error}
    print(f"  {full_name}: {status}")
    return {"name": full_name, "ok": True, "status": status}


def write_status(**status):
    """Record the outcome of a run for the web UI (webui.py)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(status, indent=2))
    tmp.replace(STATUS_PATH)


def load_config():
    return tomllib.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}


def main():
    parser = argparse.ArgumentParser(description="Back up all GitHub repositories.")
    parser.add_argument("--output", type=Path, help=f"overrides {CONFIG_PATH}")
    output = parser.parse_args().output
    output = output or Path(load_config().get("backup_dir", DEFAULT_BACKUP_DIR))
    backup_dir = output.expanduser().resolve()

    started = datetime.now().astimezone()
    print(f"=== {started:%Y-%m-%d %H:%M:%S} backing up to {backup_dir}")
    try:
        repos = get_repositories()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        error = str(getattr(e, "stderr", None) or e).strip()
        print(f"ERROR: unable to query GitHub: {error}")
        write_status(
            started=started.isoformat(),
            finished=datetime.now().astimezone().isoformat(),
            backup_dir=str(backup_dir),
            ok=False,
            error=f"unable to query GitHub: {error}",
            repos=[],
        )
        return 1

    results = [backup(name, url, backup_dir) for name, url in repos]
    failed = sum(not r["ok"] for r in results)
    print(f"=== done: {len(repos) - failed}/{len(repos)} ok, {failed} failed")
    write_status(
        started=started.isoformat(),
        finished=datetime.now().astimezone().isoformat(),
        backup_dir=str(backup_dir),
        ok=not failed,
        repos=results,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
