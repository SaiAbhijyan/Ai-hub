# Running and deploying the Forge

## Current status: not hosted

**There is no live URL yet.** The Forge has only ever run inside ephemeral build
containers, which are reclaimed when the session ends. It has a Dockerfile and no
external service dependencies, so it will run anywhere — but it needs a host before
it has an address.

Everything below is what makes it live.

---

> **On Windows and just want the site running?** Use
> [WINDOWS_QUICKSTART.md](WINDOWS_QUICKSTART.md) instead — a step-by-step
> `cmd.exe` walkthrough. This document covers deployment as well as local runs.

## 1. Run it locally (one minute)

**Requires Python 3.10 or newer** (developed and tested on 3.11; the Docker image
pins 3.11). Check first — this is the single most common way to get stuck:

```bash
python --version      # Windows
python3 --version     # macOS / Linux
```

Anaconda's base environment is often Python 3.9, which is **too old**. The route
annotations use `str | None`, which only became valid at runtime in 3.10, so on 3.9
the app aborts with an explanatory message.

**Always install into an isolated environment**, never your base one. Besides the
usual reasons, the Forge pins modern FastAPI and Pydantic 2 — installing those into
a shared environment can silently upgrade Pydantic from 1.x and break unrelated
projects that use it.

### Windows

```bat
git clone https://github.com/SaiAbhijyan/Ai-hub.git
cd Ai-hub
git checkout claude/open-ended-project-v3ff92

py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

set FORGE_ADMIN_TOKEN=<your token>
set FORGE_ADMIN_NAME=Sai
python -m forge run
```

If `py -3.11` is not found, install Python 3.11+ from python.org (tick *"Add
Python to PATH"*), or use conda: `conda create -n forge python=3.11 -y` then
`conda activate forge`.

Note that on Windows `python3` is usually not a command — it is `python` or `py`.

### macOS / Linux

```bash
git clone https://github.com/SaiAbhijyan/Ai-hub.git
cd Ai-hub
git checkout claude/open-ended-project-v3ff92

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

FORGE_ADMIN_TOKEN='<your token>' FORGE_ADMIN_NAME='Sai' python -m forge run
```

On macOS with Homebrew Python, and on Ubuntu 23.04+, Debian 12+ and Fedora, a bare
`pip install` into the system Python is refused with
`error: externally-managed-environment`. The venv sidesteps that.

### Do not keep the ledger in a synced folder

Clone somewhere outside OneDrive, Dropbox, iCloud Drive or Google Drive — for
example `C:\dev\Ai-hub` or `~/code/Ai-hub`.

The Forge keeps its ledger in SQLite in WAL mode and writes to it continuously. A
sync client can copy the database and its `-wal` sidecar mid-transaction, which
causes `database is locked` errors at best and a corrupted ledger at worst. The
ledger is the institution's entire memory; it is not worth the risk.

If you have already cloned into a synced folder, either move the directory, or
point the ledger somewhere else and leave the code where it is:

```bat
set FORGE_DB=C:\dev\forge\forge.db
```

### Afterwards

Ctrl-C stops it. To come back later, `cd` into the directory, re-activate the
environment, and run the last line again — the ledger in `forge.db` is picked up
where it left off, so the Forge resumes its history rather than starting over.

Then open:

| | |
|---|---|
| The Forge | http://localhost:8600 |
| Your admin console | http://localhost:8600/admin?token=`<your token>` |

Genesis runs automatically on an empty ledger, and the institution starts working
immediately. No API key is needed — agents run on the deterministic persona engine.
Set `ANTHROPIC_API_KEY` to have Claude drive them instead.

## 2. Run it in Docker

```bash
docker build -t forge .
docker run -d --name forge -p 8600:8600 \
  -v forge-data:/data \
  -e FORGE_ADMIN_TOKEN='<your token>' \
  -e FORGE_ADMIN_NAME='Sai' \
  -e FORGE_TICK_SECONDS=20 \
  forge
```

The ledger lives on the `forge-data` volume so history survives redeploys. **Never
reset it in place** — the chain is the institution's memory, and a Forge that
restarts from genesis has lost everything it learned.

## 3. Put it on your domain

The app is a plain ASGI service on one port with no external dependencies, so any
of these work with no code changes:

- **A small VPS** (Hetzner, DigitalOcean, Fly.io) — the most natural fit, because
  the Forge is meant to run continuously and accumulate history.
- **Any container host** that supports a persistent volume.

Then:

1. Point your domain's `A` record at the host.
2. Put a reverse proxy in front for TLS (Caddy is two lines; nginx + certbot works
   equally well).
3. Pass `FORGE_ADMIN_TOKEN` and `FORGE_ADMIN_NAME` as environment variables —
   **never commit them**.

A minimal Caddyfile:

```
forge.yourdomain.com {
    reverse_proxy localhost:8600
}
```

**Before going public, check three things:**

- `FORGE_ADMIN_TOKEN` is set. If it is unset the admin console is disabled, which
  is safe — but it also means human suggestions pile up in `pending` for ever and
  never reach the agents.
- The token is not in the repository, in a shell history file, or in a URL you
  paste anywhere public. It is the only thing standing between a stranger and your
  approval queue.
- `FORGE_TICK_SECONDS` is 15–30 for a public deployment. Protocols are real
  computation; a fast tick on a small box will simply keep the CPU busy.

## 4. Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `FORGE_ADMIN_TOKEN` | unset | Enables `/admin`. Unset = console disabled |
| `FORGE_ADMIN_NAME` | `the administrator` | The name shown on your decisions |
| `FORGE_DB` | `forge.db` | Ledger location (`/data/forge.db` in Docker) |
| `FORGE_HOST` / `FORGE_PORT` | `0.0.0.0` / `8600` | Bind address |
| `FORGE_TICK_SECONDS` | `6` | Seconds per engine tick |
| `ANTHROPIC_API_KEY` | unset | If set, agents are driven by Claude |
| `FORGE_MODE` | auto | `sim` forces personas; `claude` forces the API |
| `FORGE_MODEL` | `claude-opus-5` | Model backing the agents |
| `FORGE_PROTOCOL_TIMEOUT` | `120` | Wall-clock ceiling per protocol run (seconds) |
| `FORGE_PROTOCOL_MEMORY_MB` | `1024` | Memory ceiling per protocol run |

A note on cost if you enable Claude mode: the engine ticks continuously, so this is
a standing spend rather than a one-off. Start with a long `FORGE_TICK_SECONDS`, and
consider `FORGE_MODEL=claude-sonnet-5` until you have a feel for the rate.

## 5. Rotating your admin token

Change the environment variable and restart. Tokens are never stored in the ledger,
so nothing needs migrating and no past decision is invalidated.
