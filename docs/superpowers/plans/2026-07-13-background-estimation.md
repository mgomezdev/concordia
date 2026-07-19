# Background Estimation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When enabled, immediately run a test slice after job creation to project print time and filament usage; persist estimates on the Job row; roll up to projects.

**Architecture:** A Themis-side `asyncio.PriorityQueue` serialises all Laminus slice calls. Background estimates run at priority 1 (production at 0). A new `run_estimate` method slices, parses, discards the gcode, and writes results using a conditional UPDATE that guards against cancellation and retrigger races. Production slices capture actual grams on `Job` before `GcodeFile` is deleted so Spoolman deduction and history lookups work after completion.

**Tech Stack:** FastAPI, SQLAlchemy async, SQLite, asyncio, pytest-asyncio, React/TypeScript

**Spec reference:** `docs/superpowers/specs/2026-07-13-background-estimation-design.md`

---

## File structure

| File | What changes |
|---|---|
| `themis/backend/app/migrations/v008_job_estimates_and_queue_config.py` | **New** migration |
| `themis/backend/app/migrations/runner.py` | Register v008 |
| `themis/backend/app/models.py` | +10 Job cols, +1 QueueConfig col |
| `themis/backend/app/services/spoolman_service.py` | Add `record_spool_use` |
| `themis/backend/app/services/slicer_service.py` | Add `output_dir` param to `slice()` |
| `themis/backend/app/services/queue_engine.py` | Priority queue, estimate flow, actual capture, Spoolman deduction |
| `themis/backend/app/api/routes/jobs.py` | `_to_dict`, `create_job`, `cancel_job`, `update_job_configs`, `get_job_details` |
| `themis/backend/app/api/routes/projects.py` | `_project_dict` rollup, `generate_project` trigger |
| `themis/backend/app/api/routes/settings.py` | `estimates_enabled` |
| `themis/frontend/src/api/queue.ts` | New fields on `ApiJob` / `ApiJobDetails` |
| `themis/frontend/src/api/projects.ts` | Replace old keys with 6 new rollup keys |
| `themis/frontend/src/api/settings.ts` | `estimates_enabled: boolean` |
| `themis/frontend/src/screens/EditJobScreen.test.tsx` | Update fixture |
| `themis/frontend/src/screens/SettingsScreen.tsx` | Estimate toggle |
| `themis/frontend/src/screens/JobDetailScreen.tsx` | Estimate/actual sections |
| `themis/frontend/src/screens/HistoryScreen.tsx` | Estimate/actual columns |
| `themis/frontend/src/screens/ProjectDetailScreen.tsx` | Rollup rows |

---

## Task 1: Migration v008 + register in runner

**Files:**
- Create: `themis/backend/app/migrations/v008_job_estimates_and_queue_config.py`
- Modify: `themis/backend/app/migrations/runner.py`
- Test: `themis/backend/tests/test_migrations.py`

- [ ] **Step 1: Write the failing test** (add to `test_migrations.py`)

```python
@pytest.mark.asyncio
async def test_v008_adds_estimate_columns():
    from app.migrations.runner import run_migrations
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await run_migrations(conn)
        await run_migrations(conn)  # idempotent second run
        job_cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(jobs)"))).fetchall()}
        qc_cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(queue_config)"))).fetchall()}
    expected_job = {
        "actual_filament_grams", "actual_seconds", "actual_filament_breakdown",
        "deduction_skipped", "estimate_token", "estimate_status", "estimate_seconds",
        "estimate_filament_grams", "estimate_filament_breakdown", "estimate_preset_label",
    }
    assert expected_job <= job_cols
    assert "estimates_enabled" in qc_cols
    await engine.dispose()
```

- [ ] **Step 2: Run to confirm failure**

