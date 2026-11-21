#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def run(command, **kwargs):
    return subprocess.run(
        command,
        check=True,
        text=True,
        capture_output=True,
        **kwargs,
    )


def get_repositories():
    """Query GitHub for all repositories accessible to the account."""

    result = run(
        [
            "gh",
            "api",
            "--paginate",
            "/user/repos",
            "-f",
            "affiliation=owner,organization_member",
            "-f",
            "per_page=100",
            "--jq",
            ".[] | [.full_name, .clone_url] | @tsv",
        ]
    )

    repositories = []

    for line in result.stdout.splitlines():
        full_name, clone_url = line.split("\t", 1)
        repositories.append((full_name, clone_url))

    return repositories


def backup_repository(full_name, clone_url, backup_dir):
    """Create or update a Git mirror."""

    destination = backup_dir / f"{full_name}.git"
    destination.parent.mkdir(parents=True, exist_ok=True)

    print(f"[{full_name}]")

    if destination.exists():
        # Verify this is a valid bare Git repository.
        try:
            run(["git", "-C", str(destination), "rev-parse", "--is-bare-repository"])
        except subprocess.CalledProcessError:
            print("  ERROR: Existing backup is not a valid Git mirror")
            return False

        try:
            result = run(
                [
                    "git",
                    "-C",
                    str(destination),
                    "remote",
                    "set-url",
                    "origin",
                    clone_url,
                ]
            )

            run(["git", "-C", str(destination), "remote", "update", "--prune"])

            print("  Updated")
            return True

        except subprocess.CalledProcessError as e:
            print(f"  ERROR: {e.stderr.strip()}")
            return False

    else:
        try:
            run(
                [
                    "git",
                    "clone",
                    "--mirror",
                    clone_url,
                    str(destination),
                ]
            )

            print("  Cloned")
            return True

        except subprocess.CalledProcessError as e:
            print(f"  ERROR: {e.stderr.strip()}")
            return False


def main():
    parser = argparse.ArgumentParser(description="Back up all GitHub repositories.")

    parser.add_argument(
        "--output",
        type=Path,
        default=Path.home() / "github-backups",
        help="Backup destination directory",
    )

    args = parser.parse_args()
    backup_dir = args.output.expanduser().resolve()

    print(f"GitHub backup started: {datetime.now()}")
    print(f"Destination: {backup_dir}\n")

    try:
        repositories = get_repositories()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"ERROR: Unable to query GitHub: {e}", file=sys.stderr)
        return 1

    print(f"Found {len(repositories)} repositories\n")

    successful = 0
    failed = 0

    for full_name, clone_url in repositories:
        if backup_repository(full_name, clone_url, backup_dir):
            successful += 1
        else:
            failed += 1

    print("\n" + "=" * 45)
    print(f"Backup completed: {datetime.now()}")
    print(f"Total:     {len(repositories)}")
    print(f"Successful: {successful}")
    print(f"Failed:     {failed}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
