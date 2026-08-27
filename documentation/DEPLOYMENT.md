# Running and deploying the Forge

## Current status: not hosted

**There is no live URL yet.** The Forge has only ever run inside ephemeral build
containers, which are reclaimed when the session ends. It has a Dockerfile and no
external service dependencies, so it will run anywhere — but it needs a host before
it has an address.

Everything below is what makes it live.

---

## 1. Run it locally (one minute)

```bash
git clone https://github.com/SaiAbhijyan/Ai-hub.git
cd Ai-hub
git checkout claude/open-ended-project-v3ff92
pip install -r requirements.txt

FORGE_ADMIN_TOKEN='<your token>' FORGE_ADMIN_NAME='Sai' python -m forge run
```

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
