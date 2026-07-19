# Spoolman Per-Slot Spool-Use Deduction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current single-spool total deduction with per-extruder slot deduction using `actual_filament_breakdown`, store deduction results in a new `spoolman_deductions` field, and surface them in the job detail screen.

**Architecture:** The existing `_deduct_spool` helper and `record_spool_use` service function are reused. The deduction logic in `queue_engine.py` is rewritten to iterate `actual_filament_breakdown`, match each extruder index to the corresponding loaded slot (by `slot` field), and fire one deduction task per slot that has a `spoolman_spool_id`. Results are written to a new `spoolman_deductions` JSON column on the `Job` model. The job detail screen gains a "Spoolman" card showing which spools were deducted and how much.

**Tech Stack:** Python/FastAPI, SQLAlchemy async ORM, pytest-asyncio, React/TypeScript

---

## Context

The existing deduction path (queue_engine.py around line 1045–1097):
- Picks ONE slot via `_slot_for_config(config, loaded)` — the slot that matches the job's printer config (by filament profile)
- Deducts `actual_filament_grams` (total) from that one slot's `spoolman_spool_id`
- Works for single-spool printers; breaks for AMS jobs where each extruder used a different spool

After this plan:
- For single-slot jobs: same behavior (total grams from the one matched slot)
- For multi-slot / AMS jobs (`config.filament_map` is set): iterate `actual_filament_breakdown`, match `extruder_index` → `loaded_filaments[slot]`, deduct each slot's own grams
- Store deduction results as `[{spool_id, grams, slot}]` in `job.spoolman_deductions`

**Loaded filaments slot format** (from `Printer.loaded_filaments` JSON):
```json
[{"slot": 0, "spoolman_spool_id": "42", "filament_profile": "...", ...}]
```
The `slot` field is the 0-based tool/extruder index.

**`actual_filament_breakdown` format** (from `Job.actual_filament_breakdown` JSON):
```json
[{"extruder_index": 0, "filament_profile": "Bambu PLA Basic", "grams": 12.3}, ...]
```

---

## Pre-requisite: Understand the existing codebase

Read these files before implementing:

- `backend/app/services/queue_engine.py` lines 82–97 (`_deduct_spool`), 1035–1097 (completion + deduction block)
- `backend/app/services/spoolman_service.py` — `record_spool_use`
- `backend/app/models.py` — `Job`, `JobPrinterConfig`, `Printer`
- `backend/app/migrations/v008_job_estimates_and_queue_config.py` — migration pattern
- `backend/app/database.py` — how migrations are registered
- `backend/app/api/routes/jobs.py` — `_to_dict`, `list_jobs`, `get_job`
- `frontend/src/api/queue.ts` — `ApiJob` interface
- `frontend/src/screens/JobDetailScreen.tsx` — existing "Actual" card section

---

## File Map

| Action | File |
|--------|------|
| Create | `backend/app/migrations/v009_spoolman_deductions.py` |
| Modify | `backend/app/models.py` |
| Modify | `backend/app/database.py` (register migration) |
| Modify | `backend/app/services/queue_engine.py` |
| Modify | `backend/app/api/routes/jobs.py` |
| Modify | `backend/tests/services/test_queue_engine.py` (or create) |
| Modify | `frontend/src/api/queue.ts` |
| Modify | `frontend/src/screens/JobDetailScreen.tsx` |

---

## Task 1: Migration — add `spoolman_deductions` column

**Files:**
- Create: `backend/app/migrations/v009_spoolman_deductions.py`
- Modify: `backend/app/database.py`
- Modify: `backend/app/models.py`

- [ ] **Step 1: Write failing test**

In `backend/tests/test_migrations.py` (or a new file `backend/tests/test_v009_migration.py`):

```python
from sqlalchemy import inspect, text

async def test_v009_adds_spoolman_deductions_column(engine):
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT name FROM pragma_table_info('jobs') WHERE name='spoolman_deductions'"))
        row = result.fetchone()
    assert row is not None, "spoolman_deductions column should exist after migration"
```

Run to confirm it fails:
```
cd backend && pytest tests/test_v009_migration.py -v
```

- [ ] **Step 2: Create migration file**

Create `backend/app/migrations/v009_spoolman_deductions.py`:

```python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def run(conn: AsyncConnection) -> None:
    result = await conn.execute(
        text("SELECT name FROM pragma_table_info('jobs') WHERE name='spoolman_deductions'")
    )
    if result.fetchone() is None:
        await conn.execute(
            text("ALTER TABLE jobs ADD COLUMN spoolman_deductions JSON")
        )
```