```
cd themis/backend && pytest tests/test_migrations.py::test_v008_adds_estimate_columns -v
```
Expected: ImportError or AttributeError (module `v008` doesn't exist yet)

- [ ] **Step 3: Create migration file**

```python
# themis/backend/app/migrations/v008_job_estimates_and_queue_config.py
"""Add estimate/actual columns to jobs and estimates_enabled to queue_config."""
from __future__ import annotations
from sqlalchemy import text

version = 8
name = "job_estimates_and_queue_config"


async def up(conn) -> None:
    job_cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(jobs)"))).fetchall()}
    qc_cols  = {r[1] for r in (await conn.execute(text("PRAGMA table_info(queue_config)"))).fetchall()}

    # Actual values (captured at production slice time, before GcodeFile is deleted)
    if "actual_filament_grams" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN actual_filament_grams REAL"))
    if "actual_seconds" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN actual_seconds INTEGER"))
    if "actual_filament_breakdown" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN actual_filament_breakdown JSON"))
    if "deduction_skipped" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN deduction_skipped BOOLEAN"))

    # Estimate values (from background test slice)
    if "estimate_token" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN estimate_token INTEGER NOT NULL DEFAULT 0"))
    if "estimate_status" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN estimate_status TEXT"))
    if "estimate_seconds" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN estimate_seconds INTEGER"))
    if "estimate_filament_grams" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN estimate_filament_grams REAL"))
    if "estimate_filament_breakdown" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN estimate_filament_breakdown JSON"))
    if "estimate_preset_label" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN estimate_preset_label JSON"))

    # QueueConfig extension
    if "estimates_enabled" not in qc_cols:
        await conn.execute(text(
            "ALTER TABLE queue_config ADD COLUMN estimates_enabled BOOLEAN NOT NULL DEFAULT 0"
        ))


async def down(conn) -> None:
    # SQLite <3.35 cannot DROP COLUMN; recreate jobs without new columns.
    # Not intended for production rollback — only satisfies rollback_last().
    await conn.execute(text("""
        CREATE TABLE jobs_new AS
        SELECT id, uploaded_file_id, plate_number, order_id, assigned_printer_id,
               queue_position, status, project_id, block_reason, overrides,
               created_at, updated_at, completed_at, outcome, project_item_quantities
        FROM jobs
    """))
    await conn.execute(text("DROP TABLE jobs"))
    await conn.execute(text("ALTER TABLE jobs_new RENAME TO jobs"))
```

- [ ] **Step 4: Register in runner.py**

In `runner.py`, add `v008_job_estimates_and_queue_config` to the import line and `_MIGRATIONS` list:

```python
from . import v001_initial, v002_project_order_link, v003_webhook_config, v004_gcode_estimates, v005_project_order_merge, v006_project_links, v007_printer_bed_size, v008_job_estimates_and_queue_config

_MIGRATIONS = sorted(
    [v001_initial, v002_project_order_link, v003_webhook_config, v004_gcode_estimates,
     v005_project_order_merge, v006_project_links, v007_printer_bed_size,
     v008_job_estimates_and_queue_config],
    key=lambda m: m.version,
)
```

- [ ] **Step 5: Run test to confirm pass**

```
cd themis/backend && pytest tests/test_migrations.py::test_v008_adds_estimate_columns -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd themis
git add backend/app/migrations/v008_job_estimates_and_queue_config.py backend/app/migrations/runner.py backend/tests/test_migrations.py
git commit -m "feat: add migration v008 for estimate/actual columns and estimates_enabled"
```

---

## Task 2: Model field additions

**Files:**
- Modify: `themis/backend/app/models.py`

- [ ] **Step 1: Add fields to `Job` in `models.py`**

In the `Job` class, add after `outcome`:

```python
# --- Actual values (set at production slice time, before GcodeFile deleted) ---
actual_filament_grams: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
actual_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
actual_filament_breakdown: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
deduction_skipped: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

# --- Estimate values (set after background test slice) ---
estimate_token: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
estimate_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
estimate_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
estimate_filament_grams: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
estimate_filament_breakdown: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
estimate_preset_label: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
```

Add to `QueueConfig` class after `snapshot_interval_seconds`:

```python
estimates_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
```

- [ ] **Step 2: Verify model loads**

```
cd themis/backend && python -c "from app.models import Job, QueueConfig; j = Job.__table__.columns.keys(); print([c for c in j if 'estimate' in c or 'actual' in c])"
```
Expected: `['actual_filament_grams', 'actual_seconds', 'actual_filament_breakdown', 'deduction_skipped', 'estimate_token', 'estimate_status', 'estimate_seconds', 'estimate_filament_grams', 'estimate_filament_breakdown', 'estimate_preset_label']`

- [ ] **Step 3: Commit**

```bash
git add backend/app/models.py
git commit -m "feat: add estimate/actual fields to Job model and estimates_enabled to QueueConfig"
```

---

## Task 3: `spoolman_service.record_spool_use`

**Files:**
- Modify: `themis/backend/app/services/spoolman_service.py`
- Test: `themis/backend/tests/services/test_spoolman_service.py`

- [ ] **Step 1: Write the failing test** (add to `test_spoolman_service.py`)

```python
@pytest.mark.asyncio
async def test_record_spool_use_calls_correct_endpoint():
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock
    import httpx
    from app.services.spoolman_service import record_spool_use

    mock_instance = AsyncMock()
    mock_instance.put = AsyncMock(return_value=httpx.Response(200, json={}))

    @asynccontextmanager
    async def _ctx(*args, **kwargs):
        yield mock_instance

    with patch("httpx.AsyncClient", _ctx):
        await record_spool_use("http://spoolman.test", "key123", spool_id=42, grams=15.5)

    mock_instance.put.assert_called_once()
    call_args = mock_instance.put.call_args
    assert "/api/v1/spool/42/use" in call_args[0][0]
    assert call_args[1]["json"] == {"use_weight": 15.5}
    assert call_args[1]["headers"]["X-API-Key"] == "key123"


@pytest.mark.asyncio
async def test_record_spool_use_no_api_key():
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock
    import httpx
    from app.services.spoolman_service import record_spool_use

    mock_instance = AsyncMock()
    mock_instance.put = AsyncMock(return_value=httpx.Response(200, json={}))

    @asynccontextmanager
    async def _ctx(*args, **kwargs):
        yield mock_instance

    with patch("httpx.AsyncClient", _ctx):
        await record_spool_use("http://spoolman.test", None, spool_id=7, grams=5.0)

    call_args = mock_instance.put.call_args
    assert "X-API-Key" not in call_args[1]["headers"]
```

- [ ] **Step 2: Run to confirm failure**

```
cd themis/backend && pytest tests/services/test_spoolman_service.py::test_record_spool_use_calls_correct_endpoint -v
```
Expected: ImportError (`cannot import name 'record_spool_use'`)

- [ ] **Step 3: Add function to `spoolman_service.py`**

Add after the existing `patch_filament` function:

```python
async def record_spool_use(
    url: str, api_key: Optional[str], spool_id: int, grams: float
) -> None:
    """PUT /api/v1/spool/{spool_id}/use — records filament consumption."""
    headers = _headers(api_key)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.put(
            f"{url.rstrip('/')}/api/v1/spool/{spool_id}/use",
            json={"use_weight": grams},
            headers=headers,
        )
        resp.raise_for_status()
```

- [ ] **Step 4: Run tests to confirm pass**

```
cd themis/backend && pytest tests/services/test_spoolman_service.py -v
```
Expected: PASS (all tests, including existing ones)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/spoolman_service.py backend/tests/services/test_spoolman_service.py
git commit -m "feat: add record_spool_use to spoolman_service"
```

---

## Task 4: `slicer_service.slice` output_dir parameter

**Files:**
- Modify: `themis/backend/app/services/slicer_service.py`
- Test: `themis/backend/tests/services/test_slicer_service.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_slice_uses_custom_output_dir(tmp_path):
    """When output_dir is provided, the slice method uses it instead of the default."""
    from pathlib import Path
    from unittest.mock import MagicMock, patch
    from app.services.slicer_service import SlicerService, SliceRequest

    custom_dir = tmp_path / "estimates" / "99"

    svc = SlicerService(data_dir=str(tmp_path))

    req = SliceRequest(
        job_id=99, source_3mf="model.3mf", plate_number=1,
        machine_preset="M", process_preset="P", filament_presets=["F"],
    )

    captured_out_dir = None

    def fake_execute(self_inner, req_inner, machine_uuid, process_uuid, filament_uuids,
                     out_dir, sidecar_url):
        nonlocal captured_out_dir
        captured_out_dir = out_dir
        return str(out_dir / "result.gcode")

    with patch.object(SlicerService, "_resolve_uuids", return_value=("m", "p", ["f"])), \
         patch.object(SlicerService, "_execute_slice_by_ids", fake_execute):
        svc.slice(req, output_dir=custom_dir)

    assert captured_out_dir == custom_dir
    assert custom_dir.exists()
```

- [ ] **Step 2: Run to confirm failure**

```
cd themis/backend && pytest tests/services/test_slicer_service.py::test_slice_uses_custom_output_dir -v
```
Expected: TypeError (unexpected keyword argument `output_dir`)

- [ ] **Step 3: Update `slice()` signature and body in `slicer_service.py`**

Change:
```python
def slice(self, req: SliceRequest) -> str:
```
to:
```python
def slice(self, req: SliceRequest, output_dir: "Path | None" = None) -> str:
```

Change the `out_dir` assignment (line 71):
```python
out_dir = self._data_dir / "gcode" / str(req.job_id)
```
to:
```python
out_dir = output_dir if output_dir is not None else (self._data_dir / "gcode" / str(req.job_id))
```

Also update the `_execute_slice_by_ids` call to pass `out_dir` (it already receives `out_dir` as a parameter, just confirm line 82–84 passes the local variable).

- [ ] **Step 4: Run tests to confirm pass**

```
cd themis/backend && pytest tests/services/test_slicer_service.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/slicer_service.py backend/tests/services/test_slicer_service.py
git commit -m "feat: add output_dir param to slicer_service.slice for estimate gcode isolation"
```

---

## Task 5: `_parse_gcode_estimates` → 3-tuple

**Files:**
- Modify: `themis/backend/app/services/queue_engine.py`
- Test: `themis/backend/tests/services/test_queue_engine.py`

- [ ] **Step 1: Write the failing tests** (add to `test_queue_engine.py`)

```python
def test_parse_gcode_estimates_single_extruder(tmp_path):
    from app.services.queue_engine import _parse_gcode_estimates
    gcode = tmp_path / "test.gcode"
    gcode.write_text(
        "; filament used [g] = 12.50\n"
        "; estimated printing time (normal mode) = 1h 30m 45s\n"
    )
    grams, secs, extruder_grams = _parse_gcode_estimates(str(gcode))
    assert grams == pytest.approx(12.50)
    assert secs == 1 * 3600 + 30 * 60 + 45
    assert extruder_grams == [pytest.approx(12.50)]


def test_parse_gcode_estimates_multi_extruder(tmp_path):
    from app.services.queue_engine import _parse_gcode_estimates
    gcode = tmp_path / "test.gcode"
    gcode.write_text(
        "; filament used [g] = 15.23, 8.45\n"
        "; estimated printing time (normal mode) = 2h 0m 0s\n"
    )
    grams, secs, extruder_grams = _parse_gcode_estimates(str(gcode))
    assert grams == pytest.approx(23.68)
    assert secs == 7200
    assert extruder_grams == [pytest.approx(15.23), pytest.approx(8.45)]


def test_parse_gcode_estimates_missing_returns_none(tmp_path):
    from app.services.queue_engine import _parse_gcode_estimates
    gcode = tmp_path / "test.gcode"
    gcode.write_text("; no filament info here\n")
    grams, secs, extruder_grams = _parse_gcode_estimates(str(gcode))
    assert grams is None
    assert secs is None
    assert extruder_grams is None
```

- [ ] **Step 2: Run to confirm failure**

```
cd themis/backend && pytest tests/services/test_queue_engine.py::test_parse_gcode_estimates_single_extruder -v
```
Expected: `ValueError: too many values to unpack` (function returns 2-tuple, test expects 3)

- [ ] **Step 3: Update `_parse_gcode_estimates` in `queue_engine.py`**

Replace the existing function (lines 22–60) with:

```python
def _parse_gcode_estimates(path: str) -> tuple[float | None, int | None, list[float] | None]:
    """Extract filament_grams (total), estimated_seconds, per-extruder grams from gcode.

    Returns (total_grams, seconds, extruder_grams_list). extruder_grams_list has one
    entry per comma-separated value in the 'filament used [g]' line. Returns None for
    each field independently if parsing fails.
    """
    try:
        if path.endswith(".3mf"):
            with zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if n.endswith(".gcode")]
                if not names:
                    return None, None, None
                text = z.read(names[0]).decode("utf-8", errors="replace")[:16000]
        else:
            with open(path, "r", errors="replace") as f:
                text = f.read(16000)
    except Exception:
        return None, None, None

    grams: float | None = None
    extruder_grams: list[float] | None = None
    seconds: int | None = None
    for raw in text.splitlines():
        line = raw.lstrip("; ").strip()
        if "filament used [g]" in line.lower():
            raw_val = line.split("=")[-1].strip()
            parts = [p.strip() for p in raw_val.split(",")]
            try:
                extruder_grams = [float(p) for p in parts if p]
                grams = sum(extruder_grams)
            except ValueError:
                extruder_grams = None
                grams = None
        if "estimated printing time" in line.lower():
            time_str = re.split(r"\s*\(", line.split("=")[-1].strip())[0].strip()
            total = 0
            for num, unit in re.findall(r"(\d+)([hms])", time_str):
                if unit == "h":
                    total += int(num) * 3600
                elif unit == "m":
                    total += int(num) * 60
                else:
                    total += int(num)
            if total > 0:
                seconds = total
        if grams is not None and seconds is not None:
            break
    return grams, seconds, extruder_grams
```

- [ ] **Step 4: Fix the only existing caller** — find the 2-tuple unpack in `_run_slice_and_print` (around line 546) and update:

```python
# Before:
grams, secs = _parse_gcode_estimates(gcode_path)

# After:
grams, secs, extruder_grams = _parse_gcode_estimates(gcode_path)
```

Leave `extruder_grams` in scope; it will be used in the next task when we capture actuals.

- [ ] **Step 5: Run tests to confirm pass**

```
cd themis/backend && pytest tests/services/test_queue_engine.py -k "parse_gcode" -v
```
Expected: PASS (3 new tests)

- [ ] **Step 6: Run full backend tests to check for regressions**

```
cd themis/backend && pytest tests/ -v --tb=short
```
Expected: PASS on all previously passing tests

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/queue_engine.py backend/tests/services/test_queue_engine.py
git commit -m "feat: extend _parse_gcode_estimates to return per-extruder breakdown 3-tuple"
```

---

## Task 6: QueueEngine priority queue infrastructure + production slice migration

