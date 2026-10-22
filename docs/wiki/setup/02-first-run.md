# First Run

Getting the stack downloaded, configured, and started.

Before you start: Docker Desktop is running, and you have a terminal open in the folder where
you want this to live. If not, see [Prerequisites](01-prerequisites.md).

---

## Step 1 — Get the files

```bash
git clone https://github.com/mgomezdev/concordia.git
cd concordia
```

(Downloaded a ZIP instead? Just `cd` into the unzipped folder.)

This folder is small — it's configuration, not application code. The applications arrive as
Docker images in Step 3.

---

## Step 2 — Create your settings file

The repo ships `.env.example` as a template. Copy it to `.env`, which is the file Docker
actually reads:

```bash
# macOS / Linux
cp .env.example .env

# Windows PowerShell
Copy-Item .env.example .env
```

Now open `.env` in any text editor (Notepad is fine) and review it:

```ini
HOST_PORT=8001
ORCA_VERSION=2.4.2
ORDINUS_PORT=3002
THEMIS_BOOTSTRAP_KEY=dev-e2e-bootstrap-key
CORS_ORIGIN=*
```

| Setting | What it does | Change it? |
|---|---|---|
| `HOST_PORT` | Port for Themis in your browser. | Only if 8001 is taken. |
| `ORDINUS_PORT` | Port for Ordinus. | Only if 3002 is taken. |
| `ORCA_VERSION` | Which OrcaSlicer version Laminus installs. | Leave it. |
| `THEMIS_BOOTSTRAP_KEY` | **Your recovery key.** See below. | **Yes — set this.** |
| `CORS_ORIGIN` | Which machines may call the Ordinus API. `*` = any on your network. | Fine for a home workshop. |

> ⚠️ **Set `THEMIS_BOOTSTRAP_KEY` now, before the first start.**
>
> This is a break-glass key that always works, even if the normal key system locks you out —
> and locking yourself out on day one is [easy to do](03-first-login.md#the-first-key-trap).
> Change it from the default to any long random string. Treat it like a password.
>
> It only takes effect at container start, so setting it later means a restart.

---

## Step 3 — Start everything

```bash
docker compose up -d
```

`-d` means "detached" — it runs in the background and gives you your terminal back.

The first time, Docker downloads roughly 1.5 GB of images. Expect several minutes on a normal
connection. You'll see progress bars; that's normal.

Then it starts the three services **in order**, because each depends on the one before:

```
laminus  ──(must be healthy)──▶  themis  ──(must be healthy)──▶  ordinus
```

Successful output ends something like:

```
 Container concordia-laminus-1  Healthy
 Container concordia-themis-1   Started
 Container concordia-themis-1   Healthy
 Container concordia-ordinus-1  Started
```

---

## Step 4 — Wait for Laminus (the slow part)

**This is where first-time users think something is broken. It isn't.**

On a cold start Laminus:

1. downloads the OrcaSlicer AppImage (~300 MB), then
2. scans OrcaSlicer's entire bundled profile catalog.

On the machine this guide was written on, that catalog came to **1,545 machine profiles,
3,201 process profiles, and 6,475 filament profiles**. Scanning takes a while.

| Start type | Typical time to `healthy` |
|---|---|
| **Cold** (first ever, or after `down -v`) | **5–10 minutes** |
| **Warm** (AppImage already cached) | **~20–30 seconds** |

Watch the progress instead of guessing:

```bash
docker compose logs -f laminus
```

Press `Ctrl+C` to stop watching — that stops the *log view*, not the service.

> **Why Themis waits.** Laminus answers HTTP almost immediately, long before its catalog is
> usable. If Themis started then, every slice would fail for minutes for reasons a simple
> "is the port open?" check could never see. So the healthcheck tests `catalog_loaded`
> specifically, and Themis waits for it.

---

## Step 5 — Check the status

```bash
docker compose ps
```

What you want:

```
NAME                  SERVICE   STATUS
concordia-laminus-1   laminus   Up (healthy)
concordia-themis-1    themis    Up (healthy)
concordia-ordinus-1   ordinus   Up (healthy)
```

| Status | Meaning |
|---|---|
| `Up (healthy)` | Working. |
| `Up (health: starting)` | Still warming up. Normal for Laminus early on — wait. |
| `Up (unhealthy)` | Started but failing its check. See [Troubleshooting](05-troubleshooting.md). |
| `Exited (1)` | Crashed. `docker compose logs <service>`. |
| *missing* | Never started, because something it depends on isn't healthy. |

---

## Step 6 — Open it

Once Themis is healthy, open <http://localhost:8001>.

![Themis on first run: an empty job queue, zero printers, and Laminus showing online in the sidebar](../img/themis-queue-empty.jpg)

*A fresh install. Empty queue, `0 printers`, and — the bit that matters — **Laminus: online**
in the bottom-left. That's Themis confirming it can reach the slicer.*

If Laminus shows offline there, slicing will fail even though the page loads. See
[Troubleshooting](05-troubleshooting.md#laminus-shows-offline-in-themis).

Ordinus is at <http://localhost:3002>.

---

## Next

**Go to [First Login & Security](03-first-login.md) before you explore Settings.** There's one
specific action that will lock you out of your own install, and it's easy to hit by accident.
