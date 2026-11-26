#!/usr/bin/env python3
"""Install or remove the ghbackup systemd user timer (used by activate.sh / deactivate.sh)."""

import argparse
import getpass
import json
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from ghbackup import CONFIG_PATH, DEFAULT_BACKUP_DIR, load_config
from webui import DEFAULT_PORT as DEFAULT_WEB_PORT

UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
SERVICE = UNIT_DIR / "ghbackup.service"
TIMER = UNIT_DIR / "ghbackup.timer"
WEB_SERVICE = UNIT_DIR / "ghbackup-web.service"
DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

ALIASES_FILE = CONFIG_PATH.parent / "aliases.sh"
ALIASES = {
    "ghbstatus": "systemctl --user status ghbackup",
    "ghbstart": "systemctl --user start ghbackup",
    "ghbtimer": "systemctl --user list-timers ghbackup.timer",
    "ghblogs": "journalctl --user -u ghbackup -e",
    "ghbweb": "systemctl --user status ghbackup-web",
}
BASHRC = Path.home() / ".bashrc"
# The trailing marker lets uninstall find and remove exactly this line.
BASHRC_LINE = f"[ -f {ALIASES_FILE} ] && . {ALIASES_FILE}  # ghbackup"


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


def install_aliases():
    ALIASES_FILE.write_text(
        "".join(f"alias {name}='{cmd}'\n" for name, cmd in ALIASES.items())
    )
    bashrc = BASHRC.read_text() if BASHRC.exists() else ""
    if BASHRC_LINE not in bashrc.splitlines():
        with BASHRC.open("a") as f:
            f.write(("\n" if bashrc and not bashrc.endswith("\n") else "") + BASHRC_LINE + "\n")


def uninstall_aliases():
    ALIASES_FILE.unlink(missing_ok=True)
    if BASHRC.exists():
        lines = BASHRC.read_text().splitlines(keepends=True)
        BASHRC.write_text("".join(l for l in lines if l.rstrip("\n") != BASHRC_LINE))


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
    web_port = ask(
        "Web UI port (0 to disable)",
        args.web_port,
        config.get("web_port", DEFAULT_WEB_PORT),
    )
    try:
        web_port = int(web_port)
        if not 0 <= web_port <= 65535:
            raise ValueError
    except ValueError:
        sys.exit(f"Invalid web UI port {web_port!r}")

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        f"backup_dir = {json.dumps(str(backup_dir))}\nschedule = {json.dumps(schedule)}\n"
        f"web_port = {web_port}\n"
    )
    backup_dir.mkdir(parents=True, exist_ok=True)

    here = Path(__file__).resolve().parent
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    SERVICE.write_text(f"""[Unit]
Description=Back up GitHub repositories
StartLimitIntervalSec=1h
StartLimitBurst=3

[Service]
Type=oneshot
Environment="PATH={Path(gh).parent}:/usr/local/bin:/usr/bin:/bin"
ExecStart="{sys.executable}" -u "{here / "ghbackup.py"}"
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
    if web_port:
        WEB_SERVICE.write_text(f"""[Unit]
Description=ghbackup web UI

[Service]
ExecStart="{sys.executable}" -u "{here / "webui.py"}" --port {web_port}
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=default.target
""")
    else:
        systemctl("disable", "--now", "ghbackup-web.service", stderr=subprocess.DEVNULL)
        WEB_SERVICE.unlink(missing_ok=True)
    systemctl("daemon-reload", check=True)
    systemctl("enable", "--now", "ghbackup.timer", check=True)
    if web_port:
        systemctl("enable", "ghbackup-web.service", check=True)
        systemctl("restart", "ghbackup-web.service", check=True)
    install_aliases()

    print(f"\nBackups to: {backup_dir}\nSchedule:   {schedule}  ({calendar})")
    print(
        f"Next run:   {upcoming}\nConfig:     {CONFIG_PATH}\nLogs:       journalctl --user -u ghbackup"
    )
    if web_port:
        print(f"Web UI:     http://{socket.gethostname()}:{web_port}/")
    else:
        print("Web UI:     disabled")
    print(f"Aliases:    {', '.join(ALIASES)} (open a new shell or `source ~/.bashrc`)")
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
    systemctl("disable", "--now", "ghbackup-web.service", stderr=subprocess.DEVNULL)
    SERVICE.unlink(missing_ok=True)
    TIMER.unlink(missing_ok=True)
    WEB_SERVICE.unlink(missing_ok=True)
    systemctl("daemon-reload", check=True)
    uninstall_aliases()
    print(f"ghbackup timer, web UI and aliases removed. Config kept at {CONFIG_PATH}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(required=True)
    p = sub.add_parser("install", help="prompt for settings and enable the timer")
    p.add_argument("--dir", help="backup directory")
    p.add_argument("--schedule", help='cron expression, e.g. "0 3 * * *"')
    p.add_argument("--web-port", help=f"web UI port, 0 to disable (default {DEFAULT_WEB_PORT})")
    p.set_defaults(func=install)
    sub.add_parser("uninstall", help="disable the timer").set_defaults(func=uninstall)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