**Files:**
- Modify: `themis/backend/app/services/queue_engine.py`
- Test: `themis/backend/tests/services/test_queue_engine.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_priority_queue_orders_production_before_estimate(db):
    """Production slices (priority 0) are dequeued before estimate slices (priority 1)."""
    import itertools
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    mgr = _make_mock_printer_manager([])
    slicer = MagicMock(spec=SlicerService)

    engine = QueueEngine(db, mgr, slicer)
    seq = itertools.count()

    results = []

    async def coro(label):
        results.append(label)

    # Put estimate first, then production
    await engine._slice_queue.put((1, next(seq), coro("estimate")))
    await engine._slice_queue.put((0, next(seq), coro("production")))

    # Drain both
    for _ in range(2):
        _, _s, c = await engine._slice_queue.get()
        await c
        engine._slice_queue.task_done()

    assert results == ["production", "estimate"]


@pytest.mark.asyncio
async def test_equal_priority_no_type_error(db):
    """Two equal-priority items with seq tiebreaker don't raise TypeError."""
    import itertools
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    mgr = _make_mock_printer_manager([])
    slicer = MagicMock(spec=SlicerService)
    engine = QueueEngine(db, mgr, slicer)
    seq = itertools.count()

    async def noop():
        pass

    await engine._slice_queue.put((1, next(seq), noop()))
    await engine._slice_queue.put((1, next(seq), noop()))
    # Should not raise — drain without error
    for _ in range(2):
        _, _s, c = await engine._slice_queue.get()
        await c
        engine._slice_queue.task_done()
```

- [ ] **Step 2: Run to confirm failure**

```
cd themis/backend && pytest tests/services/test_queue_engine.py::test_priority_queue_orders_production_before_estimate -v
```
Expected: AttributeError (`QueueEngine has no attribute '_slice_queue'`)

- [ ] **Step 3: Add priority queue infrastructure to `QueueEngine.__init__`**

Add `import itertools` at the top of `queue_engine.py` (after existing imports).

In `QueueEngine.__init__`, add after `self._executor = ...`:

```python
self._slice_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
self._slice_seq: itertools.count = itertools.count()
self._slice_worker_task: asyncio.Task | None = None
self._estimate_tasks: set[asyncio.Task] = set()
```

- [ ] **Step 4: Add `_slice_worker` method to `QueueEngine`**

Add after `__init__`:

```python
async def _slice_worker(self) -> None:
    while True:
        priority, _seq, coro = await self._slice_queue.get()
        try:
            await coro
        except Exception:
            logger.exception("Slice worker: unhandled exception in queued coro")
        finally:
            self._slice_queue.task_done()
```

- [ ] **Step 5: Update `start()` to launch `_slice_worker` and add startup estimate sweep**

In `QueueEngine.start()`, at the very beginning (before the existing orphan-reset code), add:

```python
import shutil
estimate_gcode_dir = self._slicer._data_dir / "gcode_estimates"
shutil.rmtree(estimate_gcode_dir, ignore_errors=True)
```

After the existing orphaned-jobs reset blocks (before `self._task = asyncio.create_task(...)`), add:

```python
# Reset any estimate_status='pending' left from a prior unclean shutdown.
async with self._factory() as session:
    await session.execute(
        text("UPDATE jobs SET estimate_status=NULL WHERE estimate_status='pending'")
    )
    await session.commit()

self._slice_worker_task = asyncio.create_task(
    self._slice_worker(), name="slice_worker"
)
```

- [ ] **Step 6: Update `stop()` to cancel worker and estimate tasks**

In `QueueEngine.stop()`, after `self._task.cancel()` block, add:

```python
if self._slice_worker_task:
    self._slice_worker_task.cancel()
    await asyncio.gather(self._slice_worker_task, return_exceptions=True)

for t in list(self._estimate_tasks):
    t.cancel()
if self._estimate_tasks:
    await asyncio.gather(*self._estimate_tasks, return_exceptions=True)
```

- [ ] **Step 7: Migrate production slice to use the priority queue**

In `_run_slice_and_print`, replace the existing slice call:

```python
# Before (around line 532):
loop = asyncio.get_running_loop()
...
try:
    gcode_path: str = await loop.run_in_executor(self._executor, self._slicer.slice, req)
except SliceError as exc:
    await self._handle_slice_failure(job_id, printer_id, str(exc))
    return
except Exception as exc:
    logger.exception(...)
    await self._handle_slice_failure(job_id, printer_id, f"Unexpected error: {exc}")
    return
```

```python
# After:
loop = asyncio.get_running_loop()
...
fut: asyncio.Future = loop.create_future()

async def _do_prod_slice():
    try:
        result = await asyncio.to_thread(self._slicer.slice, req)
        if not fut.done():
            fut.set_result(result)
    except Exception as exc:
        if not fut.done():
            fut.set_exception(exc)

await self._slice_queue.put((0, next(self._slice_seq), _do_prod_slice()))
try:
    gcode_path = await fut
except SliceError as exc:
    await self._handle_slice_failure(job_id, printer_id, str(exc))
    return
except Exception as exc:
    logger.exception("Unexpected slice error for job %s on printer %s", job_id, printer_id)
    await self._handle_slice_failure(job_id, printer_id, f"Unexpected error: {exc}")
    return
```

