# Orca Sidecar Integration — Design Spec

**Date:** 2026-06-23  
**Branches:** `themis:orca-sidecar`, `orca:feat/themis-integration`  
**Status:** Superseded — see `docs/slicing-flow.md` for the implemented design

> **Note:** This spec describes an earlier "prepared 3MF" design where Themis resolved OrcaSlicer profiles locally (reading JSON files at `/root/.config/OrcaSlicer`) and sent a fully baked 3MF to Orca. The implemented design has Orca own the entire profile catalog and resolve profiles by UUID; Themis never reads profile files directly. The compose `${APPDATA}/OrcaSlicer` mount described here has been removed.

---

## Goal

Run Themis and Orca as a pair of Docker containers (orchestrated from `omnibus/docker-compose.yml`) so that every slice job Themis processes goes to Orca as a sidecar instead of calling a local OrcaSlicer binary. Stretch: a new Orca endpoint that accepts N STLs + bed dimensions and returns a multi-plate 3MF, which Themis stores as a library upload.

---

## 1. Compose Architecture

**File:** `omnibus/docker-compose.yml`

```yaml
services:
  orca:
    build: ../orca
    volumes:
      - ../orca/config:/config
      - ../orca/data:/data
    shm_size: "1gb"
    environment:
      - TZ=Etc/UTC
      - PYTHONUNBUFFERED=1
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/api/health"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 60s
    restart: unless-stopped

  themis:
    build: ../themis
    ports:
      - "8000:8000"
    volumes:
      - "${APPDATA}/OrcaSlicer:/root/.config/OrcaSlicer:ro"
      - themis-data:/data
    environment:
      - ORCA_SIDECAR_URL=http://orca:5000
    depends_on:
      orca:
        condition: service_healthy
    restart: unless-stopped

volumes:
  themis-data:
```

Key decisions:
- `start_period: 60s` — Orca extracts the AppImage and builds its profile catalog on first boot; health check must not fire until that completes.
- OrcaSlicer config dir mounted **read-only** into Themis (`/root/.config/OrcaSlicer`) — PresetResolver walks JSON inheritance chains there at slice time. Orca does not need this mount; its system profiles live inside the image at `/opt/orcaslicer/resources/profiles`.
- `themis-data` named volume holds the SQLite DB + uploads between restarts. The `orca/config` and `orca/data` are bind-mounted from the repo so profiles added to the host appear immediately.

---

## 2. Profile Resolution Chain

Themis sends a **fully self-contained prepared 3MF** to Orca — Orca never needs to resolve profiles itself.

```
Spoolman extra.orca_profiles
    └─► DB: printer.loaded_filaments[slot].filament_profile   (synced on spool load)
            └─► QueueEngine._slot_for_config()
                    └─► SliceRequest.filament_presets[0..N]
                            └─► SlicerService._build_config()
                                    └─► PresetResolver.resolve(name, "machine"|"process"|"filament")
                                            (reads /root/.config/OrcaSlicer JSON, walks inherits)
                                    └─► build_project_config(machine, process, filaments, colours)
                                    └─► build_sliceable_3mf(source, config, prepared.3mf)
                                            (embeds project_settings.config)
                                    └─► prepare_hook(prepared.3mf)   [vendor remap]
                                    └─► _execute_slice(prepared.3mf) → POST /api/slice/prepared
```

The sidecar receives `prepared.3mf` with all settings baked in and returns raw gcode or `.gcode.3mf`.

---

## 3. Stories

### Story 1 — OrcaSidecarClient

**New file:** `backend/app/services/orca_sidecar_client.py`

Synchronous httpx client (no async — `SlicerService` runs in a `ThreadPoolExecutor`).

