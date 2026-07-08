# Project Generate Workflow — Implementation Plan

**Date:** 2026-07-04  
**Status:** Pending approval  
**Scope:** Themis backend + frontend. Orca sidecar UUID-based pack + template cache already ship in Orca.  
**Repos:** Changes land in `../themis` (backend + frontend) and `../orca` (if sidecar pack endpoint needs updates). No omnibus changes required.

---

## Goal

From the project builder, a user selects STL library files, assigns filament profiles + colors, sets quantities, then clicks **Generate**. Themis packs the models one 3MF per filament group via Orca, saves them to the library, and queues one job per plate. Generate is the only entry point for pack/assemble.

---

## What Already Exists — reused verbatim

| Code | Location | Reused for |
|---|---|---|
| `assemble_project` file-save block (write bytes, insert `UploadedFile`, parse plates, `regen_file_thumbnails`) | `routes/projects.py:470–520` | Same block, looped per filament group |
| Old result-file cleanup (delete old `result_file_id` if no active jobs) | same | Runs before each generate, same logic |
| `_get_catalog`, `_resolve_filament_name`, slot-map loop | same | Filament display names, type lookup |
| `_get_project_or_404`, `_now_iso`, `_slugify`, `_load_items` | same | Unchanged |
| `OrcaSidecarClient.__init__`, file-handle loop in `pack_stls` | `services/orca_sidecar_client.py` | Copied pattern into `pack_stls_by_uuid` |
| `LibraryScanner.unique_path`, `sha256_file`, `folder_of` | `services/library_scanner.py` | Same as today |
| `regen_file_thumbnails` background task | `services/thumbnail_regen.py` | Same |

---

## Changes

### 1. `OrcaSidecarClient` — one new method

**File:** `themis/backend/app/services/orca_sidecar_client.py`

Add `pack_stls_by_uuid` — identical structure to existing `pack_stls`, just swaps
`bed_x`/`bed_y` form fields for `machine_uuid`/`process_uuid`/`filament_uuids`:

```python
def pack_stls_by_uuid(
    self,
    stl_paths: list[Path],
    machine_uuid: str,
    process_uuid: str,
    filament_uuids: list[str],
) -> bytes:
    """POST /api/pack (UUID mode) → multi-plate 3MF bytes with embedded settings."""
    import json as _json
    file_handles = [(p, open(p, "rb")) for p in stl_paths]
    try:
        files = [("files", (p.name, fh, "application/octet-stream")) for p, fh in file_handles]
        data = {
            "machine_uuid": machine_uuid,
            "process_uuid": process_uuid,
            "filament_uuids": _json.dumps(filament_uuids),
        }
        try:
            r = self._client.post("/api/pack", files=files, data=data)
        except httpx.HTTPError as e:
            raise SidecarError(f"pack request failed: {e}") from e
    finally:
        for _, fh in file_handles:
            fh.close()
    if r.status_code != 200:
        raise SidecarError(f"pack returned {r.status_code}: {r.text[:300]}")
    return r.content
```

---

### 2. `routes/projects.py` — rename + rewrite `assemble_project`

**Route:** `POST /{project_id}/assemble` → `POST /{project_id}/generate`

**Remove imports:** `ProjectPackBuilder`, `FilamentSlot` (no longer used).

**Logic diff from current `assemble_project`:**

| Old | New |
|---|---|
| Build `slot_map` → `ProjectPackBuilder.build()` → `client.arrange()` → one 3MF | Group items by `(filament_profile_uuid, color_hex)` → loop: `client.pack_stls_by_uuid()` per group → N 3MFs |
| Save one file; update `proj.result_file_id` | Loop: save each file the same way; clear `proj.result_file_id` after (no single result file now) |
| Return `{result_file_id, plate_count, file}` | Create `Job` per plate per file; return `{jobs: [...], files: [...]}` |

**Old result-file cleanup** (lines ~462–479) is kept and runs before the loop to evict the single previous result if unneeded. Multi-run accumulation of generated files is a known tradeoff; acceptable until a regeneration story is fleshed out.

**Job creation** (new, inside the per-group loop after plate parse):