The `loop` variable remains in scope for `_do_upload_and_print` which still uses `run_in_executor` for upload and start_print (those don't go through the slice queue).

- [ ] **Step 8: Run tests to confirm pass**

```
cd themis/backend && pytest tests/services/test_queue_engine.py -v --tb=short
```
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/queue_engine.py backend/tests/services/test_queue_engine.py
git commit -m "feat: add priority queue infrastructure to QueueEngine, migrate production slice"
```

---

## Task 7: Actual value capture at production slice time

**Files:**
- Modify: `themis/backend/app/services/queue_engine.py`
- Test: `themis/backend/tests/services/test_queue_engine.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_actual_values_captured_at_slice_time(db):
    """After a production slice, actual_filament_grams/actual_seconds/actual_filament_breakdown
    are persisted on the Job row in the same session block as GcodeFile creation."""
    from unittest.mock import patch, MagicMock
    from app.models import Job, QueueConfig
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService, SliceRequest

    printer_id = 1
    job_id = await _seed_job(db, printer_id)

    # Set up a printer with filament profile
    async with db() as session:
        printer = await session.get(Printer, printer_id)
        printer.current_orca_printer_profile = "Test Machine"
        printer.loaded_filaments = [{"filament_profile": "PLA Generic", "type": "PLA", "color": ""}]
        await session.commit()

    # Mock the slice to write a fake gcode file
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False, mode="w") as f:
        f.write("; filament used [g] = 15.50\n; estimated printing time (normal mode) = 1h 0m 0s\n")
        fake_gcode = f.name

    mgr = _make_mock_printer_manager([printer_id])
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path(tempfile.mkdtemp())

    engine = QueueEngine(db, mgr, slicer)

    # Patch the priority queue to run synchronously
    async def fake_put(item):
        _, _seq, coro = item
        await coro

    engine._slice_queue.put = fake_put

    with patch.object(slicer, "slice", return_value=fake_gcode), \
         patch.object(engine, "_do_upload_and_print", new_callable=AsyncMock):
        await engine._run_slice_and_print(job_id, printer_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.actual_filament_grams == pytest.approx(15.50)
        assert job.actual_seconds == 3600
        assert job.actual_filament_breakdown is not None
        assert len(job.actual_filament_breakdown) == 1
        assert job.actual_filament_breakdown[0]["grams"] == pytest.approx(15.50)

    os.unlink(fake_gcode)
```

- [ ] **Step 2: Run to confirm failure**

```
cd themis/backend && pytest tests/services/test_queue_engine.py::test_actual_values_captured_at_slice_time -v
```
Expected: AssertionError (fields are None)

- [ ] **Step 3: Add actual capture in `_run_slice_and_print`**

In the session block after `gcode_path = await fut` (around line 542), find where `GcodeFile` is created and `grams, secs, extruder_grams = _parse_gcode_estimates(gcode_path)`. After creating `gcode_rec` and `session.add(gcode_rec)`, add:

```python
# Persist actuals on Job NOW — before GcodeFile is ever deleted.
job.actual_filament_grams = grams
job.actual_seconds = secs
if extruder_grams is not None:
    job.actual_filament_breakdown = [
        {
            "extruder_index": i,
            "filament_profile": req.filament_presets[i] if i < len(req.filament_presets) else None,
            "grams": g,
        }
        for i, g in enumerate(extruder_grams)
    ]
```

The `req` variable is in scope for `_run_slice_and_print`. The `job` object is loaded in the `async with self._factory() as session` block at line 542.

- [ ] **Step 4: Run tests to confirm pass**

```
cd themis/backend && pytest tests/services/test_queue_engine.py -v --tb=short
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/queue_engine.py backend/tests/services/test_queue_engine.py
git commit -m "feat: capture actual filament grams/seconds on Job at production slice time"
```

---

## Task 8: Background estimation flow

**Files:**
- Modify: `themis/backend/app/services/queue_engine.py`
- Test: `themis/backend/tests/services/test_queue_engine.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_run_estimate_sets_done_with_fields(db):
    """run_estimate writes estimate fields when slice succeeds."""
    import tempfile, os
    from unittest.mock import patch, MagicMock
    from app.models import Job, QueueConfig
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    printer_id = 1
    job_id = await _seed_job(db, printer_id)

    # Mark as pending
    async with db() as session:
        job = await session.get(Job, job_id)
        job.estimate_status = "pending"
        job.estimate_token = 1
        await session.commit()

        printer = await session.get(Printer, printer_id)
        printer.current_orca_printer_profile = "Test Machine"
        printer.loaded_filaments = [{"filament_profile": "PLA Generic", "type": "PLA", "color": ""}]
        await session.commit()

    with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False, mode="w") as f:
        f.write("; filament used [g] = 10.00\n; estimated printing time (normal mode) = 30m 0s\n")
        fake_gcode = f.name

    mgr = _make_mock_printer_manager([printer_id])
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path(tempfile.mkdtemp())

    engine = QueueEngine(db, mgr, slicer)

    async def fake_put(item):
        _, _seq, coro = item
        await coro

    engine._slice_queue.put = fake_put

    with patch.object(slicer, "slice", return_value=fake_gcode):
        await engine.run_estimate(job_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.estimate_status == "done"
        assert job.estimate_filament_grams == pytest.approx(10.0)
        assert job.estimate_seconds == 1800
        assert job.estimate_filament_breakdown is not None
        assert job.estimate_preset_label is not None

    os.unlink(fake_gcode)


@pytest.mark.asyncio
async def test_run_estimate_conditional_update_guards_against_cancel(db):
    """If estimate_status is cleared (cancellation) before write, results are discarded."""
    import tempfile, os
    from unittest.mock import patch, MagicMock
    from app.models import Job
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    printer_id = 1
    job_id = await _seed_job(db, printer_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        job.estimate_status = "pending"
        job.estimate_token = 1
        await session.commit()
        printer = await session.get(Printer, printer_id)
        printer.current_orca_printer_profile = "M"
        printer.loaded_filaments = [{"filament_profile": "PLA", "type": "PLA", "color": ""}]
        await session.commit()

    with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False, mode="w") as f:
        f.write("; filament used [g] = 10.00\n; estimated printing time (normal mode) = 30m\n")
        fake_gcode = f.name

    mgr = _make_mock_printer_manager([printer_id])
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path(tempfile.mkdtemp())
    engine = QueueEngine(db, mgr, slicer)

    # Cancel the estimate mid-flight (simulates cancel_job clearing estimate_status)
    async def fake_put(item):
        _, _seq, coro = item
        # Clear the status BEFORE running the coro (simulating cancellation race)
        async with db() as session:
            j = await session.get(Job, job_id)
            j.estimate_status = None
            await session.commit()
        await coro

    engine._slice_queue.put = fake_put

    with patch.object(slicer, "slice", return_value=fake_gcode):
        await engine.run_estimate(job_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.estimate_status is None  # cleared, not overwritten
        assert job.estimate_filament_grams is None

    os.unlink(fake_gcode)


@pytest.mark.asyncio
async def test_run_estimate_failure_sets_failed(db):
    """SliceError from slicer: estimate_status becomes 'failed'; job.status unchanged."""
    from unittest.mock import patch, MagicMock
    from app.models import Job
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService, SliceError

    printer_id = 1
    job_id = await _seed_job(db, printer_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        job.estimate_status = "pending"
        job.estimate_token = 1
        await session.commit()
        printer = await session.get(Printer, printer_id)
        printer.current_orca_printer_profile = "M"
        printer.loaded_filaments = [{"filament_profile": "PLA", "type": "PLA", "color": ""}]
        await session.commit()

    mgr = _make_mock_printer_manager([printer_id])
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path("/tmp/test_estimates")

    engine = QueueEngine(db, mgr, slicer)

    async def fake_put(item):
        _, _seq, coro = item
        await coro

    engine._slice_queue.put = fake_put

    with patch.object(slicer, "slice", side_effect=SliceError("profile not found")):
        await engine.run_estimate(job_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.estimate_status == "failed"
        assert job.status == "queued"  # job.status must not be touched
        assert job.block_reason is None
```

- [ ] **Step 2: Run to confirm failure**

```
cd themis/backend && pytest tests/services/test_queue_engine.py::test_run_estimate_sets_done_with_fields -v
```
Expected: AttributeError (`QueueEngine has no attribute 'run_estimate'`)

- [ ] **Step 3: Add `spawn_estimate`, `run_estimate`, and `_fail_estimate` to `QueueEngine`**

Add these methods to `QueueEngine` (after `_slice_worker`):

```python
def spawn_estimate(self, job_id: int) -> None:
    """Create and track a background estimate task for job_id."""
    task = asyncio.create_task(
        self.run_estimate(job_id), name=f"estimate-{job_id}"
    )
    self._estimate_tasks.add(task)
    task.add_done_callback(self._estimate_tasks.discard)

async def run_estimate(self, job_id: int) -> None:
    """Background test slice to populate estimate_* fields on the Job row."""
    import json as _json
    import shutil

    # Step 1 — Load job and resolve config
    token: int | None = None
    machine_preset: str | None = None
    stored_path: str | None = None
    filament_profiles: list[str] = []
    print_profile: str | None = None
    printer_name: str | None = None
    preset_label: dict | None = None

    async with self._factory() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status in ("cancelled", "complete", "failed"):
            return
        if job.estimate_status != "pending":
            return
        token = job.estimate_token

        # Load the first JobPrinterConfig (lowest id)
        from sqlalchemy import select as _select
        cfg_result = await session.execute(
            _select(JobPrinterConfig)
            .where(JobPrinterConfig.job_id == job_id)
            .order_by(JobPrinterConfig.id.asc())
            .limit(1)
        )
        config = cfg_result.scalar_one_or_none()
        if config is None:
            return

        printer = await session.get(Printer, config.printer_id)
        if printer is None:
            return
        uploaded_file = await session.get(UploadedFile, job.uploaded_file_id)

        # Capture all scalars before session closes
        machine_preset = printer.current_orca_printer_profile or ""
        print_profile = config.print_profile or ""
        stored_path = uploaded_file.stored_path if uploaded_file else None
        printer_name = printer.name
        loaded = printer.loaded_filaments or []

        # Resolve filament profiles
        fmap = config.filament_map
        if fmap:
            for entry in sorted(fmap, key=lambda e: e.get("tool_index", 0) or 0):
                ti = entry.get("tool_index")
                ep = entry.get("filament_profile")
                if ep:
                    filament_profiles.append(ep)
                elif ti is not None and ti < len(loaded):
                    filament_profiles.append(loaded[ti].get("filament_profile", ""))
        else:
            slot = _slot_for_config(config, loaded)
            fp = config.filament_profile or (slot.get("filament_profile") if slot else None)
            if fp:
                filament_profiles.append(fp)

        preset_label = {
            "printer_name": printer_name,
            "machine_profile": machine_preset,
            "process_profile": print_profile,
            "filament_profiles": filament_profiles,
        }

    # Step 2 — Pre-flight validation
    if not machine_preset or not stored_path or not filament_profiles:
        await self._fail_estimate(job_id, token, "missing machine preset, file, or filament profile")
        return

    # Step 3 — Enqueue slice
    output_dir = self._slicer._data_dir / "gcode_estimates" / str(job_id)
    req = SliceRequest(
        job_id=job_id,
        source_3mf=stored_path,
        plate_number=1,
        machine_preset=machine_preset,
        process_preset=print_profile,
        filament_presets=filament_profiles,
        filament_colours=[],
        export_args=[],
        prepare_hook=None,
    )

    fut: asyncio.Future = asyncio.get_running_loop().create_future()

    async def _do_estimate_slice():
        try:
            async with self._factory() as s:
                j = await s.get(Job, job_id)
                if j is None or j.status in ("cancelled", "complete", "failed"):
                    if not fut.cancelled():
                        fut.cancel()
                    return
                if j.estimate_status != "pending":
                    if not fut.cancelled():
                        fut.cancel()
                    return
            result = await asyncio.to_thread(self._slicer.slice, req, output_dir)
            if not fut.done():
                fut.set_result(result)
        except Exception as exc:
            if not fut.done():
                fut.set_exception(exc)

    await self._slice_queue.put((1, next(self._slice_seq), _do_estimate_slice()))

    try:
        gcode_path = await fut
    except asyncio.CancelledError:
        return
    except Exception as exc:
        logger.warning("Estimate slice failed for job %s: %s", job_id, exc)
        await self._fail_estimate(job_id, token, str(exc))
        return

    # Step 4 — Parse, discard gcode, write results
    grams, secs, extruder_grams = _parse_gcode_estimates(gcode_path)
    try:
        shutil.rmtree(output_dir, ignore_errors=True)
    except OSError:
        pass

    breakdown = None
    if extruder_grams is not None:
        breakdown = [
            {
                "extruder_index": i,
                "filament_profile": filament_profiles[i] if i < len(filament_profiles) else None,
                "grams": g,
            }
            for i, g in enumerate(extruder_grams)
        ]

    from sqlalchemy import text as _text
    async with self._factory() as session:
        result = await session.execute(
            _text(
                "UPDATE jobs SET estimate_status='done', estimate_seconds=:secs, "
                "estimate_filament_grams=:grams, estimate_filament_breakdown=:bd, "
                "estimate_preset_label=:label, updated_at=:now "
                "WHERE id=:id AND estimate_status='pending' AND estimate_token=:token"
            ),
            {
                "secs": secs,
                "grams": grams,
                "bd": _json.dumps(breakdown),
                "label": _json.dumps(preset_label),
                "now": _now(),
                "id": job_id,
                "token": token,
            }
        )
        if result.rowcount == 0:
            return  # cancelled or retriggered — discard
        await session.commit()

    await self._broadcast_job(job_id)

async def _fail_estimate(self, job_id: int, token: int, reason: str) -> None:
    from sqlalchemy import text as _text
    async with self._factory() as session:
        result = await session.execute(
            _text(
                "UPDATE jobs SET estimate_status='failed', updated_at=:now "
                "WHERE id=:id AND estimate_status='pending' AND estimate_token=:token"
            ),
            {"now": _now(), "id": job_id, "token": token}
        )
        if result.rowcount > 0:
            await session.commit()
    logger.warning("Estimate failed for job %s: %s", job_id, reason)
    await self._broadcast_job(job_id)
```

Note: `SliceRequest` is already imported in `queue_engine.py`. The `text` import from sqlalchemy is already at the top.

- [ ] **Step 4: Run tests to confirm pass**

```
cd themis/backend && pytest tests/services/test_queue_engine.py -k "estimate" -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/queue_engine.py backend/tests/services/test_queue_engine.py
git commit -m "feat: add run_estimate, spawn_estimate, _fail_estimate to QueueEngine"
```

---

## Task 9: Spoolman deduction + startup recovery

**Files:**
- Modify: `themis/backend/app/services/queue_engine.py`
- Test: `themis/backend/tests/services/test_queue_engine.py`

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_handle_print_complete_fires_spoolman_deduction(db):
    """On completion, deducts actual_filament_grams from Spoolman using slot's spool_id."""
    from unittest.mock import patch, AsyncMock, MagicMock
    from app.models import Job, GcodeFile, SpoolmanConfig
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    printer_id = 1
    job_id = await _seed_job(db, printer_id, status="printing")

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        printer.loaded_filaments = [{"type": "PLA", "color": "", "filament_profile": "PLA",
                                      "spoolman_spool_id": 42}]
        job = await session.get(Job, job_id)
        job.status = "printing"
        job.assigned_printer_id = printer_id
        job.actual_filament_grams = 17.5
        session.add(GcodeFile(job_id=job_id, printer_id=printer_id, path="/fake.gcode"))
        session.add(SpoolmanConfig(id=1, enabled=True, url="http://spoolman.test", api_key=None))
        await session.commit()

    mgr = _make_mock_printer_manager([printer_id])
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path("/tmp")
    engine = QueueEngine(db, mgr, slicer)

    deduction_calls = []

    async def fake_deduct(url, api_key, spool_id, grams):
        deduction_calls.append({"spool_id": spool_id, "grams": grams})

    with patch("app.services.queue_engine._deduct_spool", fake_deduct):
        await engine.handle_print_complete(printer_id)

    assert len(deduction_calls) == 1
    assert deduction_calls[0]["spool_id"] == 42
    assert deduction_calls[0]["grams"] == pytest.approx(17.5)


@pytest.mark.asyncio
async def test_handle_print_complete_skips_deduction_when_grams_none(db):
    """Deduction is skipped when actual_filament_grams is None."""
    from unittest.mock import patch, MagicMock
    from app.models import Job, GcodeFile, SpoolmanConfig
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    printer_id = 1
    job_id = await _seed_job(db, printer_id, status="printing")

    async with db() as session:
        printer = await session.get(Printer, printer_id)
        printer.loaded_filaments = [{"spoolman_spool_id": 5}]
        job = await session.get(Job, job_id)
        job.status = "printing"
        job.assigned_printer_id = printer_id
        job.actual_filament_grams = None  # not captured
        session.add(GcodeFile(job_id=job_id, printer_id=printer_id, path="/fake.gcode"))
        session.add(SpoolmanConfig(id=1, enabled=True, url="http://spoolman.test", api_key=None))
        await session.commit()

    mgr = _make_mock_printer_manager([printer_id])
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path("/tmp")
    engine = QueueEngine(db, mgr, slicer)

    deduction_calls = []
    async def fake_deduct(*a, **kw):
        deduction_calls.append(a)

    with patch("app.services.queue_engine._deduct_spool", fake_deduct):
        await engine.handle_print_complete(printer_id)

    assert deduction_calls == []


@pytest.mark.asyncio
async def test_startup_resets_pending_estimates(db):
    """QueueEngine.start() resets all estimate_status='pending' to NULL."""
    from unittest.mock import patch, MagicMock
    from app.models import Job, QueueConfig
    from app.services.queue_engine import QueueEngine
    from app.services.slicer_service import SlicerService

    printer_id = 1
    job_id = await _seed_job(db, printer_id)

    async with db() as session:
        job = await session.get(Job, job_id)
        job.estimate_status = "pending"
        await session.commit()

    mgr = _make_mock_printer_manager([])
    slicer = MagicMock(spec=SlicerService)
    slicer._data_dir = Path("/tmp")
    engine = QueueEngine(db, mgr, slicer)

    with patch.object(engine, "_loop", new_callable=AsyncMock):
        # Patch _loop so start() doesn't block
        engine._task = None
        with patch("asyncio.create_task", return_value=MagicMock()):
            await engine.start()

    async with db() as session:
        job = await session.get(Job, job_id)
        assert job.estimate_status is None
```

- [ ] **Step 2: Run to confirm failures**

```
cd themis/backend && pytest tests/services/test_queue_engine.py::test_handle_print_complete_fires_spoolman_deduction -v
```
Expected: ImportError (`cannot import name '_deduct_spool'`) or AssertionError

- [ ] **Step 3: Add `_deduct_spool` module-level helper to `queue_engine.py`**

Add after `_now()` and before `_norm_color()`:

```python
async def _deduct_spool(url: str, api_key: str | None, spool_id: int, grams: float) -> None:
    """Fire-and-forget Spoolman deduction. Logs warning on failure; never raises."""
    try:
        from .spoolman_service import record_spool_use
        await record_spool_use(url, api_key, spool_id, grams)
    except Exception:
        logger.warning("Spoolman deduction failed: spool_id=%s grams=%s", spool_id, grams)
```

- [ ] **Step 4: Update `handle_print_complete` to resolve spool + fire deduction**

In `handle_print_complete`, inside the existing `async with self._factory() as session:` block, **before** `await session.commit()`, add the spool resolution logic. After commit, fire the deduction task.

Replace the existing `handle_print_complete` body with:

```python
async def handle_print_complete(self, printer_id: int) -> None:
    """Called by PrinterManager when the printer's vendor client signals print done."""
    job_id = None
    spool_id: int | None = None
    spoolman_url: str | None = None
    spoolman_key: str | None = None
    grams_to_deduct: float | None = None

    async with self._factory() as session:
        result = await session.execute(
            select(Job).where(
                Job.status == "printing",
                Job.assigned_printer_id == printer_id,
            )
        )
        job = result.scalar_one_or_none()
        if job is None:
            return
        job_id = job.id
        job.status = "complete"
        job.completed_at = _now()
        job.updated_at = _now()

        # Resolve Spoolman deduction scalars while session is open
        grams_to_deduct = job.actual_filament_grams
        if grams_to_deduct is not None:
            from ..models import SpoolmanConfig
            spoolman_cfg = await session.get(SpoolmanConfig, 1)
            if spoolman_cfg and spoolman_cfg.enabled and spoolman_cfg.url:
                printer = await session.get(Printer, printer_id)
                config_result = await session.execute(
                    select(JobPrinterConfig).where(
                        JobPrinterConfig.job_id == job_id,
                        JobPrinterConfig.printer_id == printer_id,
                    )
                )
                config = config_result.scalar_one_or_none()
                if printer and config:
                    slot = _slot_for_config(config, printer.loaded_filaments or [])
                    if slot:
                        spool_id = slot.get("spoolman_spool_id")
                spoolman_url = spoolman_cfg.url
                spoolman_key = spoolman_cfg.api_key

        # Delete gcode file from disk and DB
        gcode_result = await session.execute(
            select(GcodeFile).where(
                GcodeFile.job_id == job_id,
                GcodeFile.printer_id == printer_id,
            )
        )
        gcode = gcode_result.scalar_one_or_none()
        if gcode:
            try:
                os.remove(gcode.path)
            except OSError:
                pass
            await session.delete(gcode)
        await session.commit()

    # Fire Spoolman deduction (fire-and-forget; never blocks completion)
    if spool_id is not None and spoolman_url and grams_to_deduct is not None:
        task = asyncio.create_task(
            _deduct_spool(spoolman_url, spoolman_key, spool_id, grams_to_deduct)
        )
        self._estimate_tasks.add(task)
        task.add_done_callback(self._estimate_tasks.discard)

    await self._broadcast_job(job_id)
    await self._fire_webhooks(job_id, "job.complete")
```

- [ ] **Step 5: Update reconcile FAILED branch to set `deduction_skipped = True`**

In `_reconcile_printing_jobs`, in the `if ended_in_failure:` block, add before `await session.commit()`:

```python
job.deduction_skipped = True
```

- [ ] **Step 6: Run tests to confirm pass**

```
cd themis/backend && pytest tests/services/test_queue_engine.py -v --tb=short
```
Expected: PASS (all tests)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/queue_engine.py backend/tests/services/test_queue_engine.py
git commit -m "feat: add Spoolman deduction to handle_print_complete, startup recovery for estimates"
```

---

## Task 10: `jobs.py` API changes

**Files:**
- Modify: `themis/backend/app/api/routes/jobs.py`
- Test: `themis/backend/tests/api/test_jobs_api.py`

- [ ] **Step 1: Write failing tests**

```python
async def test_job_response_includes_estimate_fields(client, tmp_path):
    """POST /jobs response includes all new estimate and actual fields."""
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)
    payload = {
        "uploaded_file_id": file_id,
        "plate_number": 1,
        "printer_configs": [{"printer_id": printer_id, "print_profile": "0.20mm", "filament_profile": "PLA"}],
    }
    with patch("app.api.routes.jobs.queue_engine") as mock_qe:
        mock_qe.spawn_estimate = MagicMock()
        resp = await client.post("/api/v1/jobs", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    # New fields must be present (null since estimates disabled by default)
    for field in ["estimate_status", "estimate_filament_grams", "estimate_seconds",
                  "estimate_filament_breakdown", "estimate_preset_label",
                  "actual_filament_grams", "actual_seconds", "deduction_skipped"]:
        assert field in data, f"missing: {field}"


async def test_cancel_job_clears_estimate_status(client, tmp_path):
    """POST /jobs/{id}/cancel clears estimate_status when it is 'pending'."""
    from unittest.mock import patch, MagicMock, AsyncMock
    from app.models import Job, QueueConfig
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from app.database import Base, get_session

    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)

    with patch("app.api.routes.jobs.queue_engine") as mock_qe:
        mock_qe.spawn_estimate = MagicMock()
        resp = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id, "plate_number": 1,
            "printer_configs": [{"printer_id": printer_id, "print_profile": "0.20mm", "filament_profile": "PLA"}]
        })
    job_id = resp.json()["id"]

    # Manually set estimate_status to pending
    # (access via the test session through the app's session factory)
    async with (await client.app.dependency_overrides[get_session]()) as session:
        job = await session.get(Job, job_id)
        job.estimate_status = "pending"
        await session.commit()

    with patch("app.api.routes.jobs.queue_engine") as mock_qe:
        cancel_resp = await client.post(f"/api/v1/jobs/{job_id}/cancel")

    assert cancel_resp.status_code == 200
    data = cancel_resp.json()
    assert data["estimate_status"] is None


async def test_job_details_returns_live_fields(client, tmp_path):
    """GET /jobs/{id}/details returns filament_grams_live and estimated_seconds_live
    (not the old filament_grams / estimated_seconds keys)."""
    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)

    with patch("app.api.routes.jobs.queue_engine") as mock_qe:
        mock_qe.spawn_estimate = MagicMock()
        resp = await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id, "plate_number": 1,
            "printer_configs": [{"printer_id": printer_id, "print_profile": "0.20mm", "filament_profile": "PLA"}]
        })
    job_id = resp.json()["id"]

    detail_resp = await client.get(f"/api/v1/jobs/{job_id}/details")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    # New live keys
    assert "filament_grams_live" in detail
    assert "estimated_seconds_live" in detail
    # Old keys must not be present
    assert "filament_grams" not in detail
    assert "estimated_seconds" not in detail