```python
class SidecarError(Exception): ...

class OrcaSidecarClient:
    def __init__(self, base_url: str, timeout: int = 620) -> None: ...

    def health(self) -> dict:
        """GET /api/health — raises SidecarError on non-200."""

    def slice_prepared(
        self,
        prepared_3mf: Path,
        plate: int,
        export_3mf: bool = False,
        geometry_only_retry: bool = False,
    ) -> str:
        """POST /api/slice/prepared → job_id."""

    def poll_status(self, job_id: str, poll_interval: float = 2.0, timeout: float = 620.0) -> dict:
        """GET /api/slice/status/{job_id} until completed or failed — raises SidecarError."""

    def download(self, job_id: str, dest: Path) -> Path:
        """GET /api/slice/download/{job_id} → writes bytes to dest, returns dest."""

    def pack_stls(
        self,
        stl_paths: list[Path],
        bed_x: float,
        bed_y: float,
    ) -> bytes:
        """POST /api/pack → 3MF bytes of multi-plate arranged result."""
```

Client is instantiated lazily (module-level singleton gated on `get_orca_sidecar_url() is not None`). Import path: `from app.services.orca_sidecar_client import OrcaSidecarClient, SidecarError`.

`get_orca_sidecar_url()` already exists in `config.py` — reads `ORCA_SIDECAR_URL` env var.

---

### Story 2 — Replace `_execute_slice()`

**File:** `backend/app/services/slicer_service.py`

Replace the method **body only** — signature is unchanged:

```python
def _execute_slice(self, input_3mf: Path, plate_number: int, export_args: list[str], out_dir: Path) -> str:
```

New body:

```python
sidecar_url = get_orca_sidecar_url()
if sidecar_url is None:
    # Fallback: local OrcaSlicer binary (dev / non-Docker path)
    # ... existing subprocess.run logic ...
    return artifact

from .orca_sidecar_client import OrcaSidecarClient, SidecarError
client = OrcaSidecarClient(sidecar_url)
export_3mf = _export_3mf_name(export_args) is not None
try:
    job_id = client.slice_prepared(input_3mf, plate_number, export_3mf=export_3mf, geometry_only_retry=False)
    status = client.poll_status(job_id)
    sliced_file = status["sliced_file"]
    dest = out_dir / sliced_file
    return str(client.download(job_id, dest))
except SidecarError as e:
    raise SliceError(str(e)) from e
```

`geometry_only_retry=False` — Themis manages its own outer two-tier retry (full 3MF → geometry-only via two separate `build_sliceable_3mf` calls in `slice()`). Delegating retry to the sidecar would double-apply it.

The local binary fallback means the codebase still works for direct dev without Docker.

---

### Story 3 — Startup Health Check

**File:** `backend/app/main.py` — add to the `lifespan` function after the existing startup block:

```python
sidecar_url = get_orca_sidecar_url()
if sidecar_url:
    try:
        from app.services.orca_sidecar_client import OrcaSidecarClient, SidecarError
        await asyncio.to_thread(OrcaSidecarClient(sidecar_url).health)
        logger.info("Orca sidecar healthy at %s", sidecar_url)
    except Exception as e:
        logger.warning("Orca sidecar at %s is not reachable: %s", sidecar_url, e)
```

Logs a warning but does not block startup — compose `depends_on: condition: service_healthy` already ensures Orca is up before Themis starts. The warning covers the edge case of `ORCA_SIDECAR_URL` being set but pointing somewhere wrong.

---

### Story 4 (Stretch) — `POST /api/pack` on Orca

**File:** `orca/app/main.py`

New endpoint accepting N STLs + bed dimensions, returns a multi-plate 3MF:

```
POST /api/pack
  files:   list of UploadFile  (.stl, 1–50 files)
  bed_x:   float (mm)
  bed_y:   float (mm)

→ 200 application/octet-stream  (packed_<timestamp>.3mf)
→ 400  if no output produced
→ 408  on timeout (120 s)
```

Implementation:

1. Write each STL to `{ARRANGE_DIR}/{job_id}/input/`.
2. Write a minimal machine profile JSON to `{ARRANGE_DIR}/{job_id}/machine.json`:
   ```json
   {
     "name": "pack-bed",
     "printable_area": ["0x0", "<bed_x>x0", "<bed_x>x<bed_y>", "0x<bed_y>"],
     "printable_height": "300"
   }
   ```
3. Run:
   ```
   xvfb-run -a --server-args="-screen 0 1024x768x24"
   orcaslicer
     --datadir {ARRANGE_DIR}/{job_id}
     --arrange 1
     --orient 1
     --export-3mf {output}/packed.3mf
     stl1.stl stl2.stl ...
   ```
