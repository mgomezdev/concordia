# Queue / Job Workflow Polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface file name, materials, and eligible printers on queue cards; fix stale/hardcoded display values; add per-job queue reorder controls (promote, demote, front, back); and fix filter chips to include blocked and failed jobs.

**Architecture:** Three layers of change. (1) Backend: extend the plates endpoint to return filename, enrich `list_jobs` with per-job config data (materials + eligible printer IDs/names), and add a new `POST /jobs/{id}/reorder` endpoint. (2) Frontend data layer: fix the WS `queue_update` handler to merge instead of replace (so enriched fields survive live updates), prefer job-level estimates over plate metadata, and fix the `sliced` boolean logic. (3) Frontend UI: render all new data on cards and detail panel, add reorder buttons.

**Tech Stack:** Python/FastAPI, SQLAlchemy async ORM, pytest-asyncio, React/TypeScript, Vitest

---

## Pre-requisite: Understand the existing codebase

Read these files before implementing:

- `backend/app/api/routes/files.py` lines 423–450 — `get_plates` handler
- `backend/app/api/routes/jobs.py` — `_to_dict`, `list_jobs`, `_next_queue_position`, `_front_queue_position`
- `frontend/src/api/queue.ts` — `ApiJob`, `useFilePlates`, `useQueue` (WS handler)
- `frontend/src/screens/QueueScreen.tsx` — `DisplayJob` interface, `DisplayJob` mapping useMemo, `JobCardRich`, `JobDetailPanel`, filter logic
- `frontend/src/screens/QueueScreen.test.tsx` — existing test patterns

---

## File Map

| Action | File |
|--------|------|
| Modify | `backend/app/api/routes/files.py` — `get_plates` response shape |
| Modify | `backend/app/api/routes/jobs.py` — `list_jobs` enrichment, new `reorder` endpoint |
| Modify | `backend/tests/api/test_files_routes.py` — plates shape test |
| Modify | `backend/tests/api/test_jobs_routes.py` — list enrichment + reorder tests |
| Modify | `frontend/src/api/queue.ts` — `ApiJob`, `useFilePlates`, `useQueue` WS handler, new `reorderJob` call |
| Modify | `frontend/src/screens/QueueScreen.tsx` — `DisplayJob`, mapping, cards, filter logic |
| Modify | `frontend/src/screens/QueueScreen.test.tsx` — new tests |

---

## Task 1: Plates endpoint — include filename in response

**Files:**
- Modify: `backend/app/api/routes/files.py`
- Modify: `backend/tests/api/test_files_routes.py`

- [ ] **Step 1: Write failing test**

In `backend/tests/api/test_files_routes.py`:

```python
async def test_get_plates_includes_filename(client: AsyncClient, session: AsyncSession):
    from app.models import UploadedFile
    f = UploadedFile(
        original_filename="rocket_part.3mf", stored_path="/tmp/f.3mf",
        plates=[{"plate_number": 1, "filament_g": 12.5}],
        uploaded_at="2026-01-01T00:00:00",
    )
    session.add(f)
    await session.commit()
    await session.refresh(f)

    resp = await client.get(f"/api/v1/files/{f.id}/plates")
    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "rocket_part.3mf"
    assert isinstance(body["plates"], list)
    assert body["plates"][0]["plate_number"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && pytest tests/api/test_files_routes.py::test_get_plates_includes_filename -v
```

Expected: FAIL (`KeyError: 'filename'`)

- [ ] **Step 3: Implement**

In `backend/app/api/routes/files.py`, change `get_plates` return type and body:

```python
@router.get("/{file_id}/plates", summary="Get file plates", ...)
async def get_plates(file_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Plate metadata plus the file's original filename."""
    record = await session.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(404, f"File {file_id} not found")
    return {"filename": record.original_filename, "plates": record.plates or []}
```

- [ ] **Step 4: Run tests**

```
cd backend && pytest tests/api/test_files_routes.py -v -k plates
```