```

- [ ] **Step 2: Run to confirm failures**

```
cd themis/backend && pytest tests/api/test_jobs_api.py::test_job_response_includes_estimate_fields -v
```
Expected: AssertionError (missing fields)

- [ ] **Step 3: Update `_to_dict` in `jobs.py`**

Replace `_to_dict` body:

```python
def _to_dict(j: Job) -> dict:
    return {
        "id": j.id,
        "uploaded_file_id": j.uploaded_file_id,
        "plate_number": j.plate_number,
        "order_id": j.order_id,
        "project_id": j.project_id,
        "assigned_printer_id": j.assigned_printer_id,
        "queue_position": j.queue_position,
        "status": j.status,
        "overrides": j.overrides,
        "outcome": j.outcome,
        "project_item_quantities": json.loads(j.project_item_quantities) if j.project_item_quantities else None,
        "created_at": j.created_at,
        "updated_at": j.updated_at,
        "completed_at": j.completed_at,
        # Actual values (populated at production slice time)
        "actual_filament_grams": j.actual_filament_grams,
        "actual_seconds": j.actual_seconds,
        "actual_filament_breakdown": j.actual_filament_breakdown,
        "deduction_skipped": j.deduction_skipped,
        # Estimate values (populated after background test slice)
        "estimate_status": j.estimate_status,
        "estimate_seconds": j.estimate_seconds,
        "estimate_filament_grams": j.estimate_filament_grams,
        "estimate_filament_breakdown": j.estimate_filament_breakdown,
        "estimate_preset_label": j.estimate_preset_label,
    }
