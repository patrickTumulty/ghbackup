#!/usr/bin/env python3
"""A small read-only web page showing the state of the ghbackup mirrors.

Serves `/` (HTML) and `/api/status` (JSON). Standard library only.
"""

import argparse
import html
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ghbackup import DEFAULT_BACKUP_DIR, STATUS_PATH, load_config

DEFAULT_PORT = 8080


def disk_usage(path):
    """Bytes on disk used by everything under path, like `du -s`."""
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_blocks * 512
            except OSError:
                pass
    return total


def last_fetched(repo):
    """When the mirror was last cloned or updated."""
    for marker in ("FETCH_HEAD", "packed-refs", "HEAD"):
        try:
            return datetime.fromtimestamp((repo / marker).stat().st_mtime).astimezone()
        except OSError:
            continue
    return None


def load_last_run():
    try:
        return json.loads(STATUS_PATH.read_text())
    except (OSError, ValueError):
        return None


def systemd_info():
    """Whether a backup is running now and when the timer fires next."""
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show", "ghbackup.service", "ghbackup.timer",
             "-p", "ActiveState", "-p", "NextElapseUSecRealtime"],
            text=True, capture_output=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {"running": False, "next_run": None}
    props = {}
    for line in out.splitlines():
        key, _, value = line.partition("=")
        props.setdefault(key, []).append(value)
    running = props.get("ActiveState", [""])[0] in ("active", "activating")
    next_run = next((v for v in props.get("NextElapseUSecRealtime", []) if v), None)
    return {"running": running, "next_run": next_run}


