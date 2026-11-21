#!/usr/bin/env python3
"""Mirror every GitHub repo the authenticated `gh` user can access."""

import argparse
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "ghbackup" / "config.toml"
DEFAULT_BACKUP_DIR = Path.home() / "github-backups"


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
        print(f"  {full_name}: ERROR {e.stderr.strip()}")
        return False
    print(f"  {full_name}: {status}")
    return True


def load_config():
    return tomllib.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}


def main():
    parser = argparse.ArgumentParser(description="Back up all GitHub repositories.")
    parser.add_argument("--output", type=Path, help=f"overrides {CONFIG_PATH}")
    output = parser.parse_args().output
    output = output or Path(load_config().get("backup_dir", DEFAULT_BACKUP_DIR))
    backup_dir = output.expanduser().resolve()

    print(f"=== {datetime.now():%Y-%m-%d %H:%M:%S} backing up to {backup_dir}")
    try:
        repos = get_repositories()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"ERROR: unable to query GitHub: {getattr(e, 'stderr', e)}")
        return 1

    failed = sum(not backup(name, url, backup_dir) for name, url in repos)
    print(f"=== done: {len(repos) - failed}/{len(repos)} ok, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