```

- [ ] **Step 4: Update `create_job` to trigger estimate when enabled**

Add import at top of `jobs.py`:
```python
from ...models import GcodeFile, Job, JobItemFailure, JobPrinterConfig, Order, Printer, Project, ProjectItem, QueueConfig, UploadedFile
```

After `queue_engine.wake()` in `create_job`, add:

```python
async with get_session() as cfg_session:
    queue_cfg = await cfg_session.get(QueueConfig, 1)
    estimates_enabled = queue_cfg is not None and queue_cfg.estimates_enabled

if estimates_enabled:
    async with get_session() as est_session:
        j = await est_session.get(Job, job.id)
        j.estimate_token = (j.estimate_token or 0) + 1
        j.estimate_status = "pending"
        await est_session.commit()
    queue_engine.spawn_estimate(job.id)
```

Wait — `create_job` already has a `session` parameter via `Depends(get_session)`. To open additional sessions, use `SessionLocal()` from `...database`. Import `SessionLocal` from `...database`:

```python
from ...database import get_session, SessionLocal
```

Then:
```python
async with SessionLocal() as cfg_session:
    queue_cfg = await cfg_session.get(QueueConfig, 1)
    estimates_enabled = queue_cfg is not None and queue_cfg.estimates_enabled

if estimates_enabled:
    async with SessionLocal() as est_session:
        j = await est_session.get(Job, job.id)
        j.estimate_token = (j.estimate_token or 0) + 1
        j.estimate_status = "pending"
        await est_session.commit()
    queue_engine.spawn_estimate(job.id)
```

- [ ] **Step 5: Update `cancel_job` to clear estimate_status**

In `cancel_job`, before `job.status = "cancelled"`, add:

```python
if getattr(job, "estimate_status", None) == "pending":
    job.estimate_status = None
```

- [ ] **Step 6: Update `update_job_configs` to clear + re-trigger estimate**

After deleting existing configs and before adding new ones (after the `for row in existing...` loop), add:

```python
# Clear stale estimate fields
job.estimate_status = None
job.estimate_seconds = None
job.estimate_filament_grams = None
job.estimate_filament_breakdown = None
job.estimate_preset_label = None
```

After `queue_engine.wake()` at the end of `update_job_configs`, add:

```python
async with SessionLocal() as cfg_session:
    queue_cfg = await cfg_session.get(QueueConfig, 1)
    estimates_enabled = queue_cfg is not None and queue_cfg.estimates_enabled

if estimates_enabled:
    async with SessionLocal() as est_session:
        j = await est_session.get(Job, job.id)
        j.estimate_token = (j.estimate_token or 0) + 1
        j.estimate_status = "pending"
        await est_session.commit()
    queue_engine.spawn_estimate(job.id)
```

- [ ] **Step 7: Update `get_job_details` live fields**

In `get_job_details`, replace the return dict entries:
```python
# Before:
"filament_grams": gcode_rec.filament_grams if gcode_rec else None,
"estimated_seconds": gcode_rec.estimated_seconds if gcode_rec else None,

# After:
"filament_grams_live": gcode_rec.filament_grams if gcode_rec else None,
"estimated_seconds_live": gcode_rec.estimated_seconds if gcode_rec else None,
```

- [ ] **Step 8: Run tests**

```
cd themis/backend && pytest tests/api/test_jobs_api.py -v --tb=short
```
Expected: PASS (new tests and all existing)

- [ ] **Step 9: Commit**

```bash
git add backend/app/api/routes/jobs.py backend/tests/api/test_jobs_api.py
git commit -m "feat: jobs.py API — new fields in _to_dict, estimate trigger, live field rename"
```

---

## Task 11: `projects.py` — rollup + `generate_project` estimate trigger

**Files:**
- Modify: `themis/backend/app/api/routes/projects.py`
- Test: `themis/backend/tests/api/test_projects_api.py`

- [ ] **Step 1: Write failing tests**

```python
async def test_project_estimate_rollup_keys(client):
    """GET /projects/{id} response includes new rollup keys and excludes old ones."""
    resp = await client.post("/api/v1/projects", json={
        "name": "Test", "customer": "", "order_type": "internal",
        "on_hold": False, "due_date": None, "notes": None,
    })
    assert resp.status_code == 201
    proj_id = resp.json()["id"]

    get_resp = await client.get(f"/api/v1/projects/{proj_id}")
    data = get_resp.json()
    for key in ["estimate_filament_grams_total", "estimate_seconds_total",
                "estimate_filament_grams_remaining", "estimate_seconds_remaining",
                "actual_filament_grams", "actual_seconds"]:
        assert key in data, f"missing: {key}"
    assert "filament_grams" not in data
    assert "estimated_seconds" not in data


async def test_project_estimate_remaining_excludes_terminal_jobs(client, tmp_path):
    """estimate_filament_grams_remaining excludes completed/cancelled/failed jobs."""
    from unittest.mock import patch
    from app.models import Job
    from app.database import get_session

    resp = await client.post("/api/v1/projects", json={
        "name": "P", "customer": "", "order_type": "internal",
        "on_hold": False, "due_date": None, "notes": None,
    })
    proj_id = resp.json()["id"]

    file_id = await _upload_file(client, tmp_path)
    printer_id = await _create_printer(client)

    with patch("app.api.routes.jobs.queue_engine") as mock_qe:
        mock_qe.spawn_estimate = MagicMock()
        j1 = (await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id, "plate_number": 1,
            "printer_configs": [{"printer_id": printer_id, "print_profile": "0.20mm", "filament_profile": "PLA"}]
        })).json()

    # Set project_id and estimates on both jobs directly
    async with (await client.app.dependency_overrides[get_session]()) as session:
        job = await session.get(Job, j1["id"])
        job.project_id = proj_id
        job.estimate_filament_grams = 10.0
        job.status = "complete"  # terminal — excluded from remaining
        await session.commit()

    detail = (await client.get(f"/api/v1/projects/{proj_id}")).json()
    assert detail["estimate_filament_grams_total"] == pytest.approx(10.0)
    assert detail["estimate_filament_grams_remaining"] is None  # all jobs terminal
    assert detail["actual_filament_grams"] is None
