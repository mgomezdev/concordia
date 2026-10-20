# Concordia

Concordia is the orchestration layer for the print farm stack — it owns `docker-compose.yml`
and is the one place a new user starts from. It contains no application code of its own; the
apps it wires together live in sibling repos:

| Service | Repo | Role |
|---|---|---|
| `laminus` | [`../laminus`](../laminus) | OrcaSlicer sidecar — profile catalog, slicing, 3MF packing |
| `themis` | [`../themis`](../themis) | Print queue, job lifecycle, printer management, web UI |
| `ordinus` | [`../ordinus`](../ordinus) | Gridfinity bin layout tool, BOM generation, Themis integration |

> 📖 **Just want to run it?** This file gets the stack up. For a screen-by-screen walkthrough
> (first printer, first print, troubleshooting) see the [user wiki](docs/wiki/Home.md).
> For E2E tests and repo internals, see [`CONTRIBUTING.md`](CONTRIBUTING.md).

---

## Prerequisites

- **Docker**, with Compose support. This repo's own files and comments assume the `docker compose`
  (space) subcommand. If your Docker install only has the legacy standalone binary, every command
  below also works as `docker-compose` (hyphen) instead — check with `docker compose version` vs
  `docker-compose version` before you start.
- No local Python/Node toolchain needed for this quick start — everything runs from published
  images. You only need sibling checkouts of `themis`/`laminus`/`ordinus` for the local-build path
  below.

## Quick start (pull and run)

```bash
cp .env.example .env
# edit .env if you need different ports — defaults below
docker compose up
```

- Themis: <http://localhost:8001>
- Ordinus: <http://localhost:3002>

Laminus has no exposed port by default — Themis and Ordinus reach it over the internal Docker
network only.

First boot downloads the OrcaSlicer AppImage into Laminus (~300 MB) and scans the full vendor
profile bundle; this can take several minutes before Laminus reports healthy and Themis starts.
Watch it with:

```bash
docker compose logs -f laminus
```

## Local source builds (development)

`docker-compose.local.yml` overrides the image pulls with local builds from sibling repos, which
must exist at the same directory level as this one:

```
parent-dir/
├── concordia/   ← this repo
├── themis/
├── laminus/
└── ordinus/
```

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up --build
```

Rebuild a single service after code changes:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up --build themis
```

## Configuration

Copy `.env.example` to `.env` and adjust as needed:

| Variable | Default | Purpose |
|---|---|---|
| `HOST_PORT` | `8001` | Host port for Themis |
| `ORDINUS_PORT` | `3002` | Host port for Ordinus |
| `ORCA_VERSION` | `2.4.2` | OrcaSlicer AppImage version Laminus downloads |
| `THEMIS_BOOTSTRAP_KEY` | *(none)* | Fixed full-scope API key — recovery path if you lock yourself out of Themis's auth system |
| `CORS_ORIGIN` | `*` | CORS origin Ordinus allows |

## Stopping / resetting

```bash
docker compose down        # stop, keep data in named volumes
docker compose down -v     # stop and wipe all data — fresh start
```

For a production deployment where you want the data visible and backed up on the host filesystem
instead of in Docker-managed named volumes, add the prod overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up
```

This creates `./laminus/`, `./themis/`, `./ordinus/` under the current directory on first run —
back up that tree.

## Troubleshooting

- **`docker: unknown command: docker compose`** — your Docker install doesn't have the Compose v2
  plugin registered. Use `docker-compose` (hyphen) in place of every `docker compose` command above.
- **Laminus never reports healthy / Themis never starts** — check `docker compose logs laminus`.
  A cold start needs internet access to fetch the OrcaSlicer AppImage; a fully cold catalog scan
  over the real vendor profile bundle can take up to ~8 minutes before `catalog_loaded` turns true.
- More troubleshooting, including the first-login lockout trap, lives in the
  [wiki's troubleshooting page](docs/wiki/setup/05-troubleshooting.md).

## More docs

- [User wiki](docs/wiki/Home.md) — full install walkthrough and screen-by-screen guide
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — contributor reference: repo layout, E2E test suite, adding a new service
- [`docs/slicing-flow.md`](docs/slicing-flow.md) — sequence diagrams for the slicing pipeline
