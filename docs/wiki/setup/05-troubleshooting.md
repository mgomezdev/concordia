# Troubleshooting

Real failures, with the reasoning behind each fix.

**First move, always:** find out what the service itself says.

```bash
docker compose ps                    # who's up, who's healthy
docker compose logs laminus          # or themis / ordinus
docker compose logs -f themis        # follow live; Ctrl+C stops watching, not the service
```

---

## Setup problems

### `docker: command not found`

Docker Desktop isn't installed, or your terminal was open before you installed it. Close the
terminal, open a new one, try again. Still failing → reinstall from
[Prerequisites](01-prerequisites.md).

### "Cannot connect to the Docker daemon"

Docker Desktop is installed but not **running**. Launch it, wait for the whale icon to go
steady, retry.

### `port is already allocated`

Something else owns the port.

```
Error ... Bind for 0.0.0.0:8001 failed: port is already allocated
```

Change it in `.env` rather than hunting the other program:

```ini
HOST_PORT=8090
```

Then `docker compose up -d`. Themis is now at `http://localhost:8090`. Same idea for
`ORDINUS_PORT`.

### The download keeps failing or stalling

The first start pulls ~1.5 GB plus a ~300 MB OrcaSlicer AppImage. On a flaky connection it can
time out. Just run `docker compose up -d` again — Docker resumes from what it already has.

---

## Startup problems

### Laminus sits at `health: starting` forever

**First, check it's actually stuck rather than slow.** A cold start legitimately takes
**5–10 minutes**: it downloads the AppImage, then scans 11,000+ vendor profiles.

```bash
docker compose logs -f laminus
```

Moving output means it's working — wait.

If it's genuinely stuck past ~15 minutes:

```bash
docker compose restart laminus
```

The AppImage is cached in the `concordia_laminus-slicer` volume, so a retry skips the download
and is much faster.

### Themis never starts

Themis is gated on Laminus being healthy — by design, so it never starts against a slicer that
can't slice. `docker compose ps`; if Laminus isn't healthy, that's your real problem. Themis
starts on its own once Laminus is.

Same relationship between Ordinus and Themis.

### Everything starts, then Laminus goes unhealthy after a restart

Check whether `ORCA_VERSION` in `.env` changed. The entrypoint compares it against the cached
install and re-downloads on a mismatch, so it's briefly unhealthy while fetching a new
AppImage. Expected — wait it out.

---

## Running problems

### Laminus shows "offline" in Themis

The Themis page loads, but the sidebar shows Laminus offline — and every slice will fail.

1. Is Laminus actually healthy? `docker compose ps`
2. Is its catalog built? See [Verify, step 4](04-verify-it-worked.md#4-laminus-has-built-its-catalog).
3. Did you edit `LAMINUS_SIDECAR_URL`? It must stay `http://laminus:5000` — that's the
   *container* name on Docker's internal network, not something on your machine.
   `localhost:5000` will never work from inside the Themis container.

### The MAKE dropdown is empty when adding a printer

Themis reached Laminus but received no profiles. Almost always the catalog isn't built yet —
check `catalog_loaded`. If it's `true` and the dropdown is still empty, restart Themis so it
re-fetches:

```bash
docker compose restart themis
```

### A job says BLOCKED

**Blocked is not failed.** It means "can't run right now", it stays in the queue, and it's
re-evaluated every cycle. Load the right filament and it unblocks itself.

Usual causes:

| Cause | Fix |
|---|---|
| Loaded filament doesn't match the job (type **and** colour) | Load matching filament, or edit the job's filament. |
| No printer has a machine profile set | Fleet → edit printer → set make/model/nozzle. |
| Slicing failed for this machine | Open the job for the slice error. |

Only a post-slice upload/start error marks a job **failed**, which is terminal.

### A printer finished but won't take the next job

Working as intended. After a print completes, the printer is held until you confirm the plate
is clear — otherwise it would print onto the finished part.

Fleet → the printer's card → **Ready for new work**.

### Settings shows the wrong panel

Navigate Settings by **clicking the sidebar items**, not by typing URLs. The routes aren't all
what you'd guess — the webhook page is `/settings/webhook`, singular — and an unrecognised
`/settings/...` path silently renders the Tags panel instead of a not-found. If you see Tags
when you expected something else, that's why.

### I'm locked out of the UI by an API key prompt

See [the first-key trap](03-first-login.md#the-first-key-trap) — it has the full explanation
and every recovery option.

Short version: your `THEMIS_BOOTSTRAP_KEY` still works on the API via the `X-API-Key` header,
even though the browser dialog rejects it.

### The prompt says "Couldn't reach the Themis server"

Check whether it actually can't:

```bash
curl http://localhost:8001/api/v1/health
```

If that returns `{"status":"ok"}`, the message is wrong — this is the API-key lockout state
mislabelling itself. See above.

---

## Resets

Escalating. Try them in order.

```bash
# Restart one service
docker compose restart themis

# Restart everything, keep all data
docker compose down
docker compose up -d

# Re-pull images (after an upstream update)
docker compose pull
docker compose up -d

# ⚠️ Nuclear: delete ALL data — printers, jobs, files, history
docker compose down -v
docker compose up -d
```

`down -v` also removes the cached OrcaSlicer AppImage, so budget another 5–10 minutes.

---

## Gathering information for a bug report

```bash
docker compose ps
docker compose logs --tail=100 laminus
docker compose logs --tail=100 themis
docker compose exec laminus python3 -c \
  "import json,urllib.request as u; print(json.load(u.urlopen('http://localhost:5000/api/health')))"
```

Include your `.env` **with `THEMIS_BOOTSTRAP_KEY` redacted**.