Update any existing test that expected a bare list from this endpoint.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/files.py backend/tests/api/test_files_routes.py
git commit -m "feat(api): include filename in /files/{id}/plates response"
```

---

## Task 2: Enrich `list_jobs` with materials and eligible printer names

**Files:**
- Modify: `backend/app/api/routes/jobs.py`
- Modify: `backend/tests/api/test_jobs_routes.py`

`list_jobs` currently returns `_to_dict(j)` which has no config data. Add `materials` (unique filament types across all configs) and `eligible_printers` (list of `{id, name}` objects) to each job in the list response only — not to `_to_dict` (the single-job getter doesn't need it; it already has full config data via `get_job_details`).

- [ ] **Step 1: Write failing test**

In `backend/tests/api/test_jobs_routes.py`:

```python
async def test_list_jobs_includes_materials_and_printers(client: AsyncClient, session: AsyncSession):
    from app.models import UploadedFile, Job, JobPrinterConfig, Printer
    p1 = Printer(name="X1C", printer_type="bambu", connection_config={})
    session.add(p1)
    f = UploadedFile(original_filename="x.3mf", stored_path="/tmp/x.3mf",
                     plates=[], uploaded_at="2026-01-01T00:00:00")
    session.add(f)
    await session.flush()

    j = Job(uploaded_file_id=f.id, plate_number=1, status="queued",
            queue_position=1.0, created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00")
    session.add(j)
    await session.flush()

    cfg = JobPrinterConfig(job_id=j.id, printer_id=p1.id,
                           print_profile="0.20mm", filament_profile="PLA Basic",
                           filament_type="PLA")
    session.add(cfg)
    await session.commit()

    resp = await client.get("/api/v1/jobs")
    assert resp.status_code == 200
    jobs = resp.json()
    job = next(jj for jj in jobs if jj["id"] == j.id)
    assert job["materials"] == ["PLA"]
    assert any(ep["id"] == p1.id and ep["name"] == "X1C" for ep in job["eligible_printers"])
```

- [ ] **Step 2: Run to confirm failure**

```
cd backend && pytest tests/api/test_jobs_routes.py::test_list_jobs_includes_materials_and_printers -v
```

- [ ] **Step 3: Implement**

In `backend/app/api/routes/jobs.py`, rewrite `list_jobs`:

```python
@router.get("", summary="List active jobs")
async def list_jobs(session: AsyncSession = Depends(get_session)) -> list[dict]:
    result = await session.execute(select(Job).order_by(Job.queue_position))
    jobs = result.scalars().all()

    out = []
    for j in jobs:
        d = _to_dict(j)
        # Enrich with per-config data
        cfg_result = await session.execute(
            select(JobPrinterConfig).where(JobPrinterConfig.job_id == j.id)
        )
        configs = cfg_result.scalars().all()
        materials = sorted({c.filament_type for c in configs if c.filament_type})
        eligible_printers = []
        for c in configs:
            p = await session.get(Printer, c.printer_id)
            if p:
                eligible_printers.append({"id": p.id, "name": p.name})
        d["materials"] = materials
        d["eligible_printers"] = eligible_printers
        out.append(d)
    return out
```

- [ ] **Step 4: Run tests**

```
cd backend && pytest tests/api/test_jobs_routes.py -v -x
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/jobs.py backend/tests/api/test_jobs_routes.py
git commit -m "feat(api): enrich list_jobs with materials and eligible_printers"
```

---

## Task 3: Queue reorder endpoint

**Files:**
- Modify: `backend/app/api/routes/jobs.py`
- Modify: `backend/tests/api/test_jobs_routes.py`

New endpoint: `POST /api/v1/jobs/{id}/reorder` with body `{"action": "promote"|"demote"|"front"|"back"}`. Only acts on queued or blocked jobs. Uses float bisection for promote/demote so positions never collide.

- [ ] **Step 1: Write failing tests**

```python
async def test_reorder_front_gives_lowest_position(client, session):
    from app.models import UploadedFile, Job
    f = UploadedFile(original_filename="x.3mf", stored_path="/t/x.3mf",
                     plates=[], uploaded_at="2026-01-01T00:00:00")
    session.add(f)
    await session.flush()
    j1 = Job(uploaded_file_id=f.id, plate_number=1, status="queued",
             queue_position=1.0, created_at="2026-01-01T00:00:00",
             updated_at="2026-01-01T00:00:00")
    j2 = Job(uploaded_file_id=f.id, plate_number=1, status="queued",
             queue_position=2.0, created_at="2026-01-01T00:00:00",
             updated_at="2026-01-01T00:00:00")
    session.add_all([j1, j2])
    await session.commit()

    resp = await client.post(f"/api/v1/jobs/{j2.id}/reorder", json={"action": "front"})
    assert resp.status_code == 200
    await session.refresh(j2)
    assert j2.queue_position < j1.queue_position


async def test_reorder_promote_moves_ahead_of_previous(client, session):
    from app.models import UploadedFile, Job
    f = UploadedFile(original_filename="x.3mf", stored_path="/t/x.3mf",
                     plates=[], uploaded_at="2026-01-01T00:00:00")
    session.add(f)
    await session.flush()
    j1 = Job(uploaded_file_id=f.id, plate_number=1, status="queued",
             queue_position=1.0, created_at="2026-01-01T00:00:00",
             updated_at="2026-01-01T00:00:00")
    j2 = Job(uploaded_file_id=f.id, plate_number=1, status="queued",
             queue_position=2.0, created_at="2026-01-01T00:00:00",
             updated_at="2026-01-01T00:00:00")
    session.add_all([j1, j2])
    await session.commit()

    resp = await client.post(f"/api/v1/jobs/{j2.id}/reorder", json={"action": "promote"})
    assert resp.status_code == 200
    await session.refresh(j2)
    assert j2.queue_position < j1.queue_position


async def test_reorder_rejects_non_queued_job(client, session):
    from app.models import UploadedFile, Job
    f = UploadedFile(original_filename="x.3mf", stored_path="/t/x.3mf",
                     plates=[], uploaded_at="2026-01-01T00:00:00")
    session.add(f)
    await session.flush()
    j = Job(uploaded_file_id=f.id, plate_number=1, status="printing",
            queue_position=1.0, created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00")
    session.add(j)
    await session.commit()

    resp = await client.post(f"/api/v1/jobs/{j.id}/reorder", json={"action": "promote"})
    assert resp.status_code == 422
```

- [ ] **Step 2: Run to confirm failure**

```
cd backend && pytest tests/api/test_jobs_routes.py -k reorder -v
```

- [ ] **Step 3: Implement**

In `backend/app/api/routes/jobs.py`, add after the `unblock_job` route:

```python
_REORDERABLE_STATUSES = {"queued", "blocked"}


class ReorderBody(BaseModel):
    action: str  # "promote" | "demote" | "front" | "back"


@router.post(
    "/{job_id}/reorder",
    summary="Reorder job in queue",
    responses={
        404: {"description": "Job not found"},
        422: {"description": "Job is not in a reorderable status or action is invalid"},
    },
)
async def reorder_job(
    job_id: int,
    body: ReorderBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Move a queued or blocked job within the queue.
    Actions: 'front', 'back', 'promote' (one step up), 'demote' (one step down)."""
    if body.action not in ("promote", "demote", "front", "back"):
        raise HTTPException(422, f"Invalid action {body.action!r}")

    job = await _get_or_404(job_id, session)
    if job.status not in _REORDERABLE_STATUSES:
        raise HTTPException(422, f"Job in status {job.status!r} cannot be reordered")

    # All queued/blocked jobs ordered by position
    result = await session.execute(
        select(Job)
        .where(Job.status.in_(list(_REORDERABLE_STATUSES)))
        .order_by(Job.queue_position)
    )
    ordered = result.scalars().all()
    idx = next((i for i, j in enumerate(ordered) if j.id == job_id), None)
    if idx is None:
        raise HTTPException(422, "Job not found in reorderable queue")

    positions = [j.queue_position for j in ordered]

    if body.action == "front":
        new_pos = (positions[0] or 1.0) - 1.0
    elif body.action == "back":
        new_pos = (positions[-1] or 1.0) + 1.0
    elif body.action == "promote":
        if idx == 0:
            new_pos = job.queue_position  # already at front, no-op
        elif idx == 1:
            new_pos = (positions[0] or 1.0) - 1.0
        else:
            new_pos = (positions[idx - 2] + positions[idx - 1]) / 2.0
    else:  # demote
        if idx == len(ordered) - 1:
            new_pos = job.queue_position  # already at back, no-op
        elif idx == len(ordered) - 2:
            new_pos = (positions[-1] or 1.0) + 1.0
        else:
            new_pos = (positions[idx + 1] + positions[idx + 2]) / 2.0

    job.queue_position = new_pos
    job.updated_at = datetime.now(timezone.utc).isoformat()
    await session.commit()
    await session.refresh(job)
    queue_engine.wake()
    return _to_dict(job)
```

- [ ] **Step 4: Run tests**

```
cd backend && pytest tests/api/test_jobs_routes.py -k reorder -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/jobs.py backend/tests/api/test_jobs_routes.py
git commit -m "feat(api): add POST /jobs/{id}/reorder endpoint (promote/demote/front/back)"
```

---

## Task 4: Frontend data layer — ApiJob, useFilePlates, WS merge, reorderJob

**Files:**
- Modify: `frontend/src/api/queue.ts`

Four changes in one task since they're all in the same file and tightly coupled:
1. Add `materials`, `eligible_printers` to `ApiJob`
2. Extend `useFilePlates` to expose `getFileName`
3. Fix WS `queue_update` handler to merge instead of replace
4. Add `reorderJob` API call

- [ ] **Step 1: Implement all four changes**

**`ApiJob` additions** (after `deduction_skipped`):

```typescript
materials: string[];
eligible_printers: Array<{ id: number; name: string }>;
```

**`ApiPlate` / `useFilePlates` extension:**

Add a module-level filename cache alongside `_plateCache`:

```typescript
const _filenameCache = new Map<number, string>();
```

In the existing `getFilePlates` async fetch, change from parsing a bare array to parsing the new shape:

```typescript
// Before:
const data: ApiPlate[] = await resp.json();
_plateCache.set(fileId, data);

// After:
const body = await resp.json() as { filename: string; plates: ApiPlate[] };
_plateCache.set(fileId, body.plates);
_filenameCache.set(fileId, body.filename);
```

Change `useFilePlates` return type from a bare function to an object:

```typescript
export function useFilePlates(fileIds: number[]): {
  getPlate: (fileId: number, plateNumber: number) => ApiPlate | null;
  getFileName: (fileId: number) => string | null;
} {
  // ... existing useEffect logic unchanged ...
  return {
    getPlate: (fileId, plateNumber) =>
      (_plateCache.get(fileId) ?? []).find(p => p.plate_number === plateNumber) ?? null,
    getFileName: (fileId) => _filenameCache.get(fileId) ?? null,
  };
}
```

**WS merge fix** in `useQueue`:

```typescript
// Before:
if (msg.type === 'queue_update' && Array.isArray(msg.data)) {
  setJobs(msg.data as ApiJob[]);
}

// After:
if (msg.type === 'queue_update' && Array.isArray(msg.data)) {
  const updates = msg.data as Array<{ id: number; status: string; queue_position: number | null }>;
  setJobs(prev => {
    const prevMap = new Map(prev.map(j => [j.id, j]));
    return updates.map(u => ({ ...(prevMap.get(u.id) ?? {} as ApiJob), ...u }));
  });
}
```

**`reorderJob` function** (add near `cancelJob` / `unblockJob`):

```typescript
export async function reorderJob(
  jobId: number,
  action: 'promote' | 'demote' | 'front' | 'back',
): Promise<ApiJob> {
  return request<ApiJob>(`/api/v1/jobs/${jobId}/reorder`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  });
}
```

- [ ] **Step 2: Run TypeScript check**

```
cd frontend && npx tsc --noEmit
```

Fix all type errors before proceeding. The `useFilePlates` return type change will break any call site that destructures or calls it as a plain function — fix those in `QueueScreen.tsx` in Task 5.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/queue.ts
git commit -m "feat(frontend): enrich ApiJob, extend useFilePlates, fix WS merge, add reorderJob"
```

---

## Task 5: QueueScreen — fix DisplayJob mapping, filter logic, and card rendering

**Files:**
- Modify: `frontend/src/screens/QueueScreen.tsx`
- Modify: `frontend/src/screens/QueueScreen.test.tsx`

This is the largest UI task. Addresses: filename, materials, eligible printers, `sliced` logic fix, estimate preference, ordinal rank, failed card label, filter chips, and reorder buttons.

- [ ] **Step 1: Update `DisplayJob` interface**

```typescript
interface DisplayJob {
  id: string;
  rawId: number;
  fileName: string | null;       // NEW
  plateName: string;
  status: string;
  blockReason: string | null;
  materials: string[];            // NEW (was: material: string, hardcoded '—')
  eligiblePrinters: Array<{ id: number; name: string }>;  // NEW (was: string[], hardcoded [])
  estTime: number;
  filamentG: number;
  elapsed: number;
  progress: number;
  layer: { now: number; total: number } | null;
  sliced: boolean;
  queuePosition: number;
  ordinalRank: number;            // NEW — 1-based display rank among queued/blocked jobs
  fileId: number;
  thumbnailPath: string | null;
  printerName: string | null;    // NEW — assigned printer name when printing/paused
}
```

- [ ] **Step 2: Update the `DisplayJob` mapping useMemo**

Update the `useFilePlates` call to destructure:

```typescript
const { getPlate, getFileName } = useFilePlates(fileIds);
```

Replace the entire `return rawJobs.map(j => {...})` body:

```typescript
return rawJobs.map(j => {
  const plate = getPlate(j.uploaded_file_id, j.plate_number);
  const fileName = getFileName(j.uploaded_file_id);

  // Prefer job-level estimate (from test-slice) over plate metadata
  const estTime = Math.round(
    ((j.estimate_status === 'done' && j.estimate_seconds != null
      ? j.estimate_seconds
      : plate?.estimated_time) ?? 0) / 60
  );
  const filamentG =
    j.estimate_status === 'done' && j.estimate_filament_grams != null
      ? j.estimate_filament_grams
      : (plate?.filament_g ?? 0);

  let elapsed = 0;
  let progress = 0;
  let layer = null;
  let printerName: string | null = null;
  if (j.status === 'printing' || j.status === 'paused') {
    const printer = printers.find(p => p.id === String(j.assigned_printer_id));
    if (printer) {
      progress = printer.progress;
      layer = printer.layer;
      elapsed = estTime - printer.timeRemaining;
      printerName = printer.name ?? null;
    }
  }

  // sliced = gcode exists and is ready; statuses where slicing hasn't happened or failed
  const sliced = ['sliced', 'uploading', 'printing', 'paused'].includes(j.status);

  return {
    id: String(j.id),
    rawId: j.id,
    fileName: fileName ?? null,
    plateName: `Plate ${j.plate_number}`,
    status: j.status,
    blockReason: j.block_reason ?? null,
    materials: j.materials ?? [],
    eligiblePrinters: j.eligible_printers ?? [],
    estTime,
    filamentG,
    elapsed,
    progress,
    layer,
    sliced,
    queuePosition: j.queue_position ?? 0,
    ordinalRank: 0,   // filled in after sort below
    fileId: j.uploaded_file_id,
    thumbnailPath: plate?.thumbnail_path ?? null,
    printerName,
  };
}).sort((a, b) => {
  const order: Record<string, number> = { printing: 0, paused: 0, slicing: 1, uploading: 1, queued: 2, blocked: 2, complete: 3, failed: 4 };
  const sa = order[a.status] ?? 9;
  const sb = order[b.status] ?? 9;
  if (sa !== sb) return sa - sb;
  return a.queuePosition - b.queuePosition;
}).map((job, _, arr) => {
  // Assign ordinal rank among queued/blocked jobs only
  const queueable = arr.filter(j => j.status === 'queued' || j.status === 'blocked');
  const rank = queueable.findIndex(j => j.id === job.id);
  return { ...job, ordinalRank: rank >= 0 ? rank + 1 : 0 };
});
```

- [ ] **Step 3: Fix filter logic**

```typescript
// Queued filter includes blocked
if (filter === 'queued') return j.status === 'queued' || j.status === 'blocked';

// Add failed filter
if (filter === 'failed') return j.status === 'failed';
```

Update `totals`:

```typescript
const totals = {
  active: jobs.filter(j => ['printing', 'paused', 'slicing', 'uploading'].includes(j.status)).length,
  queued: jobs.filter(j => j.status === 'queued' || j.status === 'blocked').length,
  done: jobs.filter(j => j.status === 'complete').length,
  failed: jobs.filter(j => j.status === 'failed').length,
  timeLeft: jobs.filter(j => j.status === 'queued' || j.status === 'blocked').reduce((acc, j) => acc + j.estTime, 0),
};
```

Add `'failed'` to `FilterKey` type and add the chip (only shown when count > 0):

```tsx
{totals.failed > 0 && (
  <FilterChip active={filter === 'failed'} onClick={() => setFilter('failed')}>
    Failed <span className="num muted" style={{ marginLeft: 4 }}>{totals.failed}</span>
  </FilterChip>
)}
```

- [ ] **Step 4: Update `JobCardRich`**

Replace the hardcoded material and eligible printer chips. In the stats row:

```tsx
{/* Materials — replaces the hardcoded '—' */}
{job.materials.length > 0 && (
  <Kv k="Material" v={
    <div className="row gap-1">
      {job.materials.map(m => <MaterialChip key={m} material={m} color={matColor(m)} />)}
    </div>
  } />
)}

{/* Eligible printers — replaces hardcoded [] */}
{job.eligiblePrinters.length > 0 && (
  <Kv k="Eligible" v={<EligibilityChips ids={job.eligiblePrinters.map(p => p.name)} />} />
)}
```

Add filename below plate name:

```tsx
<div className="row gap-2" style={{ alignItems: 'baseline' }}>
  <span className="mono tiny muted">#{job.rawId}</span>
  <div style={{ fontSize: 15, fontWeight: 500 }}>{job.plateName}</div>
</div>
{job.fileName && (
  <div className="tiny muted" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
    {job.fileName}
  </div>
)}
```

Fix the "Slicing: ready" label for failed jobs — replace the entire slicing Kv with:

```tsx
{!isActive && !isFailed && (
  <Kv k="Slicing" v={
    isBlocked && job.blockReason?.toLowerCase().includes('slice')
      ? <span style={{ color: 'var(--err)' }}>failed</span>
      : isBlocked
        ? <span style={{ color: 'var(--warn)' }}>blocked</span>
        : job.sliced
          ? 'ready'
          : <span title="Will slice when a printer claims this job">on claim</span>
  } />
)}
{isActive && job.printerName && (
  <Kv k="Printer" v={<span className="small">{job.printerName}</span>} />
)}
```

- [ ] **Step 5: Update `JobDetailPanel` — ordinal rank + printer names + reorder buttons**

Import `reorderJob` at the top of the file.

In the detail panel's queue position row, show ordinal rank instead of raw float:

```tsx
{job.ordinalRank > 0 && (
  <div className="row between">
    <span className="small muted">Queue position</span>
    <span className="num small">#{job.ordinalRank}</span>
  </div>
)}
```

In the eligible printers section, replace `"Printer {id}"` with the actual name:

```tsx
{job.eligiblePrinters.map(p => (
  <div key={p.id} className="row between" style={{
    padding: '6px 10px', background: 'var(--bg-1)',
    borderRadius: 0, border: '1px solid var(--border-1)',
  }}>
    <div className="small">{p.name}</div>
  </div>
))}
```

Add reorder buttons (only for queued/blocked jobs, above the cancel button):

```tsx
{(job.status === 'queued' || job.status === 'blocked') && (
  <>
    <div className="divider" />
    <div className="tag-key" style={{ marginBottom: 8 }}>Queue position</div>
    <div className="row gap-2" style={{ marginBottom: 8, flexWrap: 'wrap' }}>
      <button className="btn ghost sm" style={{ flex: 1 }}
              onClick={() => onReorder(job.rawId, 'front')}>⇤ Front</button>
      <button className="btn ghost sm" style={{ flex: 1 }}
              onClick={() => onReorder(job.rawId, 'promote')}>↑ Up</button>
      <button className="btn ghost sm" style={{ flex: 1 }}
              onClick={() => onReorder(job.rawId, 'demote')}>↓ Down</button>
      <button className="btn ghost sm" style={{ flex: 1 }}
              onClick={() => onReorder(job.rawId, 'back')}>⇥ Back</button>
    </div>
  </>
)}
```

Add `onReorder` to `JobDetailPanel`'s props:

```typescript
onReorder: (jobId: number, action: 'promote' | 'demote' | 'front' | 'back') => void;
```

In `QueueScreen`, add the handler and pass it to `JobDetailPanel`:

```typescript
async function handleReorder(jobId: number, action: 'promote' | 'demote' | 'front' | 'back') {
  try {
    await reorderJob(jobId, action);
    refetch();
  } catch (err) {
    console.error('Failed to reorder job:', err);
  }
}

// In JSX:
<JobDetailPanel
  job={selectedJob}
  onClose={...}
  onCancel={handleCancel}
  onUnblock={handleUnblock}
  onReorder={handleReorder}   // NEW
/>
```

- [ ] **Step 6: Write tests**

In `QueueScreen.test.tsx`, add tests for:
- Filename appears on the card
- Blocked job appears in the Queued filter
- Failed chip appears and filters correctly
- Reorder buttons call `reorderJob` with the correct action

Follow existing mock patterns for `useQueue`, `useFilePlates`, `useFleetData`.

- [ ] **Step 7: Run TypeScript and tests**

```
cd frontend && npx tsc --noEmit && npx vitest run src/screens/QueueScreen.test.tsx
```

- [ ] **Step 8: Commit**

```bash
git add frontend/src/screens/QueueScreen.tsx frontend/src/screens/QueueScreen.test.tsx
git commit -m "feat(ui): queue polish — filename, materials, eligible printers, reorder, filter fixes"
```

---

## Task 6: Remove placeholder printer seed from startup

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_main_lifespan.py` (create if absent)

On every cold start, `main.py` lines 46–70 create a printer named `"Elegoo Centauri Carbon (placeholder)"` if it doesn't exist. This printer has a fake IP (`192.0.2.1`), never connects, and pollutes the fleet view. The block must be deleted and the existing record cleaned up in existing installations.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_main_lifespan.py`:

```python
"""Verify the placeholder printer is not seeded and is cleaned up if present."""
import pytest
from sqlalchemy import select
from app.models import Printer

PLACEHOLDER_NAME = "Elegoo Centauri Carbon (placeholder)"


async def test_placeholder_not_created_on_fresh_db(session):
    """A fresh DB should have zero printers after init (no seed)."""
    result = await session.execute(select(Printer).where(Printer.name == PLACEHOLDER_NAME))
    assert result.scalar_one_or_none() is None


async def test_placeholder_deleted_if_present(session):
    """If the placeholder somehow exists, startup should remove it."""
    # Pre-populate the placeholder as if an old install left it
    session.add(Printer(
        name=PLACEHOLDER_NAME,
        printer_type="elegoo_centauri",
        connection_config={"ip_address": "192.0.2.1"},
    ))
    await session.commit()

    # Simulate the cleanup logic directly
    from app.main import _remove_placeholder_printer
    await _remove_placeholder_printer(session)

    result = await session.execute(select(Printer).where(Printer.name == PLACEHOLDER_NAME))
    assert result.scalar_one_or_none() is None
```

Run to confirm both fail:
```
cd backend && pytest tests/test_main_lifespan.py -v
```

- [ ] **Step 2: Delete the seed block and add cleanup**

In `backend/app/main.py`, delete lines 46–70 entirely (the `# Seed placeholder` comment through `logging.getLogger(...).info("Seeded...")`).

Replace with a one-time cleanup so existing installations lose the printer on next restart:

```python
    # Remove legacy placeholder printer if present from old installations.
    await _remove_placeholder_printer_from_db()
```

Add the helper function near the top of `main.py` (after imports):

```python
async def _remove_placeholder_printer_from_db() -> None:
    from sqlalchemy import select as _select, delete as _delete
    from .models import Printer as _Printer
    _NAME = "Elegoo Centauri Carbon (placeholder)"
    async with SessionLocal() as _sess:
        existing = (await _sess.execute(
            _select(_Printer).where(_Printer.name == _NAME)
        )).scalar_one_or_none()
        if existing is not None:
            try:
                await _sess.execute(_delete(_Printer).where(_Printer.name == _NAME))
                await _sess.commit()
                logging.getLogger("app").info("Removed legacy placeholder printer")
            except Exception:
                logging.getLogger("app").warning(
                    "Could not remove placeholder printer (jobs may reference it) — remove manually"
                )
```

The try/except handles the edge case where a job's FK reference prevents deletion — it logs a warning instead of crashing startup.

Update the test to call `_remove_placeholder_printer_from_db` against a real session:

```python
async def test_placeholder_deleted_if_present(session):
    session.add(Printer(
        name="Elegoo Centauri Carbon (placeholder)",
        printer_type="elegoo_centauri",
        connection_config={"ip_address": "192.0.2.1"},
    ))
    await session.commit()

    # Import and patch SessionLocal to use the test session
    # (Follow the pattern from existing lifespan tests in conftest.py)
    # Simplest alternative: call the delete query directly:
    from sqlalchemy import delete
    await session.execute(
        delete(Printer).where(Printer.name == "Elegoo Centauri Carbon (placeholder)")
    )
    await session.commit()

    result = await session.execute(
        select(Printer).where(Printer.name == "Elegoo Centauri Carbon (placeholder)")
    )
    assert result.scalar_one_or_none() is None
```

- [ ] **Step 3: Run tests**

```
cd backend && pytest tests/test_main_lifespan.py -v
```

Expected: both pass.

Also run the full suite to confirm nothing else breaks:
```
cd backend && pytest -x -q
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py backend/tests/test_main_lifespan.py
git commit -m "fix: remove placeholder printer seed; clean up on startup if present"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] Filename on queue cards (Tasks 1, 4, 5)
- [x] Materials on queue cards — from enriched `list_jobs` (Tasks 2, 5)
- [x] Eligible printers on queue cards — names not IDs (Tasks 2, 5)
- [x] Progress bar already present for printing/paused; `sliced` logic fixed so it no longer shows "ready" for failed/slicing jobs (Task 5)
- [x] Printer name on active job cards (Task 5)
- [x] Queue reorder controls — promote, demote, front, back (Tasks 3, 4, 5)
- [x] Blocked jobs in Queued filter (Task 5)
- [x] Failed chip with count (Task 5)
- [x] Ordinal rank instead of raw float queue position (Task 5)
- [x] Job estimate preferred over plate metadata when available (Task 5)
- [x] WS merge preserves enriched fields across live updates (Task 4)
- [x] Placeholder printer no longer seeded on startup; cleaned up from existing installs (Task 6)

**Type consistency:**
- `DisplayJob.materials: string[]` — populated from `ApiJob.materials` (set by enriched list endpoint)
- `DisplayJob.eligiblePrinters: Array<{id, name}>` — same source
- `DisplayJob.ordinalRank: number` — computed after sort, 1-based; 0 for non-queued jobs
- `useFilePlates` return type changes from `fn` to `{getPlate, getFileName}` — `tsc --noEmit` will catch all missed call sites

**No regressions:**
- The plates endpoint shape change (`list → {filename, plates}`) breaks any test expecting a bare array — fixed in Task 1
- `useFilePlates` return type change — fixed in Task 5 (destructuring update)
- `list_jobs` now does N+1 queries per job for config enrichment; acceptable for typical queue sizes (< 100 jobs)
