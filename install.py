#!/usr/bin/env python3
"""Install or remove the ghbackup systemd user timer (used by activate.sh / deactivate.sh)."""

import argparse
import getpass
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ghbackup import CONFIG_PATH, DEFAULT_BACKUP_DIR, load_config

UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
SERVICE = UNIT_DIR / "ghbackup.service"
TIMER = UNIT_DIR / "ghbackup.timer"
DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def systemctl(*args, check=False, **kwargs):
    subprocess.run(["systemctl", "--user", *args], check=check, **kwargs)


def cron_to_oncalendar(expr):
    """Convert 'm h dom mon dow' to a systemd OnCalendar expression."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"expected 5 fields, got {len(fields)}")
    minute, hour, dom, mon, dow = fields
    if dom != "*" and dow != "*":
        raise ValueError("cron ORs day-of-month and day-of-week; restrict only one")

    def conv(field, start="0"):
        return field.replace("*/", f"{start}/").replace("-", "..")

    days = ""
    if dow != "*":
        days = (
            re.sub(r"\d", lambda m: DAYS[int(m[0])], dow.title()).replace("-", "..")
            + " "
        )
    return f"{days}*-{conv(mon, '1')}-{conv(dom, '1')} {conv(hour)}:{conv(minute)}:00"


def next_run(calendar):
    """Validate a calendar expression with systemd and return its next run time."""
    result = subprocess.run(
        ["systemd-analyze", "calendar", calendar],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return next(
        l.split(":", 1)[1].strip()
        for l in result.stdout.splitlines()
        if "Next elapse" in l
    )


def ask(prompt, value, default):
    """Use the CLI value, else prompt (when interactive), else the default."""
    if value or not sys.stdin.isatty():
        return value or str(default)
    return input(f"{prompt} [{default}]: ").strip() or str(default)


def install(args):
    gh = shutil.which("gh")
    if not gh:
        sys.exit("gh CLI not found: install it and run `gh auth login` first")

    config = load_config()
    backup_dir = ask(
        "Backup directory", args.dir, config.get("backup_dir", DEFAULT_BACKUP_DIR)
    )
    backup_dir = Path(backup_dir).expanduser().resolve()
    schedule = ask(
        "Schedule (cron syntax)", args.schedule, config.get("schedule", "0 3 * * *")
    )
    try:
        calendar = cron_to_oncalendar(schedule)
        upcoming = next_run(calendar)
    except ValueError as e:
        sys.exit(f"Invalid schedule {schedule!r}: {e}")

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        f"backup_dir = {json.dumps(str(backup_dir))}\nschedule = {json.dumps(schedule)}\n"
    )
    backup_dir.mkdir(parents=True, exist_ok=True)

    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    SERVICE.write_text(f"""[Unit]
Description=Back up GitHub repositories
StartLimitIntervalSec=1h
StartLimitBurst=3

[Service]
Type=oneshot
Environment="PATH={Path(gh).parent}:/usr/local/bin:/usr/bin:/bin"
ExecStart="{sys.executable}" -u "{Path(__file__).resolve().parent / "ghbackup.py"}"
Restart=on-failure
RestartSec=10min
""")
    TIMER.write_text(f"""[Unit]
Description=Nightly GitHub backup

[Timer]
OnCalendar={calendar}
Persistent=true

[Install]
WantedBy=timers.target
""")
    systemctl("daemon-reload", check=True)
    systemctl("enable", "--now", "ghbackup.timer", check=True)

    print(f"\nBackups to: {backup_dir}\nSchedule:   {schedule}  ({calendar})")
    print(
        f"Next run:   {upcoming}\nConfig:     {CONFIG_PATH}\nLogs:       journalctl --user -u ghbackup"
    )
    linger = subprocess.run(
        ["loginctl", "show-user", getpass.getuser(), "-p", "Linger", "--value"],
        check=False,
        text=True,
        capture_output=True,
    ).stdout.strip()
    if linger != "yes":
        print(
            f"\nNOTE: to run while logged out, run once: sudo loginctl enable-linger {getpass.getuser()}"
        )


def uninstall(_):
    systemctl("disable", "--now", "ghbackup.timer", stderr=subprocess.DEVNULL)
    SERVICE.unlink(missing_ok=True)
    TIMER.unlink(missing_ok=True)
    systemctl("daemon-reload", check=True)
    print(f"ghbackup timer removed. Config kept at {CONFIG_PATH}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(required=True)
    p = sub.add_parser("install", help="prompt for settings and enable the timer")
    p.add_argument("--dir", help="backup directory")
    p.add_argument("--schedule", help='cron expression, e.g. "0 3 * * *"')
    p.set_defaults(func=install)
    sub.add_parser("uninstall", help="disable the timer").set_defaults(func=uninstall)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
