# Quick Start

**For people who already have Docker and a terminal.** If either of those is unfamiliar, use
[Prerequisites](01-prerequisites.md) instead — it covers the same ground more slowly.

---

## Six commands

```bash
git clone https://github.com/mgomezdev/concordia.git
cd concordia
cp .env.example .env
# edit .env — at minimum set THEMIS_BOOTSTRAP_KEY (see the warning below)
docker compose up -d
docker compose ps
```

That's the whole install. The images come from Docker Hub; you do **not** need to clone
Themis, Laminus, or Ordinus.

When all three services report `healthy`:

| Service | URL |
|---|---|
| Themis (the app) | <http://localhost:8001> |
| Ordinus (Gridfinity) | <http://localhost:3002> |
| Laminus | not exposed — internal only, by design |

---

## Read this before you open Settings

> ⚠️ **Themis has no login.** With zero API keys it serves every request unauthenticated.
> The moment you create your first API key, the browser you created it from is **locked out**
> — and the key's plaintext is not shown to you afterwards.
>
> Set `THEMIS_BOOTSTRAP_KEY` in `.env` **before** `docker compose up`, and read
> [First Login & Security](03-first-login.md) before touching **Settings → API Keys**.

This is the single most likely way to break a fresh install. It is not theoretical — it is
reproducible, and it is [documented in full](03-first-login.md#the-first-key-trap).

---

## First-start timing

`laminus` has to be `healthy` before `themis` starts, and `themis` before `ordinus`. On a
**cold** first start Laminus downloads the OrcaSlicer AppImage (~300 MB) and scans the vendor
profile catalog — budget **5–10 minutes** and don't panic at `health: starting`.

Once the AppImage is cached in the `concordia_laminus-slicer` volume, restarts are fast — a
warm start on the machine this guide was written on reached `healthy` in **22 seconds**.

Watch it:

```bash
docker compose logs -f laminus
```

---

## Confirm it works

```bash
curl http://localhost:8001/api/v1/health
# {"status":"ok"}

curl http://localhost:3002/api/v1/health
# {"data":{"status":"ok","version":"1.0.0","uptime":8.9}}
```

Laminus has no host port. Check it through the container:

```bash
docker compose exec laminus python3 -c \
  "import json,urllib.request as u; print(json.load(u.urlopen('http://localhost:5000/api/health'))['catalog_loaded'])"
# True
```

`catalog_loaded: true` is the one that matters — Laminus answers HTTP long before its catalog
is usable, which is exactly why the compose healthcheck tests that field instead of the status
code.

Full checklist: [Verify It Worked](04-verify-it-worked.md).

---

## Stopping and resetting

```bash
docker compose stop          # stop, keep all data
docker compose down          # remove containers, keep all data
docker compose down -v       # ⚠️ delete ALL data — printers, jobs, files, uploads
```

`down -v` also drops the cached OrcaSlicer AppImage, so the next start is slow again.

---

## Next

- [First Login & Security](03-first-login.md) — do this before anything else
- [Add a Printer](../using/add-a-printer.md)
- [Your First Print](../using/first-print.md)