- [ ] **Step 3: Register the migration**

Open `backend/app/database.py` and find the migration list (search for `v008`). Add v009 in order:

```python
from .migrations import (
    ...,
    v008_job_estimates_and_queue_config,
    v009_spoolman_deductions,            # NEW
)

_MIGRATIONS = [
    ...,
    v008_job_estimates_and_queue_config.run,
    v009_spoolman_deductions.run,        # NEW
]
```

- [ ] **Step 4: Add model column**

In `backend/app/models.py`, in the `Job` class after `deduction_skipped`:

```python
spoolman_deductions: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
```

- [ ] **Step 5: Run migration test**

```
cd backend && pytest tests/test_v009_migration.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/migrations/v009_spoolman_deductions.py backend/app/database.py backend/app/models.py
git commit -m "feat(db): add spoolman_deductions JSON column to jobs"
```

---

## Task 2: Rewrite per-slot deduction logic in queue_engine.py

**Files:**
- Modify: `backend/app/services/queue_engine.py`
- Modify: `backend/tests/services/test_queue_engine.py` (or create)

The deduction block (lines 1045–1097) needs to support multi-slot AMS jobs. The key change: instead of finding ONE slot and deducting total grams, build a list of `(spool_id, grams)` pairs by cross-referencing `actual_filament_breakdown` against `loaded_filaments` slot indices.

- [ ] **Step 1: Write failing tests**

Create or open `backend/tests/services/test_queue_engine_deduction.py`:

```python
"""Tests for the _collect_deductions helper extracted from the deduction block."""
from app.services.queue_engine import _collect_deductions


def test_single_slot_deducts_total_grams():
    loaded = [{"slot": 0, "spoolman_spool_id": "7", "filament_profile": "PLA Basic"}]
    breakdown = None  # no per-extruder breakdown → use total
    total_grams = 15.0
    result = _collect_deductions(loaded, breakdown, total_grams, filament_map=None)
    assert result == [{"spool_id": 7, "grams": 15.0, "slot": 0}]


def test_ams_job_deducts_per_extruder():
    loaded = [
        {"slot": 0, "spoolman_spool_id": "10"},
        {"slot": 1, "spoolman_spool_id": "11"},
    ]
    breakdown = [
        {"extruder_index": 0, "filament_profile": "PLA Red", "grams": 8.0},
        {"extruder_index": 1, "filament_profile": "PLA Blue", "grams": 5.5},
    ]
    result = _collect_deductions(loaded, breakdown, total_grams=13.5, filament_map=[{}])
    assert result == [
        {"spool_id": 10, "grams": 8.0, "slot": 0},
        {"spool_id": 11, "grams": 5.5, "slot": 1},
    ]


def test_slot_without_spool_id_is_skipped():
    loaded = [
        {"slot": 0, "spoolman_spool_id": "5"},
        {"slot": 1},  # no spool_id → skip
    ]
    breakdown = [
        {"extruder_index": 0, "grams": 9.0},
        {"extruder_index": 1, "grams": 4.0},
    ]
    result = _collect_deductions(loaded, breakdown, total_grams=13.0, filament_map=[{}])
    assert result == [{"spool_id": 5, "grams": 9.0, "slot": 0}]


def test_no_loaded_filaments_returns_empty():
    result = _collect_deductions([], None, 12.0, filament_map=None)
    assert result == []
```

Run to confirm they fail:
```
cd backend && pytest tests/services/test_queue_engine_deduction.py -v
```