def collect(backup_dir):
    last_run = load_last_run()
    results = {r["name"]: r for r in (last_run or {}).get("repos", [])}
    repos = []
    if backup_dir.is_dir():
        for repo in sorted(backup_dir.glob("*/*.git"), key=lambda p: str(p).lower()):
            name = f"{repo.parent.name}/{repo.name.removesuffix('.git')}"
            fetched = last_fetched(repo)
            result = results.get(name)
            repos.append({
                "name": name,
                "size": disk_usage(repo),
                "last_updated": fetched.isoformat() if fetched else None,
                "last_result": result["status"] if result else None,
                "error": result.get("error") if result else None,
            })
    # Repos that failed before their first successful clone have no directory yet.
    on_disk = {r["name"] for r in repos}
    for name, result in results.items():
        if name not in on_disk and not result.get("ok"):
            repos.append({"name": name, "size": 0, "last_updated": None,
                          "last_result": result["status"], "error": result.get("error")})
    try:
        disk = shutil.disk_usage(backup_dir)
        disk = {"total": disk.total, "used": disk.used, "free": disk.free}
    except OSError:
        disk = None
    return {
        "backup_dir": str(backup_dir),
        "generated": datetime.now().astimezone().isoformat(),
        "last_run": {k: v for k, v in (last_run or {}).items() if k != "repos"} or None,
        "systemd": systemd_info(),
        "total_size": sum(r["size"] for r in repos),
        "disk": disk,
        "repos": repos,
    }


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def ago(iso):
    if not iso:
        return ""
    seconds = (datetime.now().astimezone() - datetime.fromisoformat(iso)).total_seconds()
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            n = int(seconds // size)
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"


def fmt_time(iso):
    return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M") if iso else "never"


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>ghbackup</title>
<style>
  :root {{ --bg:#f6f7f9; --card:#fff; --fg:#1d2330; --muted:#677085; --line:#e3e6ec;
          --ok:#1a7f37; --bad:#cf222e; --warn:#9a6700; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0f1218; --card:#171b23; --fg:#e6e9ef; --muted:#8b93a7; --line:#2a303c;
            --ok:#3fb950; --bad:#f85149; --warn:#d29922; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
         font:15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ max-width:960px; margin:0 auto; padding:24px 16px 48px; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  .sub {{ color:var(--muted); font-size:13px; margin-bottom:20px; word-break:break-all; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:12px;
           margin-bottom:20px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }}
  .label {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
  .value {{ font-size:22px; font-weight:600; margin-top:2px; }}
  .note {{ color:var(--muted); font-size:13px; }}
  .ok {{ color:var(--ok); }} .bad {{ color:var(--bad); }} .warn {{ color:var(--warn); }}
  .banner {{ border-radius:10px; padding:10px 14px; margin-bottom:16px; border:1px solid var(--bad);
            color:var(--bad); background:var(--card); }}
  .tablewrap {{ background:var(--card); border:1px solid var(--line); border-radius:10px; overflow-x:auto; }}
  table {{ width:100%; border-collapse:collapse; }}
  th, td {{ padding:9px 14px; text-align:left; border-bottom:1px solid var(--line); white-space:nowrap; }}
  th {{ color:var(--muted); font-size:12px; font-weight:500; text-transform:uppercase; letter-spacing:.04em; }}
  tr:last-child td {{ border-bottom:0; }}
  td.num, th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  td.name {{ font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:14px; }}
  .err {{ display:block; color:var(--bad); font-size:12px; white-space:normal; max-width:420px; }}
</style>
</head>
<body>
<main>
  <h1>ghbackup</h1>
  <div class="sub">{backup_dir} &middot; refreshed {generated}</div>
  {banner}
  <div class="cards">
    <div class="card"><div class="label">Last backup</div>
      <div class="value {last_class}">{last_value}</div><div class="note">{last_note}</div></div>
    <div class="card"><div class="label">Next backup</div>
      <div class="value">{next_value}</div><div class="note">{next_note}</div></div>
    <div class="card"><div class="label">Repositories</div>
      <div class="value">{repo_count}</div><div class="note">{repo_note}</div></div>
    <div class="card"><div class="label">Space used</div>
      <div class="value">{total_size}</div><div class="note">{disk_note}</div></div>
  </div>
  <div class="tablewrap">
  <table>
    <thead><tr><th>Repository</th><th class="num">Size</th><th>Last updated</th><th>Last run</th></tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>
  </div>
</main>
</body>
</html>
"""


def render(data):
    e = html.escape
    last = data["last_run"]
    running = data["systemd"]["running"]
    failed = [r for r in data["repos"] if r["last_result"] == "error"]

    if last:
        last_value = e(ago(last.get("finished")) or fmt_time(last.get("finished")))
        last_class = "ok" if last.get("ok") else "bad"
        last_note = f"{e(fmt_time(last.get('finished')))} &middot; " + (
            "succeeded" if last.get("ok") else
            f"{len(failed)} failed" if failed else "failed")
    else:
        last_value, last_class, last_note = "never", "warn", "no run recorded yet"
    if running:
        last_note = "a backup is running now"

    next_run = data["systemd"]["next_run"]
    next_value = e(next_run) if next_run else "&mdash;"
    next_note = "systemd timer" if next_run else "timer not active"

    banner = ""
    if last and last.get("error"):
        banner = f'<div class="banner">Last run failed: {e(last["error"])}</div>'

    disk = data["disk"]
    disk_note = f"{human_size(disk['free'])} free on disk" if disk else ""

    rows = []
    for r in data["repos"]:
        result = r["last_result"]
        cls = {"error": "bad", None: "note"}.get(result, "ok")
        err = f'<span class="err">{e(r["error"])}</span>' if r["error"] else ""
        rows.append(
            f'      <tr><td class="name">{e(r["name"])}</td>'
            f'<td class="num">{human_size(r["size"]) if r["last_updated"] else "&mdash;"}</td>'
            f'<td title="{e(fmt_time(r["last_updated"]))}">{e(ago(r["last_updated"]) or "never")}</td>'
            f'<td class="{cls}">{e(result or "-")}{err}</td></tr>'
        )
    if not rows:
        rows.append('      <tr><td colspan="4" class="note">No repositories backed up yet.</td></tr>')

    return PAGE.format(
        backup_dir=e(data["backup_dir"]),
        generated=e(fmt_time(data["generated"])),
        banner=banner,
        last_value=last_value, last_class=last_class, last_note=last_note,
        next_value=next_value, next_note=next_note,
        repo_count=len(data["repos"]),
        repo_note=f'<span class="bad">{len(failed)} failing</span>' if failed else "all ok" if last else "",
        total_size=human_size(data["total_size"]),
        disk_note=disk_note,
        rows="\n".join(rows),
    )


def make_handler(backup_dir):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                body, ctype = render(collect(backup_dir)).encode(), "text/html; charset=utf-8"
            elif path == "/api/status":
                body, ctype = json.dumps(collect(backup_dir), indent=2).encode(), "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    return Handler


def main():
    config = load_config()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on (default: all)")
    parser.add_argument("--port", type=int, default=config.get("web_port", DEFAULT_PORT))
    parser.add_argument("--dir", type=Path, help="backup directory (default: from config)")
    args = parser.parse_args()
    backup_dir = (args.dir or Path(config.get("backup_dir", DEFAULT_BACKUP_DIR))).expanduser().resolve()

    server = ThreadingHTTPServer((args.host, args.port), make_handler(backup_dir))
    print(f"ghbackup web UI on http://{args.host}:{args.port}/ for {backup_dir}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
