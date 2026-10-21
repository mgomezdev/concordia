# Architecture

How the four pieces fit together.

---

## The services

| Repo | Role | Port | Image |
|---|---|---|---|
| **concordia** | Orchestration. Owns `docker-compose.yml` and the cross-service E2E tests. No application code. | — | — |
| **themis** | Print queue, job lifecycle, printer management, REST API + React frontend. | `HOST_PORT` → 8000 | `ninjabuffalo/themis` |
| **laminus** | OrcaSlicer sidecar — profile catalog, slicing, 3MF packing. | internal 5000 only | `ninjabuffalo/laminus` |
| **ordinus** | Gridfinity layout tool, BOM generation, Themis hand-off. | `ORDINUS_PORT` → 80 | `ninjabuffalo/gridfinity-customizer` |

> Ordinus's image is published as **`gridfinity-customizer`**, not `ordinus` —
> `ninjabuffalo/ordinus` has never existed. Changing it in compose will break the pull.

---

## Startup order

```
laminus   (healthcheck: /api/v1/health → catalog_loaded == true)
   └─ themis    (depends_on: laminus healthy; healthcheck: /api/v1/health)
        └─ ordinus   (depends_on: themis healthy; healthcheck: /api/v1/health)
```

Each waits for the one above to be **healthy**, not merely started.

**Why the gate is strict.** Laminus binds its HTTP port long before its profile catalog is
usable. A plain `curl -f /api/v1/health` returns 200 immediately — so Themis would start while
`/api/v1/profiles` still 503'd for minutes, and every request in that window failed invisibly.
The healthcheck therefore parses the response and tests the `catalog_loaded` **field**, not
the status code:

```yaml
test: ["CMD", "python3", "-c",
  "import json,sys,urllib.request as u; d=json.load(u.urlopen('http://localhost:5000/api/v1/health',timeout=3)); sys.exit(0 if d.get('catalog_loaded') else 1)"]
start_period: 600s
```

The 600-second `start_period` covers the worst realistic cold start: ~130 s to download and
extract the AppImage, plus ~340 s to scan the full vendor catalog.

---

## How they talk

```
        browser                         browser
           │                               │
           ▼                               ▼
   ┌───────────────┐   HTTP/REST   ┌───────────────┐
   │    Themis     │◀──────────────│    Ordinus    │
   │  :8000 (8001) │  send-to-     │  :80  (3002)  │
   └───────┬───────┘  themis       └───────────────┘
           │
           │ HTTP  LAMINUS_SIDECAR_URL=http://laminus:5000
           ▼
   ┌───────────────┐
   │    Laminus    │──▶ OrcaSlicer CLI (headless, under xvfb)
   │     :5000     │
   └───────────────┘
           │
           ▼
   printers (MQTT/FTPS · SDCP/WebSocket · Moonraker)
```

Addresses like `http://laminus:5000` and `http://themis:8000` are **container names on
Docker's internal network**. They resolve only inside the stack — `localhost:5000` from your
machine will never reach Laminus, because it has no published port.

---

## Themis internals

One FastAPI process: REST API + WebSocket hub + static SPA host. Three in-process subsystems:

| Subsystem | Responsibility |
|---|---|
| `PrinterManager` | Vendor clients and printer state. |
| `QueueEngine` | The asyncio claim loop that assigns jobs to printers. |
| `SlicerService` | Resolves preset names → catalog UUIDs, delegates the slice to Laminus. |

**Persistence:** SQLite (WAL) via async SQLAlchemy — printers, jobs, orders, projects,
maintenance, API keys, webhook config, and more.

**Frontend:** React + Vite + TypeScript. No global store; per-screen hooks fetch on mount and
merge live WebSocket events.

**Vendor abstraction:** adding a printer type is one client class plus one registry entry.
Everything downstream — fleet, queue, slicing dispatch — is vendor-agnostic, and UI controls
render from capability flags rather than hard-coded vendor checks.

---

## The slicing path

1. Themis resolves machine / process / filament preset **names** to UUIDs against Laminus's
   cached catalog.
2. It makes a **job-scoped copy** of the source 3MF. (Important: the library file is shared by
   every job referencing that upload — mutating it in place corrupted other jobs, a real
   regression that was fixed.)
3. Vendor-specific filament→tool remapping runs on that copy, if the printer needs it.
4. Laminus assembles the 3MF, resolves profile inheritance, and runs OrcaSlicer.
5. Themis downloads the gcode, uploads it to the printer, starts the print.

Themis does still shell out to OrcaSlicer directly for exactly one narrow job — regenerating a
3MF's plate thumbnail after upload. Production slicing all goes through Laminus.

Sequence diagrams including error paths: `concordia/docs/slicing-flow.md`.

---

## Ordinus → Themis

`POST /api/v1/bom/:layoutId/send-to-themis`:

1. Resolve `THEMIS_URL` (DB setting, falling back to env); 503 if neither is set.
2. Require the BOM generation to be `ready`; 409 otherwise.
3. **Resume-or-create** — if a `themisProjectId` is recorded, fetch that project and compute
   what's already there. A timeout is re-thrown rather than assumed-gone; a 404 falls back to
   creating fresh.
4. Upload each unique STL (Themis dedups by content hash within the folder).
5. Create the project if needed, persisting the ID **before** adding items, so a mid-call
   failure resumes cleanly instead of orphaning a project.
6. Add missing items, add the layout backlink.

All Ordinus files go to one flat `/Gridfinity` folder — deliberate, so the same bin model
reused across different layouts dedups against itself too.

**No auth between Ordinus and Themis** — same local trust boundary. **No filament
pre-assignment** — Ordinus doesn't know your inventory.

---

## Data

| Volume | Holds | Back up? |
|---|---|---|
| `themis-data` | SQLite DB, uploads, gcode cache, library | **Yes** |
| `laminus-config` | Your OrcaSlicer user presets and plugins | **Yes** |
| `laminus-data` | Slice inputs/outputs, job history | Maybe |
| `laminus-slicer` | Cached OrcaSlicer AppImage | **No** — rebuildable, hundreds of MB |
| `ordinus-config`, `ordinus-data` | Layouts and settings | **Yes** |

For host-visible, backup-friendly paths instead of named volumes, use the prod overlay — see
[Configuration](configuration.md#compose-files).
