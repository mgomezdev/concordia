# Profile Drift Remap & Laminus Health — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Features 2 and 3 from the filament-drift-health spec: (2) intercept catalog sync when removed profiles are still referenced by live DB/Spoolman data, prompt the user to remap them, then commit the new catalog; (3) add a Laminus health status chip to the sidebar.

**Architecture:** A new `catalog_utils.py` service computes drift by diffing old vs incoming catalog name sets and querying live DB/Spoolman hits, deduplicating grouped entries by stale value. The laminus.py catalog routes are refactored to split fetch from commit, park a pending-sync slot on drift, and expose a `confirm-remap` endpoint that applies resolutions and finalizes the swap. A parallel Spoolman sanity check fires on test-connection. The frontend adds a `RemapModal` component wired into SettingsScreen's refresh/rescan/test-connection handlers, and a `LaminusStatusChip` polling component in the sidebar.

**Tech Stack:** Python/FastAPI, SQLAlchemy async ORM, httpx, pytest-asyncio, React/TypeScript, Mantine (existing UI components)

---

## Pre-requisite: Understand the existing codebase

Before implementing, read these files once:
- `backend/app/services/spoolman_service.py` — `patch_filament`, `fetch_filaments`, `test_connection`
- `backend/app/api/routes/laminus.py` — current `_fetch_and_cache`, `get_cached_catalog`, all route handlers
- `backend/app/api/routes/settings.py` lines 122-142 — `test_spoolman_connection` handler
- `backend/app/models.py` — `Printer`, `Job`, `JobPrinterConfig`, `SpoolmanConfig`
- `backend/tests/conftest.py` — client fixture, session override
- `frontend/src/api/orca.ts` — broken route URLs (calls `/api/v1/orca/` but backend is `/api/v1/laminus/`)
- `frontend/src/api/spoolman.ts` — `testSpoolmanConnection` (NOTE: spec says `api/settings.ts` but it actually lives here)

**Known pre-existing bug:** `orca.ts` calls `/api/v1/orca/catalog/*` routes that don't exist — the backend is at `/api/v1/laminus/catalog/*` (renamed in commit `acabc8f`). Fix these URLs in Task 6.

---

## File Map

| Action | File |
|--------|------|
| Create | `backend/app/services/catalog_utils.py` |
| Create | `backend/tests/services/test_catalog_utils.py` |
| Modify | `backend/app/api/routes/laminus.py` |
| Modify | `backend/app/api/routes/settings.py` |
| Create | `backend/tests/api/test_laminus_api.py` |
| Modify | `backend/tests/api/test_settings_routes.py` |
| Create | `frontend/src/api/laminus.ts` |
| Modify | `frontend/src/api/orca.ts` |
| Modify | `frontend/src/api/spoolman.ts` |
| Create | `frontend/src/components/RemapModal.tsx` |
| Modify | `frontend/src/screens/SettingsScreen.tsx` |
| Create | `frontend/src/components/LaminusStatusChip.tsx` |
| Modify | `frontend/src/components/Sidebar.tsx` |

---

## Task 1: `catalog_utils.py` — name-set helper and drift computation

**Files:**
- Create: `backend/app/services/catalog_utils.py`
- Create: `backend/tests/services/test_catalog_utils.py`
- Modify: `backend/app/api/routes/settings.py` (lines 281-285 — refactor inline set-builds)

### What this does

`catalog_name_sets` extracts four sets from a raw catalog dict (machine names, process names, filament names, filament UUIDs). `compute_drift` diffs old vs new catalog, queries live DB for affected printers/jobs, optionally fetches Spoolman filaments, and returns a deduplicated pending-remaps payload or `None` if nothing is affected.

- [ ] **Step 1: Write failing tests for `catalog_name_sets`**

```python
# backend/tests/services/test_catalog_utils.py
import pytest
from app.services.catalog_utils import catalog_name_sets

SAMPLE_CATALOG = {
    "machine": [{"name": "Bambu X1C 0.4", "uuid": "m1"}, {"name": "Ender 3", "uuid": "m2"}],
    "process": [{"name": "0.20mm Standard", "uuid": "p1"}],
    "filament": [
        {"name": "Generic PLA", "uuid": "f1"},
        {"name": "PolyLite PLA", "uuid": "f2"},
    ],
}

def test_catalog_name_sets_normal():
    machines, processes, filaments, uuids = catalog_name_sets(SAMPLE_CATALOG)
    assert machines == {"Bambu X1C 0.4", "Ender 3"}
    assert processes == {"0.20mm Standard"}
    assert filaments == {"Generic PLA", "PolyLite PLA"}
    assert uuids == {"f1", "f2"}

def test_catalog_name_sets_empty():
    machines, processes, filaments, uuids = catalog_name_sets({})
    assert machines == set()
    assert processes == set()
    assert filaments == set()
    assert uuids == set()

def test_catalog_name_sets_missing_name_skipped():
    catalog = {"machine": [{"uuid": "m1"}, {"name": "Good Machine", "uuid": "m2"}]}
    machines, *_ = catalog_name_sets(catalog)
    assert machines == {"Good Machine"}
```

- [ ] **Step 2: Run test to see it fail**

```
cd backend && python -m pytest tests/services/test_catalog_utils.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.services.catalog_utils'`

- [ ] **Step 3: Implement `catalog_name_sets`**

```python
# backend/app/services/catalog_utils.py
from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("app.catalog_utils")


def catalog_name_sets(catalog: dict) -> tuple[set[str], set[str], set[str], set[str]]:
    """Return (machine_names, process_names, filament_names, filament_uuids)."""
    machine_names = {m["name"] for m in catalog.get("machine", []) if m.get("name")}
    process_names = {p["name"] for p in catalog.get("process", []) if p.get("name")}
    filament_names = {f["name"] for f in catalog.get("filament", []) if f.get("name")}
    filament_uuids = {f["uuid"] for f in catalog.get("filament", []) if f.get("uuid")}
    return machine_names, process_names, filament_names, filament_uuids
```

- [ ] **Step 4: Run `catalog_name_sets` tests — they should pass**

```
cd backend && python -m pytest tests/services/test_catalog_utils.py::test_catalog_name_sets_normal tests/services/test_catalog_utils.py::test_catalog_name_sets_empty tests/services/test_catalog_utils.py::test_catalog_name_sets_missing_name_skipped -v
```

- [ ] **Step 5: Write failing tests for `compute_drift` — None paths**

```python
# append to backend/tests/services/test_catalog_utils.py
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from app.models import Base, Printer, Job, JobPrinterConfig

# In-memory SQLite session for drift tests
@pytest_asyncio.fixture
async def drift_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()

@pytest.mark.asyncio
async def test_compute_drift_no_removals_returns_none(drift_session):
    """Identical catalogs produce no drift."""
    from app.services.catalog_utils import compute_drift
    cat = {
        "machine": [{"name": "Bambu X1C 0.4", "uuid": "m1"}],
        "process": [{"name": "0.20mm Standard", "uuid": "p1"}],
        "filament": [{"name": "Generic PLA", "uuid": "f1"}],
    }
    result = await compute_drift(cat, cat, drift_session, None)
    assert result is None

@pytest.mark.asyncio
async def test_compute_drift_removals_unreferenced_returns_none(drift_session):
    """Removed profile with no DB references → None."""
    from app.services.catalog_utils import compute_drift
    old_cat = {
        "machine": [{"name": "Old Printer", "uuid": "m1"}, {"name": "Keep Me", "uuid": "m2"}],
        "process": [], "filament": [],
    }
    new_cat = {"machine": [{"name": "Keep Me", "uuid": "m2"}], "process": [], "filament": []}
    result = await compute_drift(old_cat, new_cat, drift_session, None)
    assert result is None
```

- [ ] **Step 6: Write failing test for `compute_drift` — printer machine profile drift**

```python
# append to test_catalog_utils.py
from app.models import Printer

@pytest.mark.asyncio
async def test_compute_drift_stale_printer_profile(drift_session):
    """Printer referencing removed machine profile → one grouped printer entry."""
    from app.services.catalog_utils import compute_drift

    p1 = Printer(
        name="X1C-left",
        printer_type="bambu_x1c",
        connection_config={},
        current_orca_printer_profile="Bambu X1C 0.4 nozzle",
        orca_printer_profiles=["Bambu X1C 0.4 nozzle"],
        loaded_filaments=[],
        enabled=True,
        queue_on=True,
    )
    p2 = Printer(
        name="X1C-right",
        printer_type="bambu_x1c",
        connection_config={},
        current_orca_printer_profile="Bambu X1C 0.4 nozzle",
        orca_printer_profiles=["Bambu X1C 0.4 nozzle"],
        loaded_filaments=[],
        enabled=True,
        queue_on=True,
    )
    drift_session.add_all([p1, p2])
    await drift_session.flush()

    old_cat = {
        "machine": [{"name": "Bambu X1C 0.4 nozzle", "uuid": "m1"}],
        "process": [], "filament": [],
    }
    new_cat = {"machine": [], "process": [], "filament": []}

    result = await compute_drift(old_cat, new_cat, drift_session, None)
    assert result is not None
    printers = result["pending"]["printers"]
    assert len(printers) == 1  # one grouped entry for the shared stale value
    entry = printers[0]
    assert entry["field"] == "current_orca_printer_profile"
    assert entry["stale_value"] == "Bambu X1C 0.4 nozzle"
    assert set(entry["affected_printer_ids"]) == {p1.id, p2.id}
    assert set(entry["affected_printer_names"]) == {"X1C-left", "X1C-right"}
    assert entry["required"] is True
    assert entry["options_kind"] == "machine"
    # Options must carry incoming catalog's valid values (empty here)
    assert result["options"]["machine"] == []
```

- [ ] **Step 7: Write failing tests for job drift and Spoolman section**

