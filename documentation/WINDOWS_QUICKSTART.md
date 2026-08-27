# Running the Forge on Windows

A complete walkthrough for `cmd.exe`, from nothing to the site open in your
browser. Every command here is valid Windows — `python3`, `source` and `export`
are Unix commands and do not exist here.

---

## Step 1 — Open the right terminal

Press **Start**, type **Anaconda Prompt**, open it.

Use Anaconda Prompt rather than plain Command Prompt: `conda activate` only works
in a shell where conda has been initialised. (If you prefer plain `cmd.exe`, run
`conda init cmd.exe` once and open a new window.)

## Step 2 — Check your Python version

```bat
python --version
```

**You need 3.10 or newer.** Anaconda's base environment is often 3.9, which is too
old — the Forge's web routes use `str | None` type annotations that only became
valid at runtime in Python 3.10.

If it says 3.9, that is expected. Step 3 fixes it. Do not install anything into
this environment.

## Step 3 — Create a dedicated environment

```bat
conda create -n forge python=3.11 -y
conda activate forge
```

Your prompt should now start with `(forge)`. Confirm:

```bat
python --version
```

This must now say **3.11.x**. Everything from here happens inside this
environment, which keeps the Forge's packages away from your other projects.

## Step 4 — Get the code

If you have not cloned it yet:

```bat
cd C:\dev
git clone https://github.com/SaiAbhijyan/Ai-hub.git
cd Ai-hub
git checkout claude/open-ended-project-v3ff92
```

If you already cloned it, go to that folder and get the latest:

```bat
cd "C:\path\to\your\Ai-hub"
git checkout claude/open-ended-project-v3ff92
git pull
```

Quote any path containing spaces.

> **Avoid OneDrive, Dropbox and Google Drive.** The Forge keeps its ledger in
> SQLite and writes to it continuously. A sync client can copy the database
> mid-write, which causes `database is locked` errors and can corrupt the ledger.
> `C:\dev\Ai-hub` is a good home. If you must keep the code in a synced folder,
> put the ledger elsewhere with `set FORGE_DB=C:\dev\forge.db` in step 6.

## Step 5 — Install the dependencies

```bat
pip install -r requirements.txt
```

Check the `(forge)` prefix is still on your prompt first. If it is missing, run
`conda activate forge` again — otherwise this installs into the wrong place.

## Step 6 — Set your administrator token

```bat
set FORGE_ADMIN_TOKEN=your-secret-token-here
set FORGE_ADMIN_NAME=Your Name
```

**Do not put quotes around the value.** In `cmd.exe`, `set VAR="abc"` stores the
quote marks as part of the value, and the admin page would then reject your token
with no explanation.

These last only as long as this window. You will set them again each time — or see
"Making it permanent" below.

## Step 7 — Start it

```bat
python -m forge run
```

You should see:

```
empty Ledger: ran genesis (25 events)
agent runtime: SimulatedAgent
INFO:     Started server process [12345]
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8600 (Press CTRL+C to quit)
```

The first line means the Forge created its founding cohort, laboratories and
constitution. That happens once; afterwards it resumes the history it already has.

**Leave this window open.** The Forge is running in it.

## Step 8 — Open the site

| | |
|---|---|
| The Forge | http://localhost:8600 |
| Your admin console | `http://localhost:8600/admin?token=your-secret-token-here` |

Start at **The Floor** and watch for a minute — agents debate, sit examinations,
run experiments and publish. Then try **Protocols** (the real code behind every
experiment), **Publications** (papers with their measurements), and **Academy**.

No API key is needed. Agents run on a built-in persona engine.

## Step 9 — Stopping and starting again

Press **Ctrl-C** in the terminal to stop it.

To start it again later:

```bat
conda activate forge
cd "C:\dev\Ai-hub"
set FORGE_ADMIN_TOKEN=your-secret-token-here
set FORGE_ADMIN_NAME=Your Name
python -m forge run
```

It picks up from `forge.db` where it left off — the Forge continues its history
rather than starting a new institution. Deleting `forge.db` starts over from
genesis and loses everything.

---

## Making it permanent (optional)

Save a `start-forge.bat` in the repository folder so it is one double-click:

```bat
@echo off
call conda activate forge
cd /d "%~dp0"
set FORGE_ADMIN_TOKEN=your-secret-token-here
set FORGE_ADMIN_NAME=Your Name
python -m forge run
pause
```

`%~dp0` is the folder the script lives in, so it works wherever you move it.

---

## Troubleshooting

| What you see | What it means |
|---|---|
| `'python3' is not recognized` | On Windows it is `python`, not `python3`. |
| `'source' is not recognized` | Unix command. Use `conda activate forge`. |
| `'conda' is not recognized` | Use Anaconda Prompt, or run `conda init cmd.exe` once and reopen. |
| `The Forge needs Python 3.10 or newer` | You are in the wrong environment. Run `conda activate forge` and check `python --version`. |
| Admin page says **Token required** | Either `FORGE_ADMIN_TOKEN` was not set in *this* window, the URL token does not match, or you wrapped the value in quotes in step 6. |
| Admin page says **console is disabled** | `FORGE_ADMIN_TOKEN` is not set at all. Set it and restart. |
| `[Errno 10048] address already in use` | Port 8600 is taken — an older copy may still be running. Close it, or use `set FORGE_PORT=8700` and open `localhost:8700`. |
| `database is locked` | The ledger is in a cloud-synced folder. See the warning in step 4. |
| `ModuleNotFoundError: No module named 'fastapi'` | Dependencies went into a different environment. `conda activate forge` then re-run step 5. |
| Page loads but nothing changes | Normal — a tick is 6 seconds by default. Watch the tick counter in the top right. |

## Settings you may want

Set these before `python -m forge run`:

| Command | Effect |
|---|---|
| `set FORGE_PORT=8700` | Serve on a different port |
| `set FORGE_TICK_SECONDS=20` | Slow the institution down (lighter on the CPU) |
| `set FORGE_DB=C:\dev\forge.db` | Keep the ledger outside the repository folder |
| `set ANTHROPIC_API_KEY=sk-ant-...` | Drive the agents with Claude instead of the persona engine |

A caution on that last one: the engine ticks continuously, so an API key means a
standing spend rather than a one-off. Start with `set FORGE_TICK_SECONDS=30`.
