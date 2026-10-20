# Configuration

Every setting, file, and volume.

---

## `.env`

Copy `.env.example` → `.env`. Docker Compose reads `.env` automatically.

| Variable | Default | Purpose |
|---|---|---|
| `HOST_PORT` | `8001` | Host port for the Themis UI and API. Container side is always 8000. |
| `ORDINUS_PORT` | `3002` | Host port for Ordinus. Container side is 80. |
| `ORCA_VERSION` | `2.4.2` | OrcaSlicer version Laminus installs. Change + restart to upgrade. |
| `THEMIS_BOOTSTRAP_KEY` | *(unset)* | Break-glass full-scope API key, always accepted. **Set this.** |
| `CORS_ORIGIN` | `*` | Which origins may call the Ordinus API. |

> ⚠️ **`ORDINUS_PORT` defaults disagree.** `.env.example` says `3002`; the fallback inside
> `docker-compose.yml` is `3001`. With a `.env` present you get 3002. Without one, 3001. If
> Ordinus isn't where you expect, that's why.

### `THEMIS_BOOTSTRAP_KEY`

A fixed key that Themis always accepts, independent of the normal key store. Your recovery
path if the key system locks you out — which is [easy to trigger](../setup/03-first-login.md).

- Set it **before first start**; it's read at container start.
- Use a long random string. Treat it as a password.
- It works on the API via `X-API-Key`. It is **rejected by the browser's key prompt** —
  verified.

### `CORS_ORIGIN`

`*` allows any machine on your network to call the Ordinus API — fine for a trusted home
workshop. To restrict:

```ini
CORS_ORIGIN=http://192.168.1.50:3001
```

---

## Compose files

Four files. The base is always included; the others are overlays you add with `-f`.

| File | Purpose |
|---|---|
| `docker-compose.yml` | **Base.** Pulls published images from Docker Hub. What `docker compose up` uses. |
| `docker-compose.local.yml` | Build from local sibling checkouts instead of pulling. |
| `docker-compose.prod.yml` | Bind-mount state to `./<app>` on the host instead of named volumes. |
| `docker-compose.test.yml` | CI stack — mock Laminus, mock Spoolman. Not for normal use. |

```bash
# Default — pull published images
docker compose up

# Build from local source (needs ../themis, ../laminus, ../ordinus)
docker compose -f docker-compose.yml -f docker-compose.local.yml up

# Host-visible, backup-friendly data directories
docker compose -f docker-compose.yml -f docker-compose.prod.yml up
```

### The prod overlay

Creates `./laminus/`, `./themis/`, `./ordinus/` under your current directory on first run.
Everything bind-mounted there is durable and worth backing up — back up the whole tree.

`laminus-slicer` stays a **named volume** deliberately: it's OrcaSlicer's extracted AppImage,
a rebuildable cache keyed by `ORCA_VERSION`. Bind-mounting it would drop hundreds of MB of
rebuildable files into your backup set for no benefit.

---

## Image overrides

Every image ref is overridable, so you can pin the stack to a specific build:

| Variable | Default |
|---|---|
| `THEMIS_IMAGE` | `ninjabuffalo/themis:latest` |
| `LAMINUS_IMAGE` | `ninjabuffalo/laminus:latest` |
| `ORDINUS_IMAGE` | `ninjabuffalo/gridfinity-customizer:latest` |

### Pinning versions

The defaults are all `:latest`, so a `docker compose pull` gets whatever shipped most
recently — which may not match this wiki's screenshots.

To pin, set the refs in `.env`:

```ini
THEMIS_IMAGE=ninjabuffalo/themis:0.1.0
LAMINUS_IMAGE=ninjabuffalo/laminus:1.0.0
ORDINUS_IMAGE=ninjabuffalo/gridfinity-customizer:3.1.0
```

> **Check the tag exists before pinning.** Not every repo currently publishes versioned image
> tags — some publish only `latest` and commit-SHA tags. A pin to a non-existent tag fails the
> pull. Coordinated versioning across the stack is known work that hasn't been done yet.

Themis reports its own version at **Settings → About**.

---

## Themis environment

Set in `docker-compose.yml`; usually no reason to change.

| Variable | Value in compose | Purpose |
|---|---|---|
| `LAMINUS_SIDECAR_URL` | `http://laminus:5000` | Where Themis sends slices. **Required** — slicing fails without it. |
| `THEMIS_LIBRARY_DIR` | `/data/library` | Model library root. |
| `THEMIS_DATA_DIR` | `/data` | SQLite DB, uploads, gcode cache. |
| `THEMIS_BOOTSTRAP_KEY` | from `.env` | See above. |

> `LAMINUS_SIDECAR_URL` must stay a **container name**. `localhost:5000` resolves to the
> Themis container itself and will never reach Laminus.

Also recognised by the Themis image but not set by this compose: `ORCA_CONFIG_DIR` and
`ORCA_EXECUTABLE` (thumbnail regeneration only — slicing goes through Laminus),
`FFMPEG_EXECUTABLE` (RTSP→MJPEG camera transcode), `THEMIS_STATIC_DIR`.

---

## Ordinus environment

| Variable | Value | Purpose |
|---|---|---|
| `THEMIS_URL` | `http://themis:8000` | Where send-to-Themis posts. |
| `PUBLIC_URL` | `http://localhost:${ORDINUS_PORT}` | Used for layout backlinks written into Themis. |
| `CORS_ORIGIN` | from `.env` | Allowed API origin. |
| `LOG_LEVEL` | `info` | |

`PUBLIC_URL` is what Themis's backlink points at. If you reach Ordinus by LAN IP rather than
localhost, set it accordingly or the backlinks won't work from other machines.

---

## Laminus environment

| Variable | Value | Purpose |
|---|---|---|
| `ORCA_VERSION` | from `.env` | Which AppImage to install. |
| `TZ` | `Etc/UTC` | |
| `PYTHONUNBUFFERED` | `1` | Unbuffered logs. |

`shm_size: 1gb` is set on the service and **required** — OrcaSlicer's renderer needs it.

---

## Volumes

| Volume | Mounted at | Contents | Back up? |
|---|---|---|---|
| `themis-data` | `/data` | SQLite DB, uploads, gcode, library | **Yes** |
| `laminus-config` | `/config` | Your OrcaSlicer user presets + plugins | **Yes** |
| `laminus-data` | `/data` | Slice inputs/outputs, job history | Maybe |
| `laminus-slicer` | `/opt/orcaslicer` | Cached AppImage | No |
| `ordinus-config` | `/config` | Ordinus settings | **Yes** |
| `ordinus-data` | `/data` | Saved layouts | **Yes** |

Laminus redirects OrcaSlicer's own `--datadir` to a disposable scratch path and symlinks just
`user/` and `plugins/` back in — so `laminus-config` contains only what's worth keeping.

```bash
docker volume ls | grep concordia     # list
docker compose down -v                # ⚠️ delete all of them
```

---

## Ports

| Service | Container | Host | Configurable |
|---|---|---|---|
| Themis | 8000 | 8001 | `HOST_PORT` |
| Ordinus | 80 | 3002 | `ORDINUS_PORT` |
| Laminus | 5000 | **none** | not published by default |

Exposing Laminus: see
[Reaching Laminus directly](../using/profiles-and-slicing.md#reaching-laminus-directly). It has
**no authentication** — don't leave it published.