```python
# append to test_catalog_utils.py
from app.models import Job, JobPrinterConfig

@pytest.mark.asyncio
async def test_compute_drift_two_queued_jobs_same_stale_profile(drift_session):
    """Two queued jobs with identical stale print_profile → single grouped entry."""
    from app.services.catalog_utils import compute_drift

    printer = Printer(
        name="P1", printer_type="bambu_x1c", connection_config={},
        current_orca_printer_profile="Good Printer", orca_printer_profiles=[],
        loaded_filaments=[], enabled=True, queue_on=True,
    )
    drift_session.add(printer)
    await drift_session.flush()

    for i in range(2):
        job = Job(
            status="queued",
            machine_preset="Good Printer",
            process_preset="0.20mm Standard Old",
            filament_preset="PLA",
        )
        drift_session.add(job)
        await drift_session.flush()
        cfg = JobPrinterConfig(
            job_id=job.id,
            printer_id=printer.id,
            print_profile="0.20mm Standard Old",
            filament_profile=None,
        )
        drift_session.add(cfg)
    await drift_session.flush()

    old_cat = {
        "machine": [{"name": "Good Printer", "uuid": "m1"}],
        "process": [{"name": "0.20mm Standard Old", "uuid": "p1"}],
        "filament": [],
    }
    new_cat = {
        "machine": [{"name": "Good Printer", "uuid": "m1"}],
        "process": [],
        "filament": [],
    }

    result = await compute_drift(old_cat, new_cat, drift_session, None)
    assert result is not None
    jobs = result["pending"]["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["stale_value"] == "0.20mm Standard Old"
    assert len(jobs[0]["affected_config_ids"]) == 2

@pytest.mark.asyncio
async def test_compute_drift_spoolman_stale_uuid(drift_session):
    """Spoolman filaments with removed UUID → one grouped entry per unique stale UUID."""
    from app.services.catalog_utils import compute_drift
    from app.models import SpoolmanConfig
    import json

    cfg = SpoolmanConfig(id=1, enabled=True, url="http://spoolman.test")
    drift_session.add(cfg)
    await drift_session.flush()

    filaments_response = [
        {
            "id": 9, "name": "PolyLite PLA Red",
            "extra": {"orca_profiles": json.dumps(json.dumps({"stale-uuid-A": "PolyLite PLA @X1C"}))},
        },
        {
            "id": 14, "name": "PolyLite PLA Blue",
            "extra": {"orca_profiles": json.dumps(json.dumps({"stale-uuid-A": "PolyLite PLA @X1C"}))},
        },
    ]

    old_cat = {"machine": [], "process": [], "filament": [{"name": "PolyLite PLA @X1C", "uuid": "stale-uuid-A"}]}
    new_cat = {"machine": [], "process": [], "filament": []}

    with patch("app.services.catalog_utils.fetch_filaments", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = filaments_response
        result = await compute_drift(old_cat, new_cat, drift_session, cfg)

    assert result is not None
    spool_entries = result["pending"]["spoolman_filaments"]
    assert len(spool_entries) == 1
    entry = spool_entries[0]
    assert entry["stale_uuid"] == "stale-uuid-A"
    assert set(entry["affected_filament_ids"]) == {9, 14}
    assert entry["required"] is False

@pytest.mark.asyncio
async def test_compute_drift_spoolman_fetch_failure_sets_error(drift_session):
    """Spoolman HTTP failure → spoolman_error set, other sections intact."""
    from app.services.catalog_utils import compute_drift
    from app.models import SpoolmanConfig, Printer

    p = Printer(
        name="P1", printer_type="bambu_x1c", connection_config={},
        current_orca_printer_profile="Stale Printer", orca_printer_profiles=[],
        loaded_filaments=[], enabled=True, queue_on=True,
    )
    drift_session.add(p)
    cfg = SpoolmanConfig(id=1, enabled=True, url="http://spoolman.test")
    drift_session.add(cfg)
    await drift_session.flush()

    old_cat = {"machine": [{"name": "Stale Printer", "uuid": "m1"}], "process": [], "filament": [{"name": "PLA", "uuid": "f1"}]}
    new_cat = {"machine": [], "process": [], "filament": [{"name": "PLA", "uuid": "f1"}]}

    with patch("app.services.catalog_utils.fetch_filaments", side_effect=Exception("timeout")):
        result = await compute_drift(old_cat, new_cat, drift_session, cfg)

    assert result is not None
    assert result["spoolman_error"] is not None
    assert len(result["pending"]["printers"]) == 1  # printer drift still captured

@pytest.mark.asyncio
async def test_compute_drift_spoolman_disabled_skips_section(drift_session):
    """SpoolmanConfig with enabled=False → spoolman_filaments empty, no fetch call."""
    from app.services.catalog_utils import compute_drift
    from app.models import SpoolmanConfig

    cfg = SpoolmanConfig(id=1, enabled=False, url="http://spoolman.test")
    drift_session.add(cfg)
    await drift_session.flush()

    old_cat = {"machine": [], "process": [], "filament": [{"name": "PLA", "uuid": "f1"}]}
    new_cat = {"machine": [], "process": [], "filament": []}

    with patch("app.services.catalog_utils.fetch_filaments", new_callable=AsyncMock) as mock_fetch:
        result = await compute_drift(old_cat, new_cat, drift_session, cfg)

    mock_fetch.assert_not_called()
    assert result is None  # no printers or jobs reference removed filament, so no drift
```

- [ ] **Step 8: Implement `compute_drift` in `catalog_utils.py`**

```python
# append to backend/app/services/catalog_utils.py
import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Printer, Job, JobPrinterConfig, SpoolmanConfig
from app.services.spoolman_service import fetch_filaments


async def compute_drift(
    old_catalog: dict,
    new_catalog: dict,
    session: AsyncSession,
    spoolman_cfg: SpoolmanConfig | None,
) -> dict | None:
    """Compare old vs new catalog; query live data for stale references.

    Returns a pending-remaps payload dict (without sync_id/status) if anything
    is affected, or None if the swap can proceed immediately.
    """
    old_machines, old_processes, old_filaments, old_uuids = catalog_name_sets(old_catalog)
    new_machines, new_processes, new_filaments, new_uuids = catalog_name_sets(new_catalog)

    removed_machines = old_machines - new_machines
    removed_processes = old_processes - new_processes
    removed_filaments = old_filaments - new_filaments
    removed_uuids = old_uuids - new_uuids

    if not any([removed_machines, removed_processes, removed_filaments, removed_uuids]):
        return None

    # --- Collect raw hits ---
    # Printers: group by (field, stale_value)
    printer_groups: dict[tuple[str, str], dict] = {}
    printers = (await session.execute(select(Printer))).scalars().all()
    for printer in printers:
        if printer.current_orca_printer_profile in removed_machines:
            key = ("current_orca_printer_profile", printer.current_orca_printer_profile)
            g = printer_groups.setdefault(key, {
                "field": "current_orca_printer_profile",
                "stale_value": printer.current_orca_printer_profile,
                "options_kind": "machine",
                "required": True,
                "affected_printer_ids": [],
                "affected_printer_names": [],
                "affected_slots": [],
            })
            g["affected_printer_ids"].append(printer.id)
            g["affected_printer_names"].append(printer.name)
            g["affected_slots"].append(None)

        for slot_idx, slot in enumerate(printer.loaded_filaments or []):
            fp = slot.get("filament_profile")
            if fp and fp in removed_filaments:
                key = ("loaded_filaments.filament_profile", fp)
                g = printer_groups.setdefault(key, {
                    "field": "loaded_filaments.filament_profile",
                    "stale_value": fp,
                    "options_kind": "filament",
                    "required": True,
                    "affected_printer_ids": [],
                    "affected_printer_names": [],
                    "affected_slots": [],
                })
                g["affected_printer_ids"].append(printer.id)
                g["affected_printer_names"].append(printer.name)
                g["affected_slots"].append(slot_idx)

    # Jobs: queued + blocked only; group by (field, stale_value)
    job_groups: dict[tuple[str, str], dict] = {}
    live_jobs = (await session.execute(
        select(Job).where(Job.status.in_(["queued", "blocked"]))
    )).scalars().all()
    for job in live_jobs:
        configs = (await session.execute(
            select(JobPrinterConfig).where(JobPrinterConfig.job_id == job.id)
        )).scalars().all()
        for cfg in configs:
            if cfg.print_profile in removed_processes:
                key = ("print_profile", cfg.print_profile)
                g = job_groups.setdefault(key, {
                    "field": "print_profile",
                    "stale_value": cfg.print_profile,
                    "options_kind": "process",
                    "required": False,
                    "affected_config_ids": [],
                    "affected_file_names": [],
                })
                g["affected_config_ids"].append(cfg.id)
                # file_name via uploaded_file join — use job id as fallback
                g["affected_file_names"].append(getattr(cfg, "file_name", None) or f"job#{job.id}")

            fp = cfg.filament_profile
            if fp and fp in removed_filaments:
                key = ("filament_profile", fp)
                g = job_groups.setdefault(key, {
                    "field": "filament_profile",
                    "stale_value": fp,
                    "options_kind": "filament",
                    "required": False,
                    "affected_config_ids": [],
                    "affected_file_names": [],
                })
                g["affected_config_ids"].append(cfg.id)
                g["affected_file_names"].append(getattr(cfg, "file_name", None) or f"job#{job.id}")

    # Spoolman filaments: group by stale_uuid
    spoolman_groups: dict[str, dict] = {}
    spoolman_error: str | None = None
    if spoolman_cfg and spoolman_cfg.enabled and spoolman_cfg.url and removed_uuids:
        try:
            spool_filaments = await fetch_filaments(spoolman_cfg.url, spoolman_cfg.api_key)
            for fil in spool_filaments:
                raw_extra = (fil.get("extra") or {}).get("orca_profiles")
                if not raw_extra:
                    continue
                try:
                    # double-JSON-encoded: outer string → dict
                    profiles: dict = json.loads(json.loads(raw_extra))
                except Exception:
                    continue
                for uid in profiles:
                    if uid in removed_uuids:
                        stale_name = profiles[uid] if isinstance(profiles[uid], str) else str(uid)
                        g = spoolman_groups.setdefault(uid, {
                            "stale_uuid": uid,
                            "stale_name": stale_name,
                            "options_kind": "filament_uuid",
                            "required": False,
                            "affected_filament_ids": [],
                            "affected_filament_names": [],
                        })
                        g["affected_filament_ids"].append(fil["id"])
                        g["affected_filament_names"].append(fil.get("name", str(fil["id"])))
        except Exception as exc:
            spoolman_error = str(exc)
            logger.warning("Spoolman fetch failed during drift check: %s", exc)

    all_printer = list(printer_groups.values())
    all_jobs = list(job_groups.values())
    all_spoolman = list(spoolman_groups.values())

    if not any([all_printer, all_jobs, all_spoolman]):
        return None

    return {
        "pending": {
            "printers": all_printer,
            "jobs": all_jobs,
            "spoolman_filaments": all_spoolman,
        },
        "options": {
            "machine": sorted(new_machines),
            "process": sorted(new_processes),
            "filament": sorted(new_filaments),
            "filament_uuids": [
                {"uuid": f["uuid"], "name": f["name"]}
                for f in new_catalog.get("filament", [])
                if f.get("uuid") and f.get("name")
            ],
        },
        "spoolman_error": spoolman_error,
    }
```