4. Stream `packed.3mf` back as `application/octet-stream`.
5. Cleanup temp dir in `background_tasks`.

Timeout: 120 s (same as thumbnail endpoint). No job lifecycle — synchronous response, matching the `arrange` endpoint pattern.

---

### Story 5 (Stretch) — Themis Library Pack

#### 5a. `OrcaSidecarClient.pack_stls()`

Already defined in Story 1. Calls `POST /api/pack` with multipart files, returns raw 3MF bytes.

#### 5b. `POST /api/files/pack`

**New route** in `backend/app/api/routes/files.py`:

```
POST /api/v1/files/pack
Body (JSON):
  {
    "file_ids": [int, ...],   // must all be STL files in the library
    "bed_x": float,
    "bed_y": float
  }

→ 200  UploadedFile JSON  (the newly created library entry)
→ 400  if sidecar not configured or any file_id is not an STL
→ 422  on sidecar error
```

Server logic:

1. Load each `UploadedFile` row; reject non-STL extensions.
2. Read STL bytes from disk.
3. Call `client.pack_stls(stl_paths, bed_x, bed_y)` — returns 3MF bytes.
4. Save 3MF bytes to the uploads directory (generate a filename like `packed_<uuid>.3mf`).
5. Parse plates via `three_mf_parser` (same as `/upload`).
6. Insert `UploadedFile` row; trigger thumbnail regen in background.
7. Return the new file JSON.

This re-uses the existing file ingest pipeline — no new columns, no migration needed.

#### 5c. Library UI — Multi-select + "Pack & Save"

**File:** frontend library screen (React).

- Add a checkbox column to the library file list (hidden unless at least one STL is present).
- Show a "Pack & Save" button when ≥2 STLs are checked.
- Clicking opens a small modal: bed X (mm) / bed Y (mm) inputs, confirm.
- POSTs to `/api/v1/files/pack`, then refreshes the library.

The bed dimensions default to the last-used values (localStorage).

---

## 4. Test Updates

### `test_slicer_service.py`

Current tests patch `subprocess.run`. After Story 2, the sidecar path is taken when `ORCA_SIDECAR_URL` is set. Update approach:

- Tests that cover the local binary path: set `ORCA_SIDECAR_URL` to `None` (or unset env var) and continue patching `subprocess.run` at `slicer_service.subprocess.run`.
- New tests for the sidecar path: patch `OrcaSidecarClient` at the method boundary (`_execute_slice` calls `client.slice_prepared` etc.) or use `httpx.MockTransport`.

### New: `test_orca_sidecar_client.py`

Unit tests using `httpx.MockTransport`:

| Test | Scenario |
|------|----------|
| `test_health_ok` | 200 → returns dict |
| `test_health_raises_on_non200` | 503 → SidecarError |
| `test_slice_prepared_returns_job_id` | 200 with `{job_id}` |
| `test_poll_status_completes` | completed on 2nd poll |
| `test_poll_status_timeout` | exceeds timeout → SidecarError |
| `test_download_writes_file` | streams bytes to dest path |
| `test_pack_stls_returns_bytes` | 200 → raw bytes |

---

## 5. Acceptance Criteria

- `docker compose up` in `omnibus/` starts both containers; Orca passes its healthcheck before Themis starts.
- Each job in the Themis queue that has a test slice configured runs successfully end-to-end: filament resolution → prepared 3MF → Orca sidecar → gcode artifact → upload to printer queue.
- Orca's returned gcode/3mf artifacts contain embedded thumbnails (OrcaSlicer CLI embeds them natively in 3MF output; Themis `_inject_thumbnail` handles raw gcode).
- Themis still starts cleanly with `ORCA_SIDECAR_URL` unset (local-binary fallback active, health warning suppressed).
- `POST /api/pack` with 3 STLs returns a valid multi-plate 3MF parseable by `three_mf_parser`.
- "Pack & Save" flow in Themis UI creates a new library entry and the packed 3MF appears in the file list.
