# ghbackup

Scheduled mirror backups of every GitHub repo you own or can access through an organization, kept on a local machine.
Each repo is stored as a bare mirror at `<backup-dir>/<owner>/<repo>.git`.
Runs as a systemd user timer.

## Requirements

- Python 3.11+ (Ubuntu 23.04 or later)
- `git`
- [GitHub CLI](https://cli.github.com/) (`gh`), logged in

## Installation

```bash
sudo apt install git gh
gh auth login                       # store the token in a file so systemd can read it, not a desktop keyring
gh auth setup-git                   # lets git use gh's login, needed to clone private repos
sudo loginctl enable-linger $USER   # lets your timers run while you're logged out
git clone <this-repo> && cd ghbackup
```

## Setup

```bash
./activate.sh                                               # prompts for the settings
./activate.sh --dir ~/github-backups --schedule "0 3 * * *" # no prompts
```

- You're asked for the backup directory and a schedule in cron syntax (default `0 3 * * *`, 3am nightly).
  Press Enter to accept the default shown.
- Settings are saved to `~/.config/ghbackup/config.toml`, outside the repo:
  ```toml
  backup_dir = "/home/you/github-backups"
  schedule = "0 3 * * *"
  ```
- To change settings, run `./activate.sh` again. The prompts default to the current config.
- A run that was missed (machine off or asleep) happens at the next boot.
  A failed run retries every 10 minutes, up to 3 times.

## Managing

```bash
./deactivate.sh                              # disable the timer (config is kept)
systemctl --user list-timers ghbackup.timer  # next/last run
systemctl --user status ghbackup             # result of the last run
systemctl --user start ghbackup              # run a backup now
```

Or run it directly: `./ghbackup.py` (uses the config) or `./ghbackup.py --output DIR`.

## Logs

```bash
journalctl --user -u ghbackup
```

The backup exits with a non-zero code if any repo fails, so the failure shows up in `systemctl --user status ghbackup`.

## Restoring

```bash
git clone ~/github-backups/<owner>/<repo>.git
```