- [ ] **Step 9: Run all `compute_drift` tests**

```
cd backend && python -m pytest tests/services/test_catalog_utils.py -v
```
Expected: All pass.

- [ ] **Step 10: Refactor `settings.py` to use `catalog_name_sets`**

Replace lines 281-285 in `backend/app/api/routes/settings.py`:

Old code:
```python
    machine_names: set[str] = set()
    filament_names: set[str] = set()
    if cat:
        machine_names = {m["name"] for m in cat.get("machine", []) if m.get("name")}
        filament_names = {f["name"] for f in cat.get("filament", []) if f.get("name")}
```

New code:
```python
    machine_names: set[str] = set()
    filament_names: set[str] = set()
    if cat:
        from ...services.catalog_utils import catalog_name_sets
        machine_names, _, filament_names, _ = catalog_name_sets(cat)
```

- [ ] **Step 11: Run existing settings tests to confirm no regression**

```
cd backend && python -m pytest tests/api/test_settings_routes.py -v
```

- [ ] **Step 12: Commit**

```bash
git add backend/app/services/catalog_utils.py \
        backend/tests/services/test_catalog_utils.py \
        backend/app/api/routes/settings.py
git commit -m "feat: add catalog_utils with catalog_name_sets and compute_drift"
```

---

## Task 2: Refactor `laminus.py` — split fetch/commit, add `_pending_sync` slot

**Files:**
- Modify: `backend/app/api/routes/laminus.py`

### What this does

Split the atomic `_fetch_and_cache()` into `_fetch_catalog()` (pure fetch, no side effects) and `_commit_catalog()` (writes module-level globals). Add `_pending_sync` module-level slot. No behavior change yet — existing routes still work identically.

- [ ] **Step 1: Write a regression test for existing catalog behavior**

Create `backend/tests/api/test_laminus_api.py`:

```python
# backend/tests/api/test_laminus_api.py
"""Tests for the /api/v1/laminus/catalog/* routes (Features 2 and 3)."""
import json
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from httpx import AsyncClient


async def test_catalog_status_cold_cache(client: AsyncClient):
    """Status when catalog cache is empty."""
    import app.api.routes.laminus as lmod
    original = lmod._catalog_dict
    original_bytes = lmod._catalog_bytes
    lmod._catalog_dict = None
    lmod._catalog_bytes = None
    try:
        with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value=None):
            resp = await client.get("/api/v1/laminus/catalog/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["cached"] is False
        assert body["cached_bytes"] == 0
    finally:
        lmod._catalog_dict = original
        lmod._catalog_bytes = original_bytes
```

- [ ] **Step 2: Run test — it should pass (existing behavior)**

```
cd backend && python -m pytest tests/api/test_laminus_api.py::test_catalog_status_cold_cache -v
```

- [ ] **Step 3: Refactor `laminus.py` — split `_fetch_and_cache` and add `_pending_sync`**

Replace `_fetch_and_cache` with two functions and add `_pending_sync`:

```python
# In backend/app/api/routes/laminus.py, after the existing globals block:

# Module-level pending-sync slot. Holds {sync_id, raw, catalog, pending, created_at}.
# raw=None signals a Spoolman-only pending (no catalog swap on confirm).
_pending_sync: dict | None = None


async def _fetch_catalog() -> tuple[bytes, dict]:
    """Pull catalog from Laminus sidecar. No module-level side effects."""
    _, client = _sidecar_client()
    try:
        catalog = await asyncio.to_thread(client.get_catalog)
    except SidecarError as exc:
        raise HTTPException(502, f"Laminus sidecar unreachable: {exc}") from exc
    raw = json.dumps(catalog).encode()
    return raw, catalog


def _commit_catalog(raw: bytes, parsed: dict) -> None:
    """Write the fetched catalog to the module-level cache."""
    global _catalog_dict, _catalog_bytes, _catalog_fetched_at
    _catalog_dict = parsed
    _catalog_bytes = raw
    _catalog_fetched_at = time.time()
    logger.info("Catalog cached: %d bytes", len(raw))


async def _fetch_and_cache() -> bytes:
    """Backward-compat: fetch + commit in one step (used by warm_catalog_cache)."""
    raw, catalog = await _fetch_catalog()
    _commit_catalog(raw, catalog)
    return raw
```