Expected: ImportError or AttributeError (`_collect_deductions` doesn't exist yet).

- [ ] **Step 2: Extract `_collect_deductions` helper**

At the module level in `backend/app/services/queue_engine.py`, add this pure function (near the other module-level helpers like `_slot_for_config`, `_deduct_spool`):

```python
def _collect_deductions(
    loaded: list[dict],
    breakdown: list[dict] | None,
    total_grams: float,
    filament_map: list | None,
) -> list[dict]:
    """Build per-spool deduction entries from loaded slots and actual filament usage.

    For AMS jobs (filament_map is not None/empty) with a per-extruder breakdown,
    deducts per-slot grams. Otherwise deducts the total from the first slot with a spool ID.
    Returns [{"spool_id": int, "grams": float, "slot": int}].
    """
    slot_map = {s["slot"]: s for s in loaded if "slot" in s}
    results: list[dict] = []

    if filament_map and breakdown:
        for entry in breakdown:
            idx = entry.get("extruder_index")
            grams = entry.get("grams", 0.0)
            slot = slot_map.get(idx)
            if slot is None:
                continue
            raw = slot.get("spoolman_spool_id")
            if raw is None:
                continue
            try:
                results.append({"spool_id": int(raw), "grams": grams, "slot": idx})
            except (TypeError, ValueError):
                logger.warning("Invalid spoolman_spool_id %r in slot %s", raw, idx)
    else:
        # Single-spool: first slot with a spool_id, total grams
        for slot in loaded:
            raw = slot.get("spoolman_spool_id")
            if raw is None:
                continue
            try:
                results.append({"spool_id": int(raw), "grams": total_grams, "slot": slot.get("slot", 0)})
                break
            except (TypeError, ValueError):
                logger.warning("Invalid spoolman_spool_id %r", raw)
                break

    return results
```

- [ ] **Step 3: Rewrite the deduction block in the completion handler**

Find the block in queue_engine.py starting with:
```python
# Collect Spoolman deduction data before session closes
```
(around line 1045)

Replace the entire block (from `actual_grams = job.actual_filament_grams` through the `spool_id` variable setup) with:

```python
# Collect Spoolman deduction data before session closes
deduction_list: list[dict] = []
actual_grams = job.actual_filament_grams
if actual_grams is not None:
    spoolman_cfg = await session.get(SpoolmanConfig, 1)
    if spoolman_cfg and spoolman_cfg.enabled and spoolman_cfg.url:
        spoolman_url = spoolman_cfg.url
        spoolman_key = spoolman_cfg.api_key
        printer = await session.get(Printer, printer_id)
        loaded = (printer.loaded_filaments if printer else None) or []
        cfg_result = await session.execute(
            select(JobPrinterConfig).where(
                JobPrinterConfig.job_id == job_id,
                JobPrinterConfig.printer_id == printer_id,
            )
        )
        config = cfg_result.scalar_one_or_none()
        if config is not None:
            breakdown = job.actual_filament_breakdown
            deduction_list = _collect_deductions(
                loaded, breakdown, actual_grams, config.filament_map
            )
            if deduction_list:
                job.deduction_skipped = False
                job.spoolman_deductions = deduction_list
```

Then replace the fire-and-forget task creation (after `await session.commit()`):

```python
# Before:
if spool_id is not None and spoolman_url and grams_to_deduct is not None:
    task = asyncio.create_task(...)

# After:
for entry in deduction_list:
    task = asyncio.create_task(
        _deduct_spool(spoolman_url, spoolman_key, entry["spool_id"], entry["grams"])
    )
    self._estimate_tasks.add(task)
    task.add_done_callback(self._estimate_tasks.discard)
```

Remove the now-unused `spool_id`, `grams_to_deduct`, and `spoolman_url`/`spoolman_key` local variables that were scoped outside the `async with` (they're replaced by the `deduction_list` list).

- [ ] **Step 4: Run tests**

```
cd backend && pytest tests/services/test_queue_engine_deduction.py -v
```

Expected: all 4 tests pass.

Also run the full backend test suite to check no regressions:
```
cd backend && pytest -x -q
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/queue_engine.py backend/tests/services/test_queue_engine_deduction.py
git commit -m "feat(engine): per-slot Spoolman deduction using actual_filament_breakdown"
```

---

## Task 3: Expose `spoolman_deductions` in the API

**Files:**
- Modify: `backend/app/api/routes/jobs.py`
- Modify: `frontend/src/api/queue.ts`

- [ ] **Step 1: Write failing test**

In `backend/tests/api/test_jobs_routes.py` (or the appropriate jobs test file), add:

```python
async def test_job_dict_includes_spoolman_deductions(client: AsyncClient, session: AsyncSession):
    """GET /api/v1/jobs/{id} includes spoolman_deductions field."""
    from app.models import Job, UploadedFile
    f = UploadedFile(original_filename="x.3mf", stored_path="/tmp/x.3mf",
                     plates=[], uploaded_at="2026-01-01T00:00:00")
    session.add(f)
    await session.flush()
    j = Job(uploaded_file_id=f.id, plate_number=1, status="complete",
            spoolman_deductions=[{"spool_id": 5, "grams": 9.1, "slot": 0}],
            created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00")
    session.add(j)
    await session.commit()
    await session.refresh(j)

    resp = await client.get(f"/api/v1/jobs/{j.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert "spoolman_deductions" in body
    assert body["spoolman_deductions"] == [{"spool_id": 5, "grams": 9.1, "slot": 0}]
```

Run to verify it fails:
```
cd backend && pytest tests/api/test_jobs_routes.py::test_job_dict_includes_spoolman_deductions -v
```

- [ ] **Step 2: Add to `_to_dict`**

In `backend/app/api/routes/jobs.py`, in the `_to_dict` function, add after `"deduction_skipped"`:

```python
"spoolman_deductions": j.spoolman_deductions,
```

- [ ] **Step 3: Run test**

```
cd backend && pytest tests/api/test_jobs_routes.py::test_job_dict_includes_spoolman_deductions -v
```

Expected: PASS.

- [ ] **Step 4: Update TypeScript interface**

In `frontend/src/api/queue.ts`, in `ApiJob`:

```typescript
// After: deduction_skipped: boolean | null;
spoolman_deductions: Array<{ spool_id: number; grams: number; slot: number }> | null;
```

Run TypeScript check:
```
cd frontend && npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/jobs.py backend/tests/api/test_jobs_routes.py frontend/src/api/queue.ts
git commit -m "feat(api): expose spoolman_deductions in job response"
```

---

## Task 4: Job detail screen — show deduction results

**Files:**
- Modify: `frontend/src/screens/JobDetailScreen.tsx`

After the "Actual" card (which shows total filament grams), add a "Spoolman" card when deductions were recorded, or a warning when deduction was skipped.

- [ ] **Step 1: Implement**

In `JobDetailScreen.tsx`, after the "Actual" card block (around line 337 `job.status === 'complete'`), add:

```tsx
{/* Spoolman deduction result */}
{(job.spoolman_deductions != null || job.deduction_skipped) && (
  <div className="card" style={{ padding: 20 }}>
    <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 14 }}>Spoolman</div>
    {job.deduction_skipped && (
      <div style={{ color: 'var(--warn)', fontSize: 13, marginBottom: 8 }}>
        Print was aborted — no spool weight was deducted. Update Spoolman manually.
      </div>
    )}
    {job.spoolman_deductions && job.spoolman_deductions.length > 0 && (
      <div className="col gap-2">
        {job.spoolman_deductions.map((d, i) => (
          <div key={i} className="row between">
            <span className="small muted">Spool #{d.spool_id} (T{d.slot})</span>
            <span className="num small">−{d.grams.toFixed(1)} g</span>
          </div>
        ))}
      </div>
    )}
    {job.spoolman_deductions && job.spoolman_deductions.length === 0 && !job.deduction_skipped && (
      <div className="small muted">
        No spools were linked to this printer's slots — weight not tracked.
      </div>
    )}
  </div>
)}
```

- [ ] **Step 2: Run TypeScript check**

```
cd frontend && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Also update ApiJobDetails type if separate**

Check `frontend/src/api/queue.ts` for `ApiJobDetails` interface (used by `getJobDetails`). If it's separate from `ApiJob`, add `spoolman_deductions` there too:

```typescript
spoolman_deductions: Array<{ spool_id: number; grams: number; slot: number }> | null;
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/screens/JobDetailScreen.tsx frontend/src/api/queue.ts
git commit -m "feat(ui): show Spoolman deduction results in job detail screen"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] Per-extruder deduction using `actual_filament_breakdown` (Task 2)
- [x] Single-spool fallback preserved (Task 2, `_collect_deductions` single-slot branch)
- [x] Deduction results stored in `spoolman_deductions` (Tasks 1–2)
- [x] Results exposed in API (Task 3)
- [x] Results shown in job detail UI (Task 4)
- [x] `deduction_skipped` warning shown in job detail (Task 4, already rendered, now also shows when `spoolman_deductions` is empty)

**Type consistency:**
- `_collect_deductions` returns `list[dict]` with `spool_id: int`, `grams: float`, `slot: int` — matches `ApiJob.spoolman_deductions` TypeScript type
- `Job.spoolman_deductions` is `Mapped[Optional[list]]` (JSON) — same structure stored in DB

**Migration safety:**
- v009 uses `IF NOT EXISTS` style check before ALTER — idempotent on re-run
- Column is nullable, so existing jobs without deductions are unaffected

**Backwards compatibility:**
- Jobs completed before this change have `spoolman_deductions = null`, `deduction_skipped = null`
- The job detail card only renders when either field is non-null, so no UI regression for old jobs