```

- [ ] **Step 2: Run to confirm failures**

```
cd themis/backend && pytest tests/api/test_projects_api.py::test_project_estimate_rollup_keys -v
```
Expected: AssertionError (missing keys in response)

- [ ] **Step 3: Replace `_project_dict` in `projects.py`**

Add `GcodeFile` to imports if present (it should be removable — we're dropping the gcode join):

Remove from imports: `GcodeFile` (if it's only used in `_project_dict`)

Replace the `gcode_rows` computation block and the old `filament_grams`/`estimated_seconds` keys in `_project_dict` with:

```python
async def _project_dict(project: Project, session: AsyncSession) -> dict:
    items = await _load_items(session, project.id)
    links = await _load_links(session, project.id)
    jobs_total = (await session.execute(
        select(func.count()).where(Job.project_id == project.id)
    )).scalar() or 0
    jobs_complete = (await session.execute(
        select(func.count()).where(Job.project_id == project.id, Job.status == "complete")
    )).scalar() or 0

    job_rows = (await session.execute(
        select(Job).where(Job.project_id == project.id)
    )).scalars().all()

    _TERMINAL = {"complete", "failed", "cancelled"}

    estimate_filament_grams_total = (
        sum(j.estimate_filament_grams for j in job_rows if j.estimate_filament_grams is not None) or None
    )
    estimate_seconds_total = (
        sum(j.estimate_seconds for j in job_rows if j.estimate_seconds is not None) or None
    )
    estimate_filament_grams_remaining = (
        sum(
            j.estimate_filament_grams for j in job_rows
            if j.estimate_filament_grams is not None and j.status not in _TERMINAL
        ) or None
    )
    estimate_seconds_remaining = (
        sum(
            j.estimate_seconds for j in job_rows
            if j.estimate_seconds is not None and j.status not in _TERMINAL
        ) or None
    )
    actual_filament_grams = (
        sum(j.actual_filament_grams for j in job_rows if j.actual_filament_grams is not None) or None
    )
    actual_seconds = (
        sum(j.actual_seconds for j in job_rows if j.actual_seconds is not None) or None
    )

    return {
        "id": project.id,
        "name": project.name,
        "customer": project.customer,
        "order_type": project.order_type,
        "on_hold": project.on_hold,
        "due_date": project.due_date,
        "notes": project.notes,
        "result_file_id": project.result_file_id,
        "source_app": project.source_app,
        "source_user": project.source_user,
        "source_layout_id": project.source_layout_id,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "items": items,
        "links": links,
        "jobs_total": jobs_total,
        "jobs_complete": jobs_complete,
        "estimate_filament_grams_total": round(estimate_filament_grams_total, 2) if estimate_filament_grams_total else None,
        "estimate_seconds_total": estimate_seconds_total,
        "estimate_filament_grams_remaining": round(estimate_filament_grams_remaining, 2) if estimate_filament_grams_remaining else None,
        "estimate_seconds_remaining": estimate_seconds_remaining,
        "actual_filament_grams": round(actual_filament_grams, 2) if actual_filament_grams else None,
        "actual_seconds": actual_seconds,
    }
```

- [ ] **Step 4: Add estimate trigger to `generate_project`**

In `generate_project`, after the line `await session.commit()` that follows adding `JobPrinterConfig` rows (around line 855), add:

```python
# Trigger estimates for each new job if enabled
from app.database import SessionLocal as _SessionLocal
from app.models import QueueConfig as _QueueConfig
async with _SessionLocal() as cfg_sess:
    _queue_cfg = await cfg_sess.get(_QueueConfig, 1)
    _est_enabled = _queue_cfg is not None and _queue_cfg.estimates_enabled

if _est_enabled:
    for j in new_jobs:
        async with _SessionLocal() as est_sess:
            jj = await est_sess.get(Job, j.id)
            jj.estimate_token = (jj.estimate_token or 0) + 1
            jj.estimate_status = "pending"
            await est_sess.commit()
        queue_engine.spawn_estimate(j.id)
```

- [ ] **Step 5: Run tests**

```
cd themis/backend && pytest tests/api/test_projects_api.py -v --tb=short
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/projects.py backend/tests/api/test_projects_api.py
git commit -m "feat: projects rollup uses Job actuals/estimates; generate_project triggers estimates"
```

---

## Task 12: `settings.py` — `estimates_enabled` toggle

**Files:**
- Modify: `themis/backend/app/api/routes/settings.py`
- Test: `themis/backend/tests/api/test_settings_routes.py`

- [ ] **Step 1: Write failing test** (add to `test_settings_routes.py`)

```python
async def test_estimates_enabled_get_put(client):
    """GET /settings/queue includes estimates_enabled; PUT persists it."""
    get_resp = await client.get("/api/v1/settings/queue")
    assert get_resp.status_code == 200
    assert "estimates_enabled" in get_resp.json()
    assert get_resp.json()["estimates_enabled"] is False

    put_resp = await client.put("/api/v1/settings/queue", json={"estimates_enabled": True})
    assert put_resp.status_code == 200
    assert put_resp.json()["estimates_enabled"] is True

    # Verify it persisted
    get_resp2 = await client.get("/api/v1/settings/queue")
    assert get_resp2.json()["estimates_enabled"] is True
```

- [ ] **Step 2: Run to confirm failure**

```
cd themis/backend && pytest tests/api/test_settings_routes.py::test_estimates_enabled_get_put -v
```
Expected: KeyError or AssertionError (`estimates_enabled` missing)

- [ ] **Step 3: Update `settings.py`**

Update `QueueConfigOut`:
```python
class QueueConfigOut(BaseModel):
    check_interval_minutes: int
    operator_name: str | None
    snapshot_interval_seconds: int
    estimates_enabled: bool
```

Update `QueueConfigIn`:
```python
class QueueConfigIn(BaseModel):
    check_interval_minutes: int | None = None
    operator_name: str | None = None
    snapshot_interval_seconds: int | None = None
    estimates_enabled: bool | None = None
```

In `update_queue_config`, after the existing `if body.snapshot_interval_seconds is not None:` block, add:

```python
if body.estimates_enabled is not None:
    row.estimates_enabled = body.estimates_enabled
```

Update `_get_or_create_queue` to set default:
```python
row = QueueConfig(id=1, check_interval_minutes=5, snapshot_interval_seconds=2, estimates_enabled=False)
```

- [ ] **Step 4: Run tests**

```
cd themis/backend && pytest tests/api/test_settings_routes.py -v --tb=short
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/settings.py backend/tests/api/test_settings_routes.py
git commit -m "feat: add estimates_enabled to queue settings API"
```

---

## Task 13: Frontend TypeScript types

**Files:**
- Modify: `themis/frontend/src/api/queue.ts`
- Modify: `themis/frontend/src/api/projects.ts`
- Modify: `themis/frontend/src/api/settings.ts`
- Modify: `themis/frontend/src/screens/EditJobScreen.test.tsx`

- [ ] **Step 1: Update `queue.ts` — add new fields to `ApiJob` and `ApiJobDetails`**

Add to `ApiJob` interface (after `block_reason`):
```typescript
export interface ApiJob {
  id: number;
  uploaded_file_id: number;
  plate_number: number;
  order_id: number | null;
  assigned_printer_id: number | null;
  queue_position: number | null;
  status: string;
  overrides: Record<string, string> | null;
  block_reason: string | null;
  created_at: string;
  updated_at: string;
  // Actual values
  actual_filament_grams: number | null;
  actual_seconds: number | null;
  actual_filament_breakdown: Array<{ extruder_index: number; filament_profile: string | null; grams: number }> | null;
  deduction_skipped: boolean | null;
  // Estimate values
  estimate_status: 'pending' | 'done' | 'failed' | null;
  estimate_seconds: number | null;
  estimate_filament_grams: number | null;
  estimate_filament_breakdown: Array<{ extruder_index: number; filament_profile: string | null; grams: number }> | null;
  estimate_preset_label: { printer_name: string; machine_profile: string; process_profile: string; filament_profiles: string[] } | null;
}
```

Replace `ApiJobDetails` interface:
```typescript
export interface ApiJobDetails extends ApiJob {
  block_reason: string | null;
  file: { id: number; original_filename: string } | null;
  plate: { estimated_time: number | null; filament_g: number | null; thumbnail_path: string | null } | null;
  printer_configs: ApiJobPrinterConfig[];
  assigned_printer: { id: number; name: string; printer_type: string } | null;
  filament_grams_live: number | null;       // from live GcodeFile (slicing→printing only)
  estimated_seconds_live: number | null;    // from live GcodeFile (slicing→printing only)
}
```

(Remove the old `filament_grams: number | null` and `estimated_seconds: number | null` lines from `ApiJobDetails`.)

- [ ] **Step 2: Update `projects.ts`**

Locate the project type interface (likely `ApiProject` or similar) and replace `filament_grams: number | null` and `estimated_seconds: number | null` with:

```typescript
estimate_filament_grams_total: number | null;
estimate_seconds_total: number | null;
estimate_filament_grams_remaining: number | null;
estimate_seconds_remaining: number | null;
actual_filament_grams: number | null;
actual_seconds: number | null;
```

- [ ] **Step 3: Update `settings.ts`**

In the queue config type, add:
```typescript
estimates_enabled: boolean;
```

In the update type, add:
```typescript
estimates_enabled?: boolean;
```

- [ ] **Step 4: Fix `EditJobScreen.test.tsx` fixture**

Find the fixture around lines 73–74:
```typescript
filament_grams: null,
estimated_seconds: null,
```

Replace with:
```typescript
filament_grams_live: null,
estimated_seconds_live: null,
actual_filament_grams: null,
actual_seconds: null,
actual_filament_breakdown: null,
deduction_skipped: null,
estimate_status: null,
estimate_seconds: null,
estimate_filament_grams: null,
estimate_filament_breakdown: null,
estimate_preset_label: null,
```

- [ ] **Step 5: Run TypeScript type check**

```
cd themis/frontend && npx tsc --noEmit
```
Expected: No errors

- [ ] **Step 6: Run frontend tests**

```
cd themis/frontend && npx vitest run
```
Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd themis
git add frontend/src/api/queue.ts frontend/src/api/projects.ts frontend/src/api/settings.ts
git add frontend/src/screens/EditJobScreen.test.tsx
git commit -m "feat: update frontend TypeScript types for estimates/actuals"
```

---

## Task 14: SettingsScreen — estimates_enabled toggle