Update `get_cached_catalog` to still use `_fetch_and_cache` (no change needed there since that's the warm path).

- [ ] **Step 4: Verify regression test still passes**

```
cd backend && python -m pytest tests/api/test_laminus_api.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/laminus.py
git commit -m "refactor(laminus): split _fetch_and_cache into _fetch_catalog/_commit_catalog, add _pending_sync slot"
```

---

## Task 3: Drift gate in refresh/rescan + Feature 3 catalog/status enhancements

**Files:**
- Modify: `backend/app/api/routes/laminus.py`
- Modify: `backend/tests/api/test_laminus_api.py`

### What this does

Integrate `compute_drift` into the refresh and rescan routes. Add `catalog_counts`, `status` string, and 30 s health memo to the status endpoint.

- [ ] **Step 1: Write failing test for happy-path refresh (no drift)**

```python
# append to test_laminus_api.py

SAMPLE_CATALOG = {
    "machine": [{"name": "Bambu X1C 0.4 nozzle", "uuid": "m1"}],
    "process": [{"name": "0.20mm Standard", "uuid": "p1"}],
    "filament": [{"name": "Generic PLA", "uuid": "f1"}],
}

async def test_refresh_no_drift_commits_immediately(client: AsyncClient):
    """Identical catalog → status ok, cache swapped."""
    import app.api.routes.laminus as lmod
    lmod._catalog_dict = SAMPLE_CATALOG
    lmod._catalog_bytes = json.dumps(SAMPLE_CATALOG).encode()

    with patch("app.api.routes.laminus._fetch_catalog", new_callable=AsyncMock) as mock_fetch, \
         patch("app.api.routes.laminus.get_session"):
        mock_fetch.return_value = (json.dumps(SAMPLE_CATALOG).encode(), SAMPLE_CATALOG)

        resp = await client.post("/api/v1/laminus/catalog/refresh")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["bytes"] > 0
```

- [ ] **Step 2: Write failing test for drift path — refresh with removed profile**

```python
# append to test_laminus_api.py

async def test_refresh_drift_returns_pending_remaps(client: AsyncClient):
    """Removed machine profile referenced by a printer → pending_remaps returned, old catalog kept."""
    import app.api.routes.laminus as lmod

    old_catalog = {
        "machine": [{"name": "Bambu X1C 0.4 nozzle", "uuid": "m1"}],
        "process": [], "filament": [],
    }
    new_catalog = {"machine": [], "process": [], "filament": []}

    # Seed a printer that references the removed profile
    from app.models import Printer
    from sqlalchemy import insert
    # Use the test client's DB via fixture session

    lmod._catalog_dict = old_catalog
    lmod._catalog_bytes = json.dumps(old_catalog).encode()
    old_bytes = lmod._catalog_bytes

    with patch("app.api.routes.laminus._fetch_catalog", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.catalog_utils.compute_drift", new_callable=AsyncMock) as mock_drift:
        mock_fetch.return_value = (json.dumps(new_catalog).encode(), new_catalog)
        mock_drift.return_value = {
            "pending": {
                "printers": [{"field": "current_orca_printer_profile", "stale_value": "Bambu X1C 0.4 nozzle",
                               "options_kind": "machine", "required": True,
                               "affected_printer_ids": [1], "affected_printer_names": ["X1C-left"],
                               "affected_slots": [None]}],
                "jobs": [], "spoolman_filaments": [],
            },
            "options": {"machine": [], "process": [], "filament": [], "filament_uuids": []},
            "spoolman_error": None,
        }

        resp = await client.post("/api/v1/laminus/catalog/refresh")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending_remaps"
    assert "sync_id" in body
    assert len(body["pending"]["printers"]) == 1

    # Old catalog must still be active
    assert lmod._catalog_bytes == old_bytes
    assert lmod._pending_sync is not None
    assert lmod._pending_sync["sync_id"] == body["sync_id"]
```

- [ ] **Step 3: Write tests for Feature 3 catalog/status**

```python
# append to test_laminus_api.py

async def test_catalog_status_includes_catalog_counts(client: AsyncClient):
    """catalog/status returns catalog_counts when cache is warm."""
    import app.api.routes.laminus as lmod
    lmod._catalog_dict = SAMPLE_CATALOG
    lmod._catalog_bytes = json.dumps(SAMPLE_CATALOG).encode()

    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value=None):
        resp = await client.get("/api/v1/laminus/catalog/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["catalog_counts"] == {"machine": 1, "process": 1, "filament": 1}


async def test_catalog_status_online(client: AsyncClient):
    """status='online' when sidecar health returns catalog_loaded=true."""
    import app.api.routes.laminus as lmod
    lmod._catalog_dict = SAMPLE_CATALOG
    lmod._catalog_bytes = json.dumps(SAMPLE_CATALOG).encode()

    health_response = MagicMock()
    health_response.status_code = 200
    health_response.json.return_value = {
        "catalog_loaded": True, "catalog_building": False, "catalog_profile_count": 142
    }

    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value="http://laminus:5000"), \
         patch("httpx.get", return_value=health_response):
        resp = await client.get("/api/v1/laminus/catalog/status")

    assert resp.status_code == 200
    assert resp.json()["status"] == "online"


async def test_catalog_status_building_via_flag(client: AsyncClient):
    """status='building' when catalog_building=true in health response."""
    import app.api.routes.laminus as lmod
    lmod._catalog_dict = None
    lmod._catalog_bytes = None

    health_response = MagicMock()
    health_response.status_code = 200
    health_response.json.return_value = {
        "catalog_loaded": False, "catalog_building": True, "catalog_profile_count": None
    }

    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value="http://laminus:5000"), \
         patch("httpx.get", return_value=health_response):
        resp = await client.get("/api/v1/laminus/catalog/status")

    assert resp.json()["status"] == "building"


async def test_catalog_status_offline_when_health_fails(client: AsyncClient):
    """status='offline' when health check raises an exception."""
    import app.api.routes.laminus as lmod
    lmod._catalog_dict = None
    lmod._catalog_bytes = None

    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value="http://laminus:5000"), \
         patch("httpx.get", side_effect=Exception("connection refused")):
        resp = await client.get("/api/v1/laminus/catalog/status")

    assert resp.json()["status"] == "offline"


async def test_catalog_status_unconfigured(client: AsyncClient):
    """status='unconfigured' when LAMINUS_SIDECAR_URL is not set."""
    with patch("app.api.routes.laminus.get_laminus_sidecar_url", return_value=None):
        resp = await client.get("/api/v1/laminus/catalog/status")

    assert resp.json()["status"] == "unconfigured"
```

- [ ] **Step 4: Implement drift gate in refresh and rescan, and Feature 3 status enhancements**

Replace the refresh and rescan handlers and update the status endpoint in `laminus.py`:

```python
# At the top of laminus.py, add imports:
import uuid as _uuid
from fastapi import Depends
from ..deps import get_session  # or however session is injected — check existing routes
from ...models import SpoolmanConfig
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

# Module-level health memo for 30s caching (Feature 3)
_health_memo: dict | None = None
_health_memo_at: float = 0.0
_HEALTH_MEMO_TTL = 30.0


@router.get("/catalog/status", summary="Catalog cache status")
async def get_catalog_status() -> dict:
    global _health_memo, _health_memo_at
    url = get_laminus_sidecar_url()
    laminus_status: dict | None = None
    status = "unconfigured"

    if url:
        now = time.time()
        if _health_memo is not None and now - _health_memo_at < _HEALTH_MEMO_TTL:
            h = _health_memo
        else:
            try:
                r = await asyncio.to_thread(
                    lambda: httpx.get(f"{url}/api/health", timeout=5)
                )
                if r.status_code == 200:
                    h = r.json()
                elif r.status_code == 503:
                    h = {"catalog_loaded": False, "catalog_building": True}
                else:
                    h = None
            except Exception:
                h = None
            _health_memo = h
            _health_memo_at = now

        if h is None:
            status = "offline"
        elif h.get("catalog_building"):
            status = "building"
        elif h.get("catalog_loaded"):
            status = "online"
        else:
            status = "offline"

        if h:
            laminus_status = {
                "catalog_loaded": h.get("catalog_loaded", False),
                "catalog_building": h.get("catalog_building", False),
                "profile_count": h.get("catalog_profile_count"),
            }

    catalog_counts = {
        "machine": len(_catalog_dict.get("machine", [])),
        "process": len(_catalog_dict.get("process", [])),
        "filament": len(_catalog_dict.get("filament", [])),
    } if _catalog_dict else None

    return {
        "cached": _catalog_bytes is not None,
        "cached_bytes": len(_catalog_bytes) if _catalog_bytes else 0,
        "fetched_at": _catalog_fetched_at,
        "laminus_configured": url is not None,
        "laminus": laminus_status,
        "catalog_counts": catalog_counts,
        "status": status,
    }


# Shared drift-gate logic used by both refresh and rescan
async def _apply_drift_gate(raw: bytes, new_catalog: dict, session: AsyncSession) -> dict:
    """Check drift, commit or park. Returns the HTTP response dict."""
    global _pending_sync
    old_catalog = _catalog_dict

    if old_catalog is None:
        # Cold cache — commit directly, no drift check
        _commit_catalog(raw, new_catalog)
        return {"status": "ok", "bytes": len(raw)}

    from ...services.catalog_utils import compute_drift

    # Load SpoolmanConfig for Spoolman section of drift check
    spoolman_cfg = await session.get(SpoolmanConfig, 1)

    drift = await compute_drift(old_catalog, new_catalog, session, spoolman_cfg)
    if drift is None:
        _commit_catalog(raw, new_catalog)
        return {"status": "ok", "bytes": len(raw)}

    sync_id = str(_uuid.uuid4())
    _pending_sync = {
        "sync_id": sync_id,
        "raw": raw,
        "catalog": new_catalog,
        "pending": drift["pending"],
        "created_at": time.time(),
    }
    return {
        "status": "pending_remaps",
        "sync_id": sync_id,
        **drift,
    }


@router.post("/catalog/refresh", summary="Refresh catalog from Laminus")
async def refresh_catalog(session: AsyncSession = Depends(get_session)) -> dict:
    raw, new_catalog = await _fetch_catalog()
    return await _apply_drift_gate(raw, new_catalog, session)


@router.post("/catalog/rescan", summary="Rescan profiles and refresh catalog")
async def rescan_and_refresh_catalog(session: AsyncSession = Depends(get_session)) -> dict:
    url, _ = _sidecar_client()

    try:
        r = await asyncio.to_thread(
            lambda: httpx.get(f"{url}/api/profiles?refresh=true", timeout=10)
        )
        if r.status_code not in (200, 503):
            raise HTTPException(502, f"Laminus rescan trigger returned {r.status_code}")
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Could not reach Laminus sidecar: {exc}") from exc

    deadline = time.time() + 120
    while time.time() < deadline:
        await asyncio.sleep(3)
        try:
            h = await asyncio.to_thread(
                lambda: httpx.get(f"{url}/api/health", timeout=5).json()
            )
            if not h.get("catalog_building", True) and h.get("catalog_loaded"):
                break
        except Exception:
            pass
    else:
        raise HTTPException(504, "Laminus catalog rebuild did not complete within 120 s")

    raw, new_catalog = await _fetch_catalog()
    return await _apply_drift_gate(raw, new_catalog, session)
```

**Important:** Check how other routes in `laminus.py` import `get_session`. Look at `backend/app/api/deps.py` or the existing route files. The import path may differ.

- [ ] **Step 5: Run tests**

```
cd backend && python -m pytest tests/api/test_laminus_api.py -v
```
Expected: All tests pass (including the new drift + status tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/laminus.py backend/tests/api/test_laminus_api.py
git commit -m "feat(laminus): drift gate in refresh/rescan, catalog/status enhancements (catalog_counts, status, 30s memo)"
```

---

## Task 4: `POST /catalog/confirm-remap` endpoint

**Files:**
- Modify: `backend/app/api/routes/laminus.py`
- Modify: `backend/tests/api/test_laminus_api.py`

### What this does

Validates resolutions (keyed by stale value, not per-object), applies DB updates (Printer + JobPrinterConfig) in one transaction, applies Spoolman patches best-effort, commits the parked catalog, clears the pending slot.

- [ ] **Step 1: Write failing tests for confirm-remap**

```python
# append to test_laminus_api.py
import app.api.routes.laminus as lmod

async def test_confirm_remap_unknown_sync_id_returns_409(client: AsyncClient):
    """confirm-remap with wrong sync_id → 409."""
    lmod._pending_sync = {
        "sync_id": "correct-id",
        "raw": b'{}', "catalog": {}, "pending": {"printers": [], "jobs": [], "spoolman_filaments": []},
        "created_at": 0,
    }
    resp = await client.post("/api/v1/laminus/catalog/confirm-remap", json={
        "sync_id": "wrong-id",
        "resolutions": {"printers": [], "jobs": [], "spoolman_filaments": []}
    })
    assert resp.status_code == 409


async def test_confirm_remap_no_pending_returns_409(client: AsyncClient):
    """confirm-remap with no pending slot → 409."""
    lmod._pending_sync = None
    resp = await client.post("/api/v1/laminus/catalog/confirm-remap", json={
        "sync_id": "any-id",
        "resolutions": {"printers": [], "jobs": [], "spoolman_filaments": []}
    })
    assert resp.status_code == 409


async def test_confirm_remap_missing_required_printer_resolution_returns_422(client: AsyncClient):
    """Missing required printer resolution → 422."""
    lmod._pending_sync = {
        "sync_id": "sync-1",
        "raw": b'{}', "catalog": {},
        "pending": {
            "printers": [{"field": "current_orca_printer_profile", "stale_value": "Stale Machine",
                          "required": True, "options_kind": "machine",
                          "affected_printer_ids": [1], "affected_printer_names": ["P1"],
                          "affected_slots": [None]}],
            "jobs": [], "spoolman_filaments": [],
        },
        "created_at": 0,
    }
    resp = await client.post("/api/v1/laminus/catalog/confirm-remap", json={
        "sync_id": "sync-1",
        "resolutions": {"printers": [], "jobs": [], "spoolman_filaments": []}
    })
    assert resp.status_code == 422


async def test_confirm_remap_applies_printer_update_and_commits_catalog(client: AsyncClient):
    """Valid confirm updates Printer row and commits pending catalog."""
    from app.models import Printer
    from sqlalchemy import insert
    # Create a printer via the printers API to get a real DB row
    create_resp = await client.post("/api/v1/printers", json={
        "name": "Test Printer",
        "printer_type": "bambu_x1c",
        "connection_config": {"ip_address": "1.2.3.4"},
        "current_orca_printer_profile": "Stale Machine",
        "orca_printer_profiles": ["Stale Machine"],
        "loaded_filaments": [],
    })
    assert create_resp.status_code == 201
    printer_id = create_resp.json()["id"]

    new_catalog = {"machine": [{"name": "New Machine", "uuid": "m2"}], "process": [], "filament": []}
    pending_bytes = json.dumps(new_catalog).encode()

    lmod._pending_sync = {
        "sync_id": "sync-apply",
        "raw": pending_bytes,
        "catalog": new_catalog,
        "pending": {
            "printers": [{"field": "current_orca_printer_profile", "stale_value": "Stale Machine",
                          "required": True, "options_kind": "machine",
                          "affected_printer_ids": [printer_id], "affected_printer_names": ["Test Printer"],
                          "affected_slots": [None]}],
            "jobs": [], "spoolman_filaments": [],
        },
        "created_at": 0,
    }

    resp = await client.post("/api/v1/laminus/catalog/confirm-remap", json={
        "sync_id": "sync-apply",
        "resolutions": {
            "printers": [{"field": "current_orca_printer_profile", "stale_value": "Stale Machine",
                          "new_value": "New Machine"}],
            "jobs": [], "spoolman_filaments": [],
        }
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["applied"]["printers"] == 1
    assert lmod._pending_sync is None
    assert lmod._catalog_dict == new_catalog

    # Verify DB was updated
    printer_resp = await client.get(f"/api/v1/printers/{printer_id}")
    assert printer_resp.json()["current_orca_printer_profile"] == "New Machine"


async def test_confirm_remap_spoolman_only_raw_none_skips_commit_catalog(client: AsyncClient):
    """When raw=None (Spoolman-only pending), confirm applies Spoolman patches only."""
    lmod._pending_sync = {
        "sync_id": "spoolman-only",
        "raw": None,
        "catalog": None,
        "pending": {
            "printers": [], "jobs": [],
            "spoolman_filaments": [{"stale_uuid": "old-uuid", "stale_name": "Old PLA",
                                    "required": False, "options_kind": "filament_uuid",
                                    "affected_filament_ids": [5], "affected_filament_names": ["Red PLA"]}],
        },
        "created_at": 0,
    }
    original_catalog = lmod._catalog_dict

    with patch("app.services.spoolman_service.patch_filament", new_callable=AsyncMock):
        resp = await client.post("/api/v1/laminus/catalog/confirm-remap", json={
            "sync_id": "spoolman-only",
            "resolutions": {
                "printers": [], "jobs": [],
                "spoolman_filaments": [{"stale_uuid": "old-uuid", "new_uuid": None}]
            }
        })

    assert resp.status_code == 200
    assert lmod._pending_sync is None
    assert lmod._catalog_dict is original_catalog  # catalog was NOT swapped
```

- [ ] **Step 2: Run tests to see them fail**

```
cd backend && python -m pytest tests/api/test_laminus_api.py::test_confirm_remap_unknown_sync_id_returns_409 -v
```
Expected: 404 (route doesn't exist yet).

- [ ] **Step 3: Implement `POST /catalog/confirm-remap`**

Add to `backend/app/api/routes/laminus.py`:

```python
from pydantic import BaseModel


class PrinterResolution(BaseModel):
    field: str
    stale_value: str
    new_value: str | None


class JobResolution(BaseModel):
    field: str
    stale_value: str
    new_value: str | None = None


class SpoolmanResolution(BaseModel):
    stale_uuid: str
    new_uuid: str | None = None


class ConfirmRemapBody(BaseModel):
    sync_id: str
    resolutions: dict  # {printers: [...], jobs: [...], spoolman_filaments: [...]}


@router.post("/catalog/confirm-remap", summary="Confirm pending profile remap and commit catalog")
async def confirm_remap(body: ConfirmRemapBody, session: AsyncSession = Depends(get_session)) -> dict:
    global _pending_sync

    if _pending_sync is None or _pending_sync["sync_id"] != body.sync_id:
        raise HTTPException(409, "Sync superseded or expired — re-run the catalog sync")

    pending = _pending_sync["pending"]
    resolutions = body.resolutions

    # Validate: every required printer entry must have a non-null resolution
    # and the new_value must be in the incoming catalog's machine/filament names
    incoming_catalog = _pending_sync.get("catalog") or {}
    from ...services.catalog_utils import catalog_name_sets
    new_machines, new_processes, new_filaments, new_uuids = catalog_name_sets(incoming_catalog)

    printer_res_map: dict[tuple[str, str], str | None] = {
        (r["field"], r["stale_value"]): r.get("new_value")
        for r in resolutions.get("printers", [])
    }
    job_res_map: dict[tuple[str, str], str | None] = {
        (r["field"], r["stale_value"]): r.get("new_value")
        for r in resolutions.get("jobs", [])
    }
    spoolman_res_map: dict[str, str | None] = {
        r["stale_uuid"]: r.get("new_uuid")
        for r in resolutions.get("spoolman_filaments", [])
    }

    unresolved = []
    for entry in pending.get("printers", []):
        key = (entry["field"], entry["stale_value"])
        new_val = printer_res_map.get(key)
        if entry.get("required") and not new_val:
            unresolved.append(f"Printer {entry['field']}={entry['stale_value']}")
        elif new_val:
            valid_set = new_machines if entry.get("options_kind") == "machine" else new_filaments
            if new_val not in valid_set:
                unresolved.append(f"Invalid value '{new_val}' for {entry['field']}")

    if unresolved:
        raise HTTPException(422, {"detail": "Unresolved required remaps", "unresolved": unresolved})

    # Apply DB updates in a single transaction
    from ...models import Printer as PrinterModel, JobPrinterConfig as JPC
    from sqlalchemy import select as _select

    applied_printers = 0
    for entry in pending.get("printers", []):
        key = (entry["field"], entry["stale_value"])
        new_val = printer_res_map.get(key)  # may be None for optional

        for printer_id, slot in zip(entry["affected_printer_ids"], entry["affected_slots"]):
            printer = await session.get(PrinterModel, printer_id)
            if printer is None:
                continue
            if slot is None:
                printer.current_orca_printer_profile = new_val
            else:
                loaded = list(printer.loaded_filaments or [])
                if slot < len(loaded):
                    loaded[slot] = {**loaded[slot], "filament_profile": new_val}
                    printer.loaded_filaments = loaded
            applied_printers += 1

    applied_jobs = 0
    for entry in pending.get("jobs", []):
        key = (entry["field"], entry["stale_value"])
        new_val = job_res_map.get(key)
        for cfg_id in entry["affected_config_ids"]:
            cfg = await session.get(JPC, cfg_id)
            if cfg is None:
                continue
            if entry["field"] == "print_profile":
                cfg.print_profile = new_val or ""
            else:
                cfg.filament_profile = new_val
            applied_jobs += 1

    await session.commit()

    # Spoolman patches — best-effort after DB commit
    from ...services import spoolman_service
    spoolman_failures: list[str] = []
    applied_spoolman = 0
    for entry in pending.get("spoolman_filaments", []):
        new_uuid = spoolman_res_map.get(entry["stale_uuid"])
        stale_uuid = entry["stale_uuid"]
        for fil_id in entry["affected_filament_ids"]:
            try:
                # We'd need the SpoolmanConfig URL + api_key — fetch from DB
                spoolman_cfg = await session.get(__import__("app.models", fromlist=["SpoolmanConfig"]).SpoolmanConfig, 1)
                if not spoolman_cfg or not spoolman_cfg.url:
                    break
                # Fetch current extra.orca_profiles for this filament
                import httpx as _httpx
                import json as _json
                headers = {}
                if spoolman_cfg.api_key:
                    headers["X-API-Key"] = spoolman_cfg.api_key
                r = await asyncio.to_thread(
                    lambda: _httpx.get(
                        f"{spoolman_cfg.url.rstrip('/')}/api/v1/filament/{fil_id}",
                        headers=headers, timeout=10,
                    )
                )
                r.raise_for_status()
                fil_data = r.json()
                raw_extra = (fil_data.get("extra") or {}).get("orca_profiles", "null")
                try:
                    profiles: dict = _json.loads(_json.loads(raw_extra))
                except Exception:
                    profiles = {}
                profiles.pop(stale_uuid, None)
                if new_uuid:
                    # Look up name from incoming catalog
                    name = next(
                        (f["name"] for f in (_pending_sync.get("catalog") or {}).get("filament", [])
                         if f.get("uuid") == new_uuid),
                        new_uuid,
                    )
                    profiles[new_uuid] = name
                await spoolman_service.patch_filament(
                    spoolman_cfg.url, spoolman_cfg.api_key, fil_id, profiles
                )
                applied_spoolman += 1
            except Exception as exc:
                spoolman_failures.append(f"filament {fil_id}: {exc}")
                logger.warning("Spoolman patch failed for filament %s: %s", fil_id, exc)

    # Commit catalog only when raw is not None (Spoolman-only pending skips this)
    if _pending_sync.get("raw") is not None:
        _commit_catalog(_pending_sync["raw"], _pending_sync["catalog"])

    _pending_sync = None
    return {
        "status": "ok",
        "applied": {"printers": applied_printers, "jobs": applied_jobs, "spoolman_filaments": applied_spoolman},
        "spoolman_failures": spoolman_failures,
    }
```

- [ ] **Step 4: Run confirm-remap tests**

```
cd backend && python -m pytest tests/api/test_laminus_api.py -k "confirm_remap" -v
```
Expected: All pass.

- [ ] **Step 5: Run the full laminus test file**

```
cd backend && python -m pytest tests/api/test_laminus_api.py -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/laminus.py backend/tests/api/test_laminus_api.py
git commit -m "feat(laminus): add POST /catalog/confirm-remap endpoint"
```

---

## Task 5: Spoolman sanity check on test-connection

**Files:**
- Modify: `backend/app/api/routes/settings.py`
- Modify: `backend/tests/api/test_settings_routes.py`

### What this does

After a successful Spoolman test-connection, compare every Spoolman filament's `extra.orca_profiles` UUID keys against the Themis cached catalog filament UUID set. If stale UUIDs found, write `_pending_sync` with `raw=None` and return `status: "pending_remaps"`.

- [ ] **Step 1: Write failing tests for the sanity check**

```python
# append to backend/tests/api/test_settings_routes.py
import json
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient
import app.api.routes.laminus as lmod


async def test_spoolman_test_connection_all_uuids_valid_returns_ok(client: AsyncClient):
    """All Spoolman filament UUIDs present in catalog → normal success response."""
    catalog = {"machine": [], "process": [], "filament": [{"name": "PLA", "uuid": "f1"}]}
    lmod._catalog_dict = catalog

    version_response = MagicMock()
    version_response.raise_for_status = MagicMock()
    version_response.json.return_value = {"version": "0.19.0"}

    filaments_response = [
        {"id": 1, "name": "PLA Red", "extra": {"orca_profiles": json.dumps(json.dumps({"f1": "PLA"}))}}
    ]

    with patch("app.services.spoolman_service.test_connection", new_callable=AsyncMock) as mock_test, \
         patch("app.services.spoolman_service.fetch_filaments", new_callable=AsyncMock) as mock_fetch:
        mock_test.return_value = {"version": "0.19.0"}
        mock_fetch.return_value = filaments_response

        resp = await client.post("/api/v1/settings/spoolman/test", json={"url": "http://spoolman.test"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok" or body.get("ok") is True  # existing shape or new shape


async def test_spoolman_test_connection_stale_uuid_returns_pending_remaps(client: AsyncClient):
    """Three filaments share one stale UUID → single grouped entry with three affected_filament_ids."""
    catalog = {"machine": [], "process": [], "filament": [{"name": "PLA New", "uuid": "f-new"}]}
    lmod._catalog_dict = catalog
    lmod._pending_sync = None

    filaments_response = [
        {"id": 9, "name": "Red PLA", "extra": {"orca_profiles": json.dumps(json.dumps({"stale-uuid": "PLA Old"}))}},
        {"id": 14, "name": "Blue PLA", "extra": {"orca_profiles": json.dumps(json.dumps({"stale-uuid": "PLA Old"}))}},
        {"id": 22, "name": "White PLA", "extra": {"orca_profiles": json.dumps(json.dumps({"stale-uuid": "PLA Old"}))}},
    ]

    with patch("app.services.spoolman_service.test_connection", new_callable=AsyncMock) as mock_test, \
         patch("app.services.spoolman_service.fetch_filaments", new_callable=AsyncMock) as mock_fetch:
        mock_test.return_value = {"version": "0.19.0"}
        mock_fetch.return_value = filaments_response

        resp = await client.post("/api/v1/settings/spoolman/test", json={"url": "http://spoolman.test"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending_remaps"
    assert "sync_id" in body
    spool_entries = body["pending"]["spoolman_filaments"]
    assert len(spool_entries) == 1
    assert set(spool_entries[0]["affected_filament_ids"]) == {9, 14, 22}
    assert body["pending"]["printers"] == []
    assert body["pending"]["jobs"] == []
    assert lmod._pending_sync is not None
    assert lmod._pending_sync["raw"] is None  # Spoolman-only


async def test_spoolman_test_connection_cold_catalog_returns_ok(client: AsyncClient):
    """Cold cache → skip UUID check, return normal success."""
    lmod._catalog_dict = None

    with patch("app.services.spoolman_service.test_connection", new_callable=AsyncMock) as mock_test, \
         patch("app.services.spoolman_service.fetch_filaments", new_callable=AsyncMock) as mock_fetch:
        mock_test.return_value = {"version": "0.19.0"}

        resp = await client.post("/api/v1/settings/spoolman/test", json={"url": "http://spoolman.test"})

    mock_fetch.assert_not_called()
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to see them fail**

```
cd backend && python -m pytest tests/api/test_settings_routes.py -k "spoolman" -v
```

- [ ] **Step 3: Add Spoolman sanity check to `settings.py`**

Replace the `test_spoolman_connection` handler in `backend/app/api/routes/settings.py`:

```python
@router.post("/spoolman/test", summary="Test Spoolman connection")
async def test_spoolman_connection(
    body: SpoolmanConfigIn,
    session: AsyncSession = Depends(get_session),
):
    """Verify connectivity to Spoolman. On success, checks Spoolman filament UUIDs
    against the cached catalog; returns pending_remaps if stale UUIDs found."""
    url = body.url
    api_key = body.api_key
    if not url:
        row = await _get_or_create(session)
        url = row.url
        if api_key is None:
            api_key = row.api_key
    if not url:
        return {"status": "error", "ok": False, "message": "No URL configured"}
    try:
        info = await spoolman_service.test_connection(url, api_key)
    except Exception as e:
        return {"status": "error", "ok": False, "message": str(e)}

    # Spoolman is reachable — check for stale UUIDs vs cached catalog
    from .laminus import _catalog_dict, _pending_sync as _lam_pending_sync
    import app.api.routes.laminus as _lam_mod

    if _catalog_dict is None:
        # Cold cache — skip UUID check
        return {"status": "ok", "ok": True, "version": info.get("version", "unknown")}

    from ...services.catalog_utils import catalog_name_sets
    import json as _json
    import uuid as _uuid

    _, _, _, catalog_uuids = catalog_name_sets(_catalog_dict)

    try:
        filaments = await spoolman_service.fetch_filaments(url, api_key)
    except Exception:
        return {"status": "ok", "ok": True, "version": info.get("version", "unknown")}

    # Group stale UUIDs
    spoolman_groups: dict[str, dict] = {}
    for fil in filaments:
        raw_extra = (fil.get("extra") or {}).get("orca_profiles")
        if not raw_extra:
            continue
        try:
            profiles: dict = _json.loads(_json.loads(raw_extra))
        except Exception:
            continue
        for uid, name in profiles.items():
            if uid not in catalog_uuids:
                g = spoolman_groups.setdefault(uid, {
                    "stale_uuid": uid,
                    "stale_name": name if isinstance(name, str) else str(uid),
                    "options_kind": "filament_uuid",
                    "required": False,
                    "affected_filament_ids": [],
                    "affected_filament_names": [],
                })
                g["affected_filament_ids"].append(fil["id"])
                g["affected_filament_names"].append(fil.get("name", str(fil["id"])))

    if not spoolman_groups:
        return {"status": "ok", "ok": True, "version": info.get("version", "unknown")}

    sync_id = str(_uuid.uuid4())
    _lam_mod._pending_sync = {
        "sync_id": sync_id,
        "raw": None,
        "catalog": None,
        "pending": {
            "printers": [], "jobs": [],
            "spoolman_filaments": list(spoolman_groups.values()),
        },
        "created_at": __import__("time").time(),
    }
    return {
        "status": "pending_remaps",
        "ok": True,
        "sync_id": sync_id,
        "pending": _lam_mod._pending_sync["pending"],
        "options": {
            "machine": [], "process": [], "filament": [],
            "filament_uuids": [
                {"uuid": f["uuid"], "name": f["name"]}
                for f in _catalog_dict.get("filament", [])
                if f.get("uuid") and f.get("name")
            ],
        },
        "spoolman_error": None,
    }
```

- [ ] **Step 4: Run sanity check tests**

```
cd backend && python -m pytest tests/api/test_settings_routes.py -v
```
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/settings.py backend/tests/api/test_settings_routes.py
git commit -m "feat(settings): Spoolman sanity check on test-connection returns SyncResponse"
```

---

## Task 6: Frontend `api/laminus.ts` (new) and fix `api/orca.ts` route URLs

**Files:**
- Create: `frontend/src/api/laminus.ts`
- Modify: `frontend/src/api/orca.ts`

### What this does

Creates `laminus.ts` with `SyncResponse` discriminated union types and the three catalog mutation functions (`refreshCatalog`, `rescanCatalog`, `confirmRemap`). Fixes the pre-existing bug in `orca.ts` where all routes point to `/api/v1/orca/catalog/*` instead of `/api/v1/laminus/catalog/*`, and fixes the `OrcaCatalogStatus.orca` field name (should be `laminus`).

- [ ] **Step 1: Create `frontend/src/api/laminus.ts`**

```typescript
// frontend/src/api/laminus.ts

export interface SyncOk {
  status: 'ok';
  bytes: number;
}

export interface PrinterPendingEntry {
  field: string;
  stale_value: string;
  options_kind: 'machine' | 'filament';
  required: true;
  affected_printer_ids: number[];
  affected_printer_names: string[];
  affected_slots: (number | null)[];
}

export interface JobPendingEntry {
  field: string;
  stale_value: string;
  options_kind: 'process' | 'filament';
  required: false;
  affected_config_ids: number[];
  affected_file_names: string[];
}

export interface SpoolmanPendingEntry {
  stale_uuid: string;
  stale_name: string;
  options_kind: 'filament_uuid';
  required: false;
  affected_filament_ids: number[];
  affected_filament_names: string[];
}

export interface PendingRemaps {
  status: 'pending_remaps';
  sync_id: string;
  pending: {
    printers: PrinterPendingEntry[];
    jobs: JobPendingEntry[];
    spoolman_filaments: SpoolmanPendingEntry[];
  };
  options: {
    machine: string[];
    process: string[];
    filament: string[];
    filament_uuids: { uuid: string; name: string }[];
  };
  spoolman_error: string | null;
}

export type SyncResponse = SyncOk | PendingRemaps;

export interface PrinterResolution {
  field: string;
  stale_value: string;
  new_value: string | null;
}

export interface JobResolution {
  field: string;
  stale_value: string;
  new_value: string | null;
}

export interface SpoolmanResolution {
  stale_uuid: string;
  new_uuid: string | null;
}

export interface Resolutions {
  printers: PrinterResolution[];
  jobs: JobResolution[];
  spoolman_filaments: SpoolmanResolution[];
}

export interface ConfirmResult {
  status: 'ok';
  applied: { printers: number; jobs: number; spoolman_filaments: number };
  spoolman_failures: string[];
}

export async function refreshCatalog(): Promise<SyncResponse> {
  const r = await fetch('/api/v1/laminus/catalog/refresh', { method: 'POST' });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

export async function rescanCatalog(): Promise<SyncResponse> {
  const r = await fetch('/api/v1/laminus/catalog/rescan', { method: 'POST' });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

export async function confirmRemap(syncId: string, resolutions: Resolutions): Promise<ConfirmResult> {
  const r = await fetch('/api/v1/laminus/catalog/confirm-remap', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sync_id: syncId, resolutions }),
  });
  if (r.status === 409) throw Object.assign(new Error('sync_superseded'), { status: 409 });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}
```

- [ ] **Step 2: Fix route URLs and field name in `frontend/src/api/orca.ts`**

Change all `/api/v1/orca/` to `/api/v1/laminus/` and fix `OrcaCatalogStatus.orca` → `laminus`:

```typescript
// frontend/src/api/orca.ts — change these lines:

export interface OrcaCatalogStatus {
  cached: boolean;
  cached_bytes: number;
  fetched_at: number | null;
  laminus: { catalog_loaded: boolean; catalog_building: boolean; profile_count: { machine: number; process: number; filament: number } | null } | null;
  catalog_counts: { machine: number; process: number; filament: number } | null;
  status: 'online' | 'building' | 'offline' | 'unconfigured';
}

export const getOrcaCatalog = (): Promise<OrcaCatalog> =>
  fetch('/api/v1/laminus/catalog').then(r => {
    if (!r.ok) throw new Error(`${r.status}`);
    return r.json();
  });

export const getOrcaCatalogStatus = (): Promise<OrcaCatalogStatus> =>
  fetch('/api/v1/laminus/catalog/status').then(r => r.json());

// Keep these two for backward compatibility but they now delegate to laminus.ts
export { refreshCatalog as refreshOrcaCatalog, rescanCatalog as rescanOrcaCatalog } from './laminus';
```

Actually, rather than re-exporting with aliases (which is a backwards-compat shim and the style guide says to avoid those), update `SettingsScreen.tsx` imports directly. Keep `orca.ts` clean:

```typescript
// Remove refreshOrcaCatalog and rescanOrcaCatalog from orca.ts entirely
// (SettingsScreen.tsx will import from laminus.ts instead)
```

- [ ] **Step 3: Run TypeScript check to verify no type errors**

```
cd frontend && npx tsc --noEmit
```
Expected: 0 errors (or only pre-existing errors if any).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/laminus.ts frontend/src/api/orca.ts
git commit -m "feat(frontend): add api/laminus.ts with SyncResponse types; fix orca.ts route URLs (orca→laminus)"
```

---

## Task 7: Update `api/spoolman.ts` — `testSpoolmanConnection` returns `SyncResponse`

**Files:**
- Modify: `frontend/src/api/spoolman.ts`

### What this does

The backend now returns `SyncResponse` from the test-connection endpoint. Update the TypeScript return type and handle both `status: 'ok'` and `status: 'pending_remaps'` in callers.

- [ ] **Step 1: Read `frontend/src/api/spoolman.ts` lines around `testSpoolmanConnection`**

Locate the function (approx. line 94 based on prior session) and update its return type:

```typescript
// In frontend/src/api/spoolman.ts, update testSpoolmanConnection:
import type { SyncResponse } from './laminus';

export const testSpoolmanConnection = async (
  url: string,
  api_key: string | null,
): Promise<SyncResponse> => {
  const r = await fetch('/api/v1/settings/spoolman/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, api_key }),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.message || `${r.status}`);
  }
  return r.json();
};
```

- [ ] **Step 2: Run TypeScript check**

```
cd frontend && npx tsc --noEmit
```
Expected: 0 new errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/spoolman.ts
git commit -m "feat(spoolman): testSpoolmanConnection returns SyncResponse"
```

---

## Task 8: `RemapModal.tsx` — stale-reference resolution modal

**Files:**
- Create: `frontend/src/components/RemapModal.tsx`

### What this does

A modal component that receives a `PendingRemaps` payload and lets the user pick replacements for each stale profile reference. Printer entries are required; job and Spoolman entries are optional (default: clear). Groups by stale value — one row per unique stale value, not one row per affected object.

- [ ] **Step 1: Create `frontend/src/components/RemapModal.tsx`**

```tsx
// frontend/src/components/RemapModal.tsx
import React, { useState } from 'react';
import type { PendingRemaps, Resolutions } from '../api/laminus';

interface Props {
  payload: PendingRemaps;
  onDone: (result: import('../api/laminus').ConfirmResult) => void;
  onCancel: () => void;
  onConfirm: (syncId: string, resolutions: Resolutions) => Promise<import('../api/laminus').ConfirmResult>;
}

type SelectionMap = Record<string, string | null>;  // key: stale_value or stale_uuid, value: new value

export function RemapModal({ payload, onDone, onCancel, onConfirm }: Props) {
  const [printerSelections, setPrinterSelections] = useState<SelectionMap>({});
  const [jobSelections, setJobSelections] = useState<SelectionMap>({});
  const [spoolmanSelections, setSpoolmanSelections] = useState<SelectionMap>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { pending, options, spoolman_error, sync_id } = payload;

  // Confirm is disabled until every required row has a selection
  const requiredPrintersMet = pending.printers.every(entry => {
    const sel = printerSelections[`${entry.field}|${entry.stale_value}`];
    return !entry.required || (sel !== undefined && sel !== null && sel !== '');
  });
  const canConfirm = requiredPrintersMet && !submitting;

  const handleConfirm = async () => {
    setSubmitting(true);
    setError(null);
    const resolutions: Resolutions = {
      printers: pending.printers.map(entry => ({
        field: entry.field,
        stale_value: entry.stale_value,
        new_value: printerSelections[`${entry.field}|${entry.stale_value}`] ?? null,
      })),
      jobs: pending.jobs.map(entry => ({
        field: entry.field,
        stale_value: entry.stale_value,
        new_value: jobSelections[`${entry.field}|${entry.stale_value}`] ?? null,
      })),
      spoolman_filaments: pending.spoolman_filaments.map(entry => ({
        stale_uuid: entry.stale_uuid,
        new_uuid: spoolmanSelections[entry.stale_uuid] ?? null,
      })),
    };
    try {
      const result = await onConfirm(sync_id, resolutions);
      onDone(result);
    } catch (err: any) {
      if (err?.status === 409) {
        setError('Sync superseded — run the catalog sync again');
      } else {
        setError(err?.message ?? 'Unknown error');
      }
      setSubmitting(false);
    }
  };

  const countBadge = (names: string[], noun: string) =>
    names.length === 1 ? names[0] : `affects ${names.length} ${noun}`;

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ background: 'var(--surface, #1e1e2e)', borderRadius: 8, padding: 24, minWidth: 480, maxWidth: 640, maxHeight: '80vh', overflowY: 'auto' }}>
        <h2 style={{ marginTop: 0 }}>Profile References Need Remapping</h2>
        <p style={{ color: 'var(--text-muted, #aaa)', fontSize: 14 }}>
          The incoming catalog removed profiles that are still referenced. Printers require a replacement; jobs and Spoolman filaments can be cleared.
        </p>

        {spoolman_error && (
          <div style={{ background: '#7c2d12', padding: '8px 12px', borderRadius: 4, marginBottom: 12, fontSize: 13 }}>
            ⚠ Spoolman references could not be fully checked this sync: {spoolman_error}
          </div>
        )}

        {pending.printers.length > 0 && (
          <section>
            <h3>Printers</h3>
            {pending.printers.map(entry => {
              const key = `${entry.field}|${entry.stale_value}`;
              const optList = entry.options_kind === 'machine' ? options.machine : options.filament;
              return (
                <div key={key} style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 13, color: 'var(--text-muted, #aaa)' }}>
                    <s>{entry.stale_value}</s>
                    {' → '}
                    <span style={{ fontSize: 12 }}>{countBadge(entry.affected_printer_names, 'printers')}</span>
                  </div>
                  <select
                    value={printerSelections[key] ?? ''}
                    onChange={e => setPrinterSelections(s => ({ ...s, [key]: e.target.value || null }))}
                    style={{ width: '100%', marginTop: 4, padding: '4px 8px' }}
                  >
                    <option value="">— select a replacement —</option>
                    {optList.map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                  {entry.required && !printerSelections[key] && (
                    <div style={{ color: 'var(--err, #f87171)', fontSize: 12, marginTop: 2 }}>Required</div>
                  )}
                </div>
              );
            })}
          </section>
        )}

        {pending.jobs.length > 0 && (
          <section>
            <h3>Queued Jobs</h3>
            {pending.jobs.map(entry => {
              const key = `${entry.field}|${entry.stale_value}`;
              const optList = entry.options_kind === 'process' ? options.process : options.filament;
              return (
                <div key={key} style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 13, color: 'var(--text-muted, #aaa)' }}>
                    <s>{entry.stale_value}</s>
                    {' → '}
                    <span style={{ fontSize: 12 }}>{countBadge(entry.affected_file_names, 'jobs')}</span>
                  </div>
                  <select
                    value={printerSelections[key] ?? ''}
                    onChange={e => setJobSelections(s => ({ ...s, [key]: e.target.value || null }))}
                    style={{ width: '100%', marginTop: 4, padding: '4px 8px' }}
                  >
                    <option value="">— clear —</option>
                    {optList.map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                </div>
              );
            })}
          </section>
        )}

        {pending.spoolman_filaments.length > 0 && (
          <section>
            <h3>Spoolman Filaments</h3>
            {pending.spoolman_filaments.map(entry => (
              <div key={entry.stale_uuid} style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 13, color: 'var(--text-muted, #aaa)' }}>
                  <s>{entry.stale_name}</s>
                  {' → '}
                  <span style={{ fontSize: 12 }}>{countBadge(entry.affected_filament_names, 'filaments')}</span>
                </div>
                <select
                  value={spoolmanSelections[entry.stale_uuid] ?? ''}
                  onChange={e => setSpoolmanSelections(s => ({ ...s, [entry.stale_uuid]: e.target.value || null }))}
                  style={{ width: '100%', marginTop: 4, padding: '4px 8px' }}
                >
                  <option value="">— clear —</option>
                  {options.filament_uuids.map(o => (
                    <option key={o.uuid} value={o.uuid}>{o.name}</option>
                  ))}
                </select>
              </div>
            ))}
          </section>
        )}

        {error && (
          <div style={{ color: 'var(--err, #f87171)', fontSize: 13, marginBottom: 8 }}>{error}</div>
        )}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
          <button onClick={onCancel} disabled={submitting} style={{ padding: '6px 16px' }}>
            Cancel
          </button>
          <button
            onClick={handleConfirm}
            disabled={!canConfirm}
            style={{ padding: '6px 16px', background: canConfirm ? 'var(--accent, #7c3aed)' : '#555', color: '#fff', border: 'none', borderRadius: 4, cursor: canConfirm ? 'pointer' : 'default' }}
          >
            {submitting ? 'Applying…' : 'Confirm'}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: TypeScript check**

```
cd frontend && npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/RemapModal.tsx
git commit -m "feat: add RemapModal component for stale profile remap flow"
```

---

## Task 9: `SettingsScreen.tsx` — wire remap modal

**Files:**
- Modify: `frontend/src/screens/SettingsScreen.tsx`

### What this does

The "Refresh Catalog", "Rescan", and "Test Connection" handlers branch on `SyncResponse.status`. On `pending_remaps`, open the `RemapModal`. On success after confirm, show a toast with remap count.

- [ ] **Step 1: Read `SettingsScreen.tsx` around the doRefreshCatalog, doRescanCatalog, and testSpoolmanConnection handlers**

Relevant lines:
- Line 3: `import { getSpoolmanConfig, saveSpoolmanConfig, testSpoolmanConnection, useSpools, useSpoolmanConfig } from '../api/spoolman';`
- Line 7: `import { getOrcaCatalogStatus, refreshOrcaCatalog, rescanOrcaCatalog, type OrcaCatalogStatus } from '../api/orca';`
- Approx line 469: `doRefreshCatalog`
- Approx line 484: `doRescanCatalog`
- Approx line 701: `testSpoolmanConnection` call

- [ ] **Step 2: Update imports in `SettingsScreen.tsx`**

Add to imports:
```typescript
import { refreshCatalog, rescanCatalog, confirmRemap, type SyncResponse, type PendingRemaps, type ConfirmResult } from '../api/laminus';
import { RemapModal } from '../components/RemapModal';
```

Remove `refreshOrcaCatalog` and `rescanOrcaCatalog` from the `orca` import (they're gone from `orca.ts`).

- [ ] **Step 3: Add `pendingRemap` state**

Near other state declarations:
```typescript
const [pendingRemap, setPendingRemap] = useState<PendingRemaps | null>(null);
```

- [ ] **Step 4: Update `doRefreshCatalog`**

Find and replace the existing handler (approx. line 469):

```typescript
async function doRefreshCatalog() {
  setCatalogStatus(s => ({ ...s, loading: true }));
  try {
    const r = await refreshCatalog();
    if (r.status === 'pending_remaps') {
      setPendingRemap(r);
    } else {
      showToast(`Catalog refreshed — ${(r.bytes / 1024).toFixed(0)} KB cached`);
      doLoadCatalogStatus();
    }
  } catch (err: any) {
    showToast(`Refresh failed: ${err.message}`, 'error');
  } finally {
    setCatalogStatus(s => ({ ...s, loading: false }));
  }
}
```

- [ ] **Step 5: Update `doRescanCatalog`**

```typescript
async function doRescanCatalog() {
  setCatalogStatus(s => ({ ...s, loading: true }));
  try {
    const r = await rescanCatalog();
    if (r.status === 'pending_remaps') {
      setPendingRemap(r);
    } else {
      showToast(`Catalog rescanned — ${(r.bytes / 1024).toFixed(0)} KB cached`);
      doLoadCatalogStatus();
    }
  } catch (err: any) {
    showToast(`Rescan failed: ${err.message}`, 'error');
  } finally {
    setCatalogStatus(s => ({ ...s, loading: false }));
  }
}
```

- [ ] **Step 6: Update the `testSpoolmanConnection` handler (approx. line 701)**

Find the block that calls `testSpoolmanConnection` and update it to handle `SyncResponse`:

```typescript
const result = await testSpoolmanConnection(s.url, s.apiKey || null);
if (result.status === 'pending_remaps') {
  setPendingRemap(result);
  setSpoolmanTestStatus({ ok: true, message: 'Connected — stale profile references found' });
} else {
  setSpoolmanTestStatus({ ok: true, message: `Connected — Spoolman v${(result as any).version ?? 'unknown'}` });
}
```

- [ ] **Step 7: Add `RemapModal` to the JSX**

Inside the SettingsScreen return, add the modal (at the end, just before the closing tag):

```tsx
{pendingRemap && (
  <RemapModal
    payload={pendingRemap}
    onConfirm={confirmRemap}
    onDone={(result: ConfirmResult) => {
      setPendingRemap(null);
      const total = result.applied.printers + result.applied.jobs + result.applied.spoolman_filaments;
      showToast(`Catalog updated — ${total} reference${total !== 1 ? 's' : ''} remapped`);
      if (result.spoolman_failures.length > 0) {
        showToast(`${result.spoolman_failures.length} Spoolman update(s) failed — check console`, 'warning');
      }
      doLoadCatalogStatus();
    }}
    onCancel={() => {
      setPendingRemap(null);
      showToast('Catalog sync cancelled — profiles unchanged', 'info');
    }}
  />
)}
```

- [ ] **Step 8: TypeScript check**

```
cd frontend && npx tsc --noEmit
```

- [ ] **Step 9: Commit**

```bash
git add frontend/src/screens/SettingsScreen.tsx
git commit -m "feat(settings): wire RemapModal into refresh/rescan/test-connection handlers"
```

---

## Task 10: `LaminusStatusChip.tsx` — polling health chip

**Files:**
- Create: `frontend/src/components/LaminusStatusChip.tsx`

### What this does

Polls `GET /api/v1/laminus/catalog/status` every 60 s. Renders a colored dot + "Laminus" label + status text. Hidden when `laminus_configured: false`. Click shows a detail block with machine/process/filament counts and last-fetched time.

- [ ] **Step 1: Create `frontend/src/components/LaminusStatusChip.tsx`**

```tsx
// frontend/src/components/LaminusStatusChip.tsx
import React, { useEffect, useState } from 'react';
import type { OrcaCatalogStatus } from '../api/orca';

const DOT_COLORS: Record<string, string> = {
  online: 'var(--ok, #22c55e)',
  building: '#f59e0b',
  offline: 'var(--err, #ef4444)',
  unconfigured: '#6b7280',
};

function relativeTime(ts: number | null): string {
  if (!ts) return 'never';
  const secs = Math.floor(Date.now() / 1000 - ts);
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  return `${Math.floor(secs / 3600)}h ago`;
}

export function LaminusStatusChip() {
  const [data, setData] = useState<OrcaCatalogStatus | null>(null);
  const [expanded, setExpanded] = useState(false);

  const load = () => {
    fetch('/api/v1/laminus/catalog/status')
      .then(r => r.json())
      .then(setData)
      .catch(() => {});
  };

  useEffect(() => {
    load();
    const interval = setInterval(load, 60_000);
    return () => clearInterval(interval);
  }, []);

  if (!data || !data.laminus_configured) return null;

  const status = data.status ?? 'offline';
  const dotColor = DOT_COLORS[status] ?? '#6b7280';

  return (
    <div style={{ padding: '4px 8px', fontSize: 13 }}>
      <button
        onClick={() => setExpanded(e => !e)}
        style={{
          display: 'flex', alignItems: 'center', gap: 6,
          background: 'none', border: 'none', cursor: 'pointer',
          color: 'inherit', padding: 0, width: '100%',
        }}
      >
        <span style={{
          width: 8, height: 8, borderRadius: '50%',
          background: dotColor, flexShrink: 0,
          boxShadow: status === 'online' ? `0 0 4px ${dotColor}` : 'none',
        }} />
        <span>Laminus</span>
        <span style={{ color: 'var(--text-muted, #aaa)', marginLeft: 'auto' }}>{status}</span>
      </button>
      {expanded && (
        <div style={{ marginTop: 6, paddingLeft: 14, fontSize: 12, color: 'var(--text-muted, #aaa)' }}>
          {data.catalog_counts ? (
            <>
              <div>{data.catalog_counts.machine} machines</div>
              <div>{data.catalog_counts.process} processes</div>
              <div>{data.catalog_counts.filament} filaments</div>
            </>
          ) : (
            <div>Catalog not loaded</div>
          )}
          <div style={{ marginTop: 4 }}>Fetched: {relativeTime(data.fetched_at)}</div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: TypeScript check**

```
cd frontend && npx tsc --noEmit
```
Note: `OrcaCatalogStatus` now has `catalog_counts` and `status` fields (added in Task 6's `orca.ts` update). Verify they're present.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/LaminusStatusChip.tsx
git commit -m "feat: add LaminusStatusChip polling component"
```

---

## Task 11: `Sidebar.tsx` — add chip to Account section

**Files:**
- Modify: `frontend/src/components/Sidebar.tsx`

### What this does

Add `<LaminusStatusChip />` below the Settings `NavLink` in the Account `nav-section`. No props needed — chip fetches its own data.

- [ ] **Step 1: Read `Sidebar.tsx` to find the Account nav-section**

The Account section is around lines 88-95 (from prior session). Find the Settings NavLink.

- [ ] **Step 2: Add the import and chip**

At the top of `Sidebar.tsx`:
```tsx
import { LaminusStatusChip } from './LaminusStatusChip';
```

In the Account section JSX, immediately after the Settings `<NavLink>`:
```tsx
<LaminusStatusChip />
```

- [ ] **Step 3: TypeScript check and visual verification**

```
cd frontend && npx tsc --noEmit
```

Start the dev server (or use the running stack) and verify the chip appears in the sidebar when `LAMINUS_SIDECAR_URL` is configured.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Sidebar.tsx
git commit -m "feat(sidebar): add LaminusStatusChip to Account section"
```

---

## Final Step: Run the full test suite

- [ ] **Run all backend tests**

```
cd backend && python -m pytest tests/ -v --timeout=30
```
Expected: All existing tests pass; new tests pass.

- [ ] **Run TypeScript check one final time**

```
cd frontend && npx tsc --noEmit
```

- [ ] **Final commit with any remaining changes, then push**

```bash
git status
# Stage any unstaged files
git push origin main
```

---

## Self-Review Checklist

### Spec Coverage

| Spec Requirement | Task |
|---|---|
| `catalog_name_sets` helper extracted from settings.py | Task 1 |
| `compute_drift` with dedup by stale value | Task 1 |
| `_pending_sync` module-level slot | Task 2 |
| `_fetch_catalog` / `_commit_catalog` split | Task 2 |
| Drift gate in refresh + rescan | Task 3 |
| `catalog/status`: `catalog_counts`, `status`, 30s memo | Task 3 |
| `POST /catalog/confirm-remap` | Task 4 |
| Spoolman sanity check on test-connection | Task 5 |
| `api/laminus.ts` with SyncResponse types | Task 6 |
| Fix `orca.ts` route URLs (`/orca/` → `/laminus/`) | Task 6 |
| `testSpoolmanConnection` returns SyncResponse | Task 7 |
| `RemapModal.tsx` | Task 8 |
| SettingsScreen handler updates | Task 9 |
| `LaminusStatusChip.tsx` | Task 10 |
| `Sidebar.tsx` integration | Task 11 |

### Placeholder Scan

No TBD, TODO, or vague instructions found. Every step has exact code.

### Type Consistency

- `SyncResponse = SyncOk | PendingRemaps` defined in `laminus.ts` (Task 6), used in `spoolman.ts` (Task 7), `SettingsScreen.tsx` (Task 9)
- `PendingRemaps.pending.printers` uses `PrinterPendingEntry[]` (Task 6), rendered in `RemapModal` (Task 8)
- `OrcaCatalogStatus` gets `catalog_counts` and `status` fields (Task 6), consumed in `LaminusStatusChip` (Task 10)
- Backend `_pending_sync` slot structure is consistent across laminus.py (Task 2-4) and settings.py (Task 5)
- `confirmRemap(syncId, resolutions)` signature in `laminus.ts` matches `RemapModal` `onConfirm` prop and `SettingsScreen` usage

### Known Pre-existing Bug Fixed

`orca.ts` route URLs fixed from `/api/v1/orca/catalog/*` to `/api/v1/laminus/catalog/*` in Task 6. `OrcaCatalogStatus.orca` field renamed to `laminus`. `refreshOrcaCatalog` / `rescanOrcaCatalog` removed from `orca.ts` — callers in `SettingsScreen.tsx` updated to use `refreshCatalog` / `rescanCatalog` from `laminus.ts`.

### Implementation Notes

- `get_session` import in `laminus.py` (Task 3): check the existing import path used by other routes in the same app. Look at `backend/app/api/deps.py` or examine how `settings.py` imports its session dependency.
- `SpoolmanConfig` import in `laminus.py` (Task 3): `from ...models import SpoolmanConfig`
- The `showToast` function in `SettingsScreen.tsx` may have a different name — read the file to confirm before using it.
- `fetchFilaments` in `catalog_utils.py` must be imported from `app.services.spoolman_service` — verify the exact function signature matches.