```python
# ponytail: queue_position = max existing + 1 per job; no re-sort needed at this scale
next_pos = await _max_queue_position(session) + 1.0
for plate_num in plate_nums:
    job = Job(
        uploaded_file_id=new_file.id,
        plate_number=plate_num,
        queue_position=next_pos,
        status="queued",
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    next_pos += 1.0
    created_jobs.append({"plate_number": plate_num, "file_id": new_file.id})
```

`_max_queue_position` is a one-liner:
```python
async def _max_queue_position(session: AsyncSession) -> float:
    result = await session.execute(select(func.max(Job.queue_position)))
    return result.scalar_one_or_none() or 0.0
```

**Filename per group** (replaces single `project-{slug}.3mf`):
```python
label = color_hex.lstrip("#") if color_hex else fil_uuid[:8]
out_filename = f"project-{_slugify(proj.name)}-{label}.3mf"
```

**`machine_uuid` / `bed_x` / `bed_y`:** The catalog lookup for bed dimensions (lines ~385–395) is removed — Orca resolves those from the machine profile internally when using UUID mode. The catalog is still fetched for filament display names only.

---

### 3. `routes/files.py` — delete the pack endpoint

`POST /api/v1/files/pack` and its `PackRequest` schema are not called from the frontend (not present in `api/files.ts`). Delete both. Also remove the `OrcaSidecarClient` + `SidecarError` import from `files.py` (they are only used there by pack).

---

### 4. Frontend — three additions to `ProjectBuilderScreen`

**Files:** `frontend/src/screens/ProjectBuilderScreen.tsx`, `frontend/src/api/projects.ts`

#### 4a. `generateProject` in `api/projects.ts`

```ts
export const generateProject = (id: number) =>
  fetch(`/api/v1/projects/${id}/generate`, { method: "POST" })
    .then(r => r.ok ? r.json() : r.json().then(e => Promise.reject(e)));
```

#### 4b. Library picker modal

An **"Add Models"** button opens a `<dialog>` (native, no library):
- Calls existing `getFiles()` from `api/files.ts`; shows list filtered to `.stl` client-side
- Multi-select checkboxes → confirm → for each selected file, calls existing `addProjectItem`
- Inline qty / filament / color inputs before confirm (see 4c)

#### 4c. Per-item filament/color row

Each item row gets two new fields:
- **Filament** — `<select>` populated from the orca catalog already fetched for machine/process pickers. Saves via existing `updateProjectItem`
- **Color** — `<input type="color">`. Saves via same.

#### 4d. Generate button

```tsx
const [generating, setGenerating] = useState(false);

const handleGenerate = async () => {
  setGenerating(true);
  try {
    await generateProject(project.id);
    navigate("/queue");
  } catch (e) {
    // show error toast — reuse whatever error display pattern the screen already uses
  } finally {
    setGenerating(false);
  }
};
```

Button disabled when `generating || items.length === 0`.

---

## File Changelist

| File | Change |
|---|---|
| `themis/backend/app/services/orca_sidecar_client.py` | Add `pack_stls_by_uuid()` |
| `themis/backend/app/api/routes/projects.py` | Rename route `/assemble` → `/generate`; rewrite body; add `_max_queue_position`; remove `ProjectPackBuilder`/`FilamentSlot` imports |
| `themis/backend/app/api/routes/files.py` | Delete `PackRequest` + `pack_files` endpoint; remove sidecar client import |
| `themis/frontend/src/api/projects.ts` | Add `generateProject(id)` |
| `themis/frontend/src/screens/ProjectBuilderScreen.tsx` | Add library picker dialog, per-item filament/color inputs, Generate button |

No schema migrations. No new dependencies.

---

## Scope Limits

| Not in scope | Add when |
|---|---|
| Printer/filament assignment on generated jobs | Queue screen already handles this |
| Cleanup of previous generate's files on re-generate | Add a `generated_file_ids: JSON` column if re-generate churn becomes a real problem |
| Multi-filament-per-group (AMS multi-color) | Items in a group share one filament UUID by definition; multi-color = multiple groups |
| Progress stream during generate | Add polling/SSE if generate takes >10s in practice |