**Files:**
- Modify: `themis/frontend/src/screens/SettingsScreen.tsx`
- Test: `themis/frontend/src/screens/SettingsScreen.test.tsx`

- [ ] **Step 1: Read the Queue settings card section**

Read `SettingsScreen.tsx` to locate the Queue settings card and identify where to add the toggle. Look for the `check_interval_minutes` field — the toggle goes after the existing Queue settings fields.

- [ ] **Step 2: Write a failing test** (add to `SettingsScreen.test.tsx`)

```typescript
it('renders estimates_enabled toggle in queue settings', async () => {
  // Mock a queue config response that includes estimates_enabled
  mockFetch.mockImplementation((url: string) => {
    if (url.includes('/settings/queue')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        check_interval_minutes: 5,
        operator_name: null,
        snapshot_interval_seconds: 2,
        estimates_enabled: false,
      }) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  });

  render(<SettingsScreen />);

  await waitFor(() => {
    expect(screen.getByText(/enable estimate generation/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Add toggle to SettingsScreen**

In the Queue settings card body, add after the last existing queue field:

```tsx
{/* Estimate generation toggle */}
<label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.75rem' }}>
  <input
    type="checkbox"
    checked={queueConfig?.estimates_enabled ?? false}
    onChange={async (e) => {
      await updateQueueConfig({ estimates_enabled: e.target.checked });
    }}
  />
  <span>Enable estimate generation for queued jobs</span>
</label>
<p style={{ fontSize: '0.8rem', color: 'var(--muted)', marginTop: '0.25rem' }}>
  When enabled, a test slice runs immediately after job creation to estimate print time
  and filament use. The gcode is discarded — only time and grams are stored.
</p>
```

Adapt the exact JSX to match the existing patterns in the file (check how other checkboxes or toggles are rendered).

- [ ] **Step 4: Run test**

```
cd themis/frontend && npx vitest run src/screens/SettingsScreen.test.tsx
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/screens/SettingsScreen.tsx frontend/src/screens/SettingsScreen.test.tsx
git commit -m "feat: add estimates_enabled toggle to Queue settings card"
```

---

## Task 15: JobDetailScreen — estimate + actual sections

**Files:**
- Modify: `themis/frontend/src/screens/JobDetailScreen.tsx`

- [ ] **Step 1: Read the file**

Read `JobDetailScreen.tsx` to understand where job fields are displayed. Identify where to insert the estimate card and actual section. Look for where `filament_grams` or `estimated_seconds` are referenced — those references must be updated to `filament_grams_live` / `estimated_seconds_live`.

- [ ] **Step 2: Replace `filament_grams` → `filament_grams_live` and `estimated_seconds` → `estimated_seconds_live`**

Use grep to find all occurrences:
```
cd themis/frontend && grep -n "filament_grams\|estimated_seconds" src/screens/JobDetailScreen.tsx
```

Update each reference. The live values come from `job.filament_grams_live` and `job.estimated_seconds_live` and are only present while `GcodeFile` exists (slicing → printing states).

- [ ] **Step 3: Add estimate section**

After the existing job status/info display, add an estimate card visible when `job.estimate_status !== null`:

```tsx
{/* Estimate section */}
{job.estimate_status !== null && (
  <div className="info-card">
    <h3>Estimate</h3>
    {job.estimate_status === 'pending' && (
      <p style={{ color: 'var(--muted)' }}>Estimating…</p>
    )}
    {job.estimate_status === 'done' && job.estimate_seconds !== null && (
      <>
        <p>Time: {formatSeconds(job.estimate_seconds)}</p>
        <p>Filament: {job.estimate_filament_grams?.toFixed(1)} g</p>
        {job.estimate_filament_breakdown && (
          <ul>
            {job.estimate_filament_breakdown.map((b) => (
              <li key={b.extruder_index}>
                T{b.extruder_index}: {b.filament_profile ?? '—'} — {b.grams.toFixed(1)} g
              </li>
            ))}
          </ul>
        )}
        {job.estimate_preset_label && (
          <p style={{ fontSize: '0.8rem', color: 'var(--muted)' }}>
            {job.estimate_preset_label.printer_name} ·{' '}
            {job.estimate_preset_label.process_profile}
          </p>
        )}
      </>
    )}
    {job.estimate_status === 'failed' && (
      <p style={{ color: 'var(--muted)' }}>Estimate unavailable</p>
    )}
  </div>
)}
```

- [ ] **Step 4: Add actual section**

After the estimate section (or near the completion status), add:

```tsx
{/* Actual section — shown after completion */}
{(job.status === 'complete' || job.status === 'failed') && job.actual_filament_grams !== null && (
  <div className="info-card">
    <h3>Actual</h3>
    <p>Time: {job.actual_seconds !== null ? formatSeconds(job.actual_seconds) : '—'}</p>
    <p>Filament: {job.actual_filament_grams.toFixed(1)} g</p>
    {job.actual_filament_breakdown && (
      <ul>
        {job.actual_filament_breakdown.map((b) => (
          <li key={b.extruder_index}>
            T{b.extruder_index}: {b.filament_profile ?? '—'} — {b.grams.toFixed(1)} g
          </li>
        ))}
      </ul>
    )}
    {job.deduction_skipped && (
      <p style={{ color: 'var(--warn, #f59e0b)', fontWeight: 500 }}>
        Print was aborted — please manually update your Spoolman inventory.
      </p>
    )}
  </div>
)}
```

Where `formatSeconds` is a helper that converts seconds to `"1h 30m"`. Check if one already exists in the codebase; if not, add:

```tsx
function formatSeconds(s: number): string {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}
```

- [ ] **Step 5: Check TypeScript**

```
cd themis/frontend && npx tsc --noEmit
```
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add frontend/src/screens/JobDetailScreen.tsx
git commit -m "feat: add estimate card and actual section to JobDetailScreen"
```

---

## Task 16: HistoryScreen + ProjectDetailScreen rollup

**Files:**
- Modify: `themis/frontend/src/screens/HistoryScreen.tsx`
- Modify: `themis/frontend/src/screens/ProjectDetailScreen.tsx`

- [ ] **Step 1: Update HistoryScreen**

Read `HistoryScreen.tsx` to find the history table. Locate where `filament_grams` was previously shown (or where to add new columns). Add two column groups:

1. **Estimate** group: `estimate_filament_grams` (format as `"{n.toFixed(1)} g"` or `"—"`) and `estimate_seconds` (formatted)
2. **Actual** group: `actual_filament_grams` and `actual_seconds`

Remove any existing `filament_grams` or `estimated_seconds` columns.

```tsx
// In the table header:
<th>Est. Filament</th>
<th>Est. Time</th>
<th>Act. Filament</th>
<th>Act. Time</th>

// In each row:
<td>{job.estimate_filament_grams !== null ? `${job.estimate_filament_grams.toFixed(1)} g` : '—'}</td>
<td>{job.estimate_seconds !== null ? formatSeconds(job.estimate_seconds) : '—'}</td>
<td>{job.actual_filament_grams !== null ? `${job.actual_filament_grams.toFixed(1)} g` : '—'}</td>
<td>{job.actual_seconds !== null ? formatSeconds(job.actual_seconds) : '—'}</td>
```

- [ ] **Step 2: Update ProjectDetailScreen**

Read `ProjectDetailScreen.tsx` to find where `estimated_seconds` and `filament_grams` are displayed. Replace with three rows:

```tsx
{/* Replace the old single estimated_seconds row */}
<div className="stat-row">
  <span>Estimated total</span>
  <span>
    {project.estimate_filament_grams_total !== null
      ? `${project.estimate_filament_grams_total.toFixed(1)} g`
      : '—'}
    {project.estimate_seconds_total !== null
      ? ` / ${formatSeconds(project.estimate_seconds_total)}`
      : ''}
  </span>
</div>
<div className="stat-row">
  <span>Estimated remaining</span>
  <span>
    {project.estimate_filament_grams_remaining !== null
      ? `${project.estimate_filament_grams_remaining.toFixed(1)} g`
      : '—'}
    {project.estimate_seconds_remaining !== null
      ? ` / ${formatSeconds(project.estimate_seconds_remaining)}`
      : ''}
  </span>
</div>
<div className="stat-row">
  <span>Actual total</span>
  <span>
    {project.actual_filament_grams !== null
      ? `${project.actual_filament_grams.toFixed(1)} g`
      : '—'}
    {project.actual_seconds !== null
      ? ` / ${formatSeconds(project.actual_seconds)}`
      : ''}
  </span>
</div>
```

- [ ] **Step 3: Check TypeScript**

```
cd themis/frontend && npx tsc --noEmit
```
Expected: No errors

- [ ] **Step 4: Run all frontend tests**

```
cd themis/frontend && npx vitest run
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/screens/HistoryScreen.tsx frontend/src/screens/ProjectDetailScreen.tsx
git commit -m "feat: update HistoryScreen and ProjectDetailScreen with estimate/actual rollup"
```

---

## Final verification

- [ ] **Run full backend test suite**

```
cd themis/backend && pytest tests/ -v --tb=short
```
Expected: All green

- [ ] **Run full frontend test suite**

```
cd themis/frontend && npx vitest run
```
Expected: All green

- [ ] **Start the stack and smoke-test manually**

```
cd omnibus && docker compose up
```

1. Create a printer with a filament profile loaded
2. Enable "Estimate generation" in Settings → Queue
3. Upload a 3MF and create a job
4. Observe `estimate_status: "pending"` on the job detail screen → transitions to `"done"`
5. See estimate filament grams and time in the job detail card
6. Complete a print → verify actual values appear in history and job detail
7. Check project rollup shows estimate total / remaining / actual rows

- [ ] **Final commit if any fixes were needed**

```bash
cd themis && git add -A && git commit -m "fix: post-smoke-test adjustments"
```
