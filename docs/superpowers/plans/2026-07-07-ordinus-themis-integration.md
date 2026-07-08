# Ordinus → Themis Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Send to Themis" flow that uploads Ordinus BOM STLs to the Themis library, creates a project with correct quantities and bidirectional linking, and opens the project in a new tab — with the link persisting across page reloads.

**Architecture:** Two repos change independently — Themis gets dedup-on-upload, optional project fields, and three new source columns on `Project`; Ordinus gets `themisProjectId` on `bom_generations`, a Themis HTTP client, one new endpoint that looks up the caller's username and writes both sides of the link, and a frontend button that pre-populates from the DB on load. Tasks 1–2 are in `themis`; Tasks 3–7 are in `gridfinity-customizer`. Each task commits to its own repo.

**Tech Stack:** Python/FastAPI/SQLAlchemy (Themis); Node 24/Express/TypeScript + React/Vite (Ordinus)

---

## Task 1 — Themis: dedup on upload

**Repos:** `C:\Users\mgome\Documents\projects\themis`

**Files:**
- Modify: `backend/app/api/routes/files.py` — `upload_file` function
- Test: `backend/tests/api/test_files_api.py`

### Background

`UploadedFile.content_hash` (SHA-256 hex) is already populated on every upload.  
`upload_file` currently calls `await file.read()` once via `dest.write_bytes(await file.read())`.  
We need to read the bytes first, check for a duplicate in the same target folder, and either return early or write.

The `folder` field stored in the DB is the POSIX-relative path of the containing directory, e.g. `Gridfinity/my-layout` (no leading slash). We derive the same string from `folder_abs.relative_to(library).as_posix()` before any file is written.

- [ ] **Step 1: Write two failing tests**

Append to `backend/tests/api/test_files_api.py`:

```python
import hashlib


def _make_stl_bytes(seed: bytes = b"stl") -> bytes:
    """Minimal binary STL — 84 bytes (header + triangle count = 0)."""
    return seed.ljust(80, b" ") + b"\x00\x00\x00\x00"


async def test_upload_dedup_same_folder_returns_existing(client, tmp_path):
    """Uploading the same bytes to the same folder twice returns the existing record."""
    lib, cache = _patch_dirs(tmp_path)
    stl = _make_stl_bytes()
    with lib, cache:
        r1 = await client.post(
            "/api/v1/files/upload",
            data={"folder": "/Gridfinity/layout-a"},
            files={"file": ("bin_2x3.stl", stl, "application/octet-stream")},
        )
        r2 = await client.post(
            "/api/v1/files/upload",
            data={"folder": "/Gridfinity/layout-a"},
            files={"file": ("bin_2x3.stl", stl, "application/octet-stream")},
        )
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]
    # Only one file written to disk
    stl_files = list((tmp_path / "library" / "Gridfinity" / "layout-a").glob("*.stl"))
    assert len(stl_files) == 1


async def test_upload_dedup_different_folder_creates_separate_record(client, tmp_path):
    """Same bytes in a different folder get a new record — no cross-folder dedup."""
    lib, cache = _patch_dirs(tmp_path)
    stl = _make_stl_bytes()
    with lib, cache:
        r1 = await client.post(
            "/api/v1/files/upload",
            data={"folder": "/Gridfinity/layout-a"},
            files={"file": ("bin_2x3.stl", stl, "application/octet-stream")},
        )
        r2 = await client.post(
            "/api/v1/files/upload",
            data={"folder": "/Gridfinity/layout-b"},
            files={"file": ("bin_2x3.stl", stl, "application/octet-stream")},
        )
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd C:\Users\mgome\Documents\projects\themis\backend
python -m pytest tests/api/test_files_api.py::test_upload_dedup_same_folder_returns_existing tests/api/test_files_api.py::test_upload_dedup_different_folder_creates_separate_record -v
```

Expected: both FAIL (second upload creates a new record instead of returning the existing one).

- [ ] **Step 3: Implement dedup in `upload_file`**

In `backend/app/api/routes/files.py`, add `import hashlib` at the top alongside existing imports.

Replace the `upload_file` function body:

```python
@router.post("/upload", status_code=201)
async def upload_file(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    folder: str = Form("/Job Uploads"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    fname = (file.filename or "")
    ext = Path(fname).suffix.lower()
    if ext not in MODEL_EXTS:
        raise HTTPException(422, "Only .3mf and .stl files are accepted")

    raw = await file.read()
    incoming_hash = hashlib.sha256(raw).hexdigest()

    library = config.get_library_dir()
    folder_abs = _safe_subpath(library, folder)
    folder_abs.mkdir(parents=True, exist_ok=True)
    target_folder = folder_abs.relative_to(library).as_posix()

    # Dedup: return existing record if same content is already in this folder.
    existing = (await session.execute(
        select(UploadedFile)
        .where(UploadedFile.content_hash == incoming_hash)
        .where(UploadedFile.folder == target_folder)
        .limit(1)
    )).scalar_one_or_none()
    if existing:
        return _to_dict(existing, [])

    dest = LibraryScanner.unique_path(folder_abs, Path(fname).name)
    dest.write_bytes(raw)

    rel = dest.relative_to(library).as_posix()
    stat = dest.stat()
    scanner = LibraryScanner(session, library, config.get_filecache_dir())
    record = UploadedFile(
        original_filename=dest.name, stored_path=str(dest), relative_path=rel,
        folder=folder_of(rel), size_bytes=stat.st_size, content_hash=sha256_file(dest),
        mtime=stat.st_mtime, plates=[], missing=False,
        uploaded_at=datetime.now(timezone.utc).isoformat(),
    )
    session.add(record)
    await session.flush()
    record.plates = scanner._parse_plates(dest, record.id)
    await session.commit()
    await session.refresh(record)
    if dest.suffix.lower() == ".3mf":
        background_tasks.add_task(regen_file_thumbnails, record.id)
    return _to_dict(record, [])
```

- [ ] **Step 4: Run all tests**

```bash
cd C:\Users\mgome\Documents\projects\themis\backend
python -m pytest -v
```

Expected: 456 passed, 0 failed.

- [ ] **Step 5: Commit**

```bash
git -C C:\Users\mgome\Documents\projects\themis checkout -b feat/ordinus-themis-integration
git -C C:\Users\mgome\Documents\projects\themis add backend/app/api/routes/files.py backend/tests/api/test_files_api.py
git -C C:\Users\mgome\Documents\projects\themis commit -m "feat(files): dedup upload within target folder by content hash"
```

---

## Task 2 — Themis: project source fields + optional machine/process

**Repos:** `C:\Users\mgome\Documents\projects\themis`

**Files:**
- Modify: `backend/app/models.py` — add `source_app`, `source_user`, `source_layout_id` to `Project`; make `machine_uuid`/`process_uuid` nullable
- Modify: `backend/app/database.py` — add `projects` entry to `_ALTERS`
- Modify: `backend/app/api/routes/projects.py` — update `ProjectCreate`, `create_project`, `_project_dict`
- Create: `backend/tests/api/test_projects_api.py`

### Background

Three new nullable columns are added to `projects` for bidirectional traceability: `source_app` (e.g. `"ordinus"`), `source_user` (Ordinus username), `source_layout_id` (Ordinus layout pk). They are populated by the caller at creation time and returned in every project response.

`machine_uuid` and `process_uuid` are required strings today. External callers (Ordinus) don't know slicer profile UUIDs, so the project must be creatable without them. The SQLAlchemy model must be updated to `Optional[str]` with `nullable=True`; the DB migration needs to handle existing databases where these columns were created as NOT NULL. We follow the same `CREATE TABLE ... _new / INSERT INTO / DROP / RENAME` pattern used in `database.py` for `job_printer_configs`.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/api/test_projects_api.py`:

```python
import pytest


async def test_create_project_without_machine_process(client):
    """Projects can be created without machine_uuid / process_uuid for external importers."""
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Ordinus Import"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Ordinus Import"
    assert data["machine_uuid"] is None
    assert data["process_uuid"] is None


async def test_create_project_with_source_fields(client):
    """Source fields are stored and returned on the project."""
    resp = await client.post(
        "/api/v1/projects",
        json={
            "name": "Ordinus Import",
            "source_app": "ordinus",
            "source_user": "alice",
            "source_layout_id": 42,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["source_app"] == "ordinus"
    assert data["source_user"] == "alice"
    assert data["source_layout_id"] == 42


async def test_create_project_source_fields_default_null(client):
    """Source fields are null when not provided."""
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Regular Project", "machine_uuid": "abc", "process_uuid": "def"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["source_app"] is None
    assert data["source_user"] is None
    assert data["source_layout_id"] is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd C:\Users\mgome\Documents\projects\themis\backend
python -m pytest tests/api/test_projects_api.py -v
```

Expected: all three FAIL (422 on missing required fields; source fields not present in response).

- [ ] **Step 3: Update `Project` model in `models.py`**

In `backend/app/models.py`, replace the `Project` class:

```python
class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    machine_uuid: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    process_uuid: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_file_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("uploaded_files.id", ondelete="SET NULL"), nullable=True
    )
    source_app: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    source_user: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_layout_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))
```

(`Integer` is already imported from `sqlalchemy` at the top of `models.py`.)

- [ ] **Step 4: Add migration to `database.py`**

In `backend/app/database.py`, add a `projects` entry to `_ALTERS`:

```python
_ALTERS: list[tuple[str, list[tuple[str, str]]]] = [
    ("printers", [
        ("loaded_filaments",         "JSON DEFAULT '[]'"),
        ("queue_on",                 "BOOLEAN NOT NULL DEFAULT 1"),
        ("build_plate_type",         "VARCHAR(100)"),
        ("no_snapshots_while_idle",  "BOOLEAN NOT NULL DEFAULT 0"),
    ]),
    ("job_printer_configs", [
        ("filament_id",    "INTEGER"),
        ("filament_type",  "VARCHAR(100)"),
        ("filament_color", "VARCHAR(20)"),
        ("tool_index",     "INTEGER"),
        ("filament_map",   "JSON"),
    ]),
    ("jobs", [
        ("block_reason", "TEXT"),
        ("order_id",     "INTEGER"),
        ("overrides",    "JSON"),
    ]),
    ("uploaded_files", [
        ("relative_path", "VARCHAR(1024) DEFAULT ''"),
        ("folder",        "VARCHAR(1024) DEFAULT '/'"),
        ("size_bytes",    "INTEGER DEFAULT 0"),
        ("content_hash",  "VARCHAR(64) DEFAULT ''"),
        ("mtime",         "FLOAT DEFAULT 0"),
        ("missing",       "BOOLEAN NOT NULL DEFAULT 0"),
    ]),
    ("queue_config", [
        ("operator_name",             "VARCHAR(120)"),
        ("snapshot_interval_seconds", "INTEGER DEFAULT 2"),
    ]),
    ("projects", [
        ("source_app",       "VARCHAR(50)"),
        ("source_user",      "VARCHAR(255)"),
        ("source_layout_id", "INTEGER"),
    ]),
]
```

Then in `_migrate`, after the existing `job_printer_configs` recreation block, add logic to make `machine_uuid`/`process_uuid` nullable in existing databases:

```python
    # machine_uuid/process_uuid changed to nullable; SQLite can't ALTER COLUMN so recreate.
    proj_info = (await conn.execute(text("PRAGMA table_info(projects)"))).fetchall()
    proj_cols = {row[1]: row[3] for row in proj_info}  # col_name → notnull
    if proj_cols.get("machine_uuid", 0) == 1 or proj_cols.get("process_uuid", 0) == 1:
        await conn.execute(text("""
            CREATE TABLE projects_new (
                id INTEGER NOT NULL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                machine_uuid VARCHAR(36),
                process_uuid VARCHAR(36),
                notes TEXT,
                result_file_id INTEGER REFERENCES uploaded_files (id) ON DELETE SET NULL,
                source_app VARCHAR(50),
                source_user VARCHAR(255),
                source_layout_id INTEGER,
                created_at VARCHAR(32) NOT NULL,
                updated_at VARCHAR(32) NOT NULL
            )
        """))
        await conn.execute(text(
            "INSERT INTO projects_new "
            "(id, name, machine_uuid, process_uuid, notes, result_file_id, created_at, updated_at) "
            "SELECT id, name, machine_uuid, process_uuid, notes, result_file_id, created_at, updated_at "
            "FROM projects"
        ))
        await conn.execute(text("DROP TABLE projects"))
        await conn.execute(text("ALTER TABLE projects_new RENAME TO projects"))
```

Place this block at the END of `_migrate`, after the `job_printer_configs` block.

- [ ] **Step 5: Update `ProjectCreate`, `create_project`, and `_project_dict` in `routes/projects.py`**

Update `ProjectCreate`:

```python
class ProjectCreate(BaseModel):
    name: str
    machine_uuid: Optional[str] = None
    process_uuid: Optional[str] = None
    notes: Optional[str] = None
    source_app: Optional[str] = None
    source_user: Optional[str] = None
    source_layout_id: Optional[int] = None
```

Update `create_project` to pass source fields to `Project()`:

```python
@router.post("", status_code=201)
async def create_project(
    body: ProjectCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    now = _now_iso()
    proj = Project(
        name=body.name,
        machine_uuid=body.machine_uuid,
        process_uuid=body.process_uuid,
        notes=body.notes,
        result_file_id=None,
        source_app=body.source_app,
        source_user=body.source_user,
        source_layout_id=body.source_layout_id,
        created_at=now,
        updated_at=now,
    )
    session.add(proj)
    await session.commit()
    await session.refresh(proj)
    return await _project_dict(proj, session)
```

Update `_project_dict` to include source fields:

```python
async def _project_dict(
    project: Project,
    session: AsyncSession,
    catalog: dict | None = None,
) -> dict:
    items = await _load_items(session, project.id, catalog)
    return {
        "id": project.id,
        "name": project.name,
        "machine_uuid": project.machine_uuid,
        "process_uuid": project.process_uuid,
        "notes": project.notes,
        "result_file_id": project.result_file_id,
        "source_app": project.source_app,
        "source_user": project.source_user,
        "source_layout_id": project.source_layout_id,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "items": items,
    }
```

- [ ] **Step 6: Run all tests**

```bash
cd C:\Users\mgome\Documents\projects\themis\backend
python -m pytest -v
```

Expected: 459+ passed, 0 failed.

- [ ] **Step 7: Commit**

```bash
git -C C:\Users\mgome\Documents\projects\themis add backend/app/models.py backend/app/database.py backend/app/api/routes/projects.py backend/tests/api/test_projects_api.py
git -C C:\Users\mgome\Documents\projects\themis commit -m "feat(projects): add source fields for bidirectional linking; make machine/process optional"
```

---

## Task 3 — Ordinus: THEMIS_URL config

**Repo:** `C:\Users\mgome\Documents\projects\gridfinity-customizer`

**Files:**
- Modify: `server/src/config.ts`
- Modify: `.env.example`
- Modify: `.env.development`

- [ ] **Step 1: Add THEMIS_URL to config schema**

In `server/src/config.ts`, add to the `envSchema` object before the `.superRefine`:

```typescript
THEMIS_URL: z.string().url().optional(),
```

- [ ] **Step 2: Document in .env.example**

Append to `.env.example`:

```
# Optional: Themis print farm URL for "Send to Themis" integration
# THEMIS_URL=http://localhost:8001
```

- [ ] **Step 3: Set in .env.development**

Append to `.env.development`:

```
THEMIS_URL=http://localhost:8001
```

- [ ] **Step 4: Verify tests still pass**

```bash
cd C:\Users\mgome\Documents\projects\gridfinity-customizer
npm run test:run
```

Expected: all tests pass (config change is additive).

- [ ] **Step 5: Commit**

```bash
cd C:\Users\mgome\Documents\projects\gridfinity-customizer
git checkout -b feat/ordinus-themis-integration
git add server/src/config.ts .env.example .env.development
git commit -m "feat(config): add optional THEMIS_URL for Themis integration"
```

---

## Task 4 — Ordinus: bidirectional schema

**Repo:** `C:\Users\mgome\Documents\projects\gridfinity-customizer`

**Files:**
- Modify: `server/src/db/schema.ts` — add `themisProjectId` to `bomGenerations`
- Modify: `server/src/db/migrate.ts` — add ALTER TABLE migration for new column
- Modify: `shared/src/types.ts` — add `themisProjectId: number | null` to `ApiBomGeneration`
- Modify: `server/src/services/bomGeneration.service.ts` — include `themisProjectId` in `RawGenRow` and `formatBomGeneration`

### Background

`bomGenerations.themisProjectId` stores the Themis project ID after a successful "Send to Themis". It is nullable (not every generation gets sent to Themis). Ordinus' hand-rolled migration in `migrate.ts` adds columns via `ALTER TABLE ... ADD COLUMN` in a try/catch block — same pattern as all other additive migrations in that file.

The `formatBomGeneration` function maps DB row → `ApiBomGeneration` API shape. Adding `themisProjectId` here makes it available to every existing caller (`getGeneration`, `triggerGeneration` return value, controller responses).

- [ ] **Step 1: Add `themisProjectId` to drizzle schema**

In `server/src/db/schema.ts`, update the `bomGenerations` table definition:

```typescript
export const bomGenerations = sqliteTable('bom_generations', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  layoutId: integer('layout_id').notNull().unique().references(() => layouts.id, { onDelete: 'cascade' }),
  status: text('status').notNull().default('pending'),
  exportJson: text('export_json'),
  fileManifest: text('file_manifest'),
  threeMfPath: text('three_mf_path'),
  generatedAt: text('generated_at'),
  errorMessage: text('error_message'),
  themisProjectId: integer('themis_project_id'),
});
```

- [ ] **Step 2: Add migration in `migrate.ts`**

At the end of `server/src/db/migrate.ts`, before the closing brace of `runMigrations`, add:

```typescript
  // Add themis_project_id to bom_generations if missing
  try {
    await client.execute(`ALTER TABLE bom_generations ADD COLUMN themis_project_id INTEGER;`);
  } catch {
    // Column already exists — ignore
  }
```

- [ ] **Step 3: Update `ApiBomGeneration` in `shared/src/types.ts`**

In `shared/src/types.ts`, update the `ApiBomGeneration` interface:

```typescript
export interface ApiBomGeneration {
  id: number;
  layoutId: number;
  status: BomGenerationStatus;
  fileManifest: BomGenerationManifestEntry[] | null;
  threeMfPath: string | null;
  generatedAt: string | null;
  errorMessage: string | null;
  themisProjectId: number | null;
}
```

- [ ] **Step 4: Update `RawGenRow` and `formatBomGeneration` in `bomGeneration.service.ts`**

In `server/src/services/bomGeneration.service.ts`, update the `RawGenRow` type:

```typescript
type RawGenRow = Pick<
  typeof bomGenerations.$inferSelect,
  'id' | 'layoutId' | 'status' | 'fileManifest' | 'threeMfPath' | 'generatedAt' | 'errorMessage' | 'themisProjectId'
>;
```

Update `formatBomGeneration`:

```typescript
export function formatBomGeneration(row: RawGenRow): ApiBomGeneration {
  return {
    id: row.id,
    layoutId: row.layoutId,
    status: row.status as ApiBomGeneration['status'],
    fileManifest: row.fileManifest ? (JSON.parse(row.fileManifest) as BomGenerationManifestEntry[]) : null,
    threeMfPath: row.threeMfPath,
    generatedAt: row.generatedAt,
    errorMessage: row.errorMessage,
    themisProjectId: row.themisProjectId ?? null,
  };
}
```

- [ ] **Step 5: Run tests and type-check**

```bash
cd C:\Users\mgome\Documents\projects\gridfinity-customizer
npm run test:run
npm run build
```

Expected: all tests pass, build succeeds with no type errors.

- [ ] **Step 6: Commit**

```bash
git add server/src/db/schema.ts server/src/db/migrate.ts shared/src/types.ts server/src/services/bomGeneration.service.ts
git commit -m "feat(bom): add themisProjectId to bom_generations for bidirectional linking"
```

---

## Task 5 — Ordinus: Themis HTTP client

**Repo:** `C:\Users\mgome\Documents\projects\gridfinity-customizer`

**Files:**
- Create: `server/src/services/themis.service.ts`
- Create: `server/src/services/themis.service.test.ts`

### Background

This service is a thin HTTP client over Themis' REST API. It uses Node 24's native `fetch` and `FormData` — no new dependencies. All functions accept a `themisUrl` parameter so they're testable without environment coupling.

`createThemisProject` accepts optional `sourceUser` and `sourceLayoutId` parameters that map to `source_user` and `source_layout_id` on the Themis project — completing the Themis-side bidirectional link.

- [ ] **Step 1: Write failing tests**

Create `server/src/services/themis.service.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  uploadStlToThemis,
  createThemisProject,
  addThemisProjectItem,
} from './themis.service.js';

const THEMIS = 'http://localhost:8001';

function mockFetch(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as Response);
}

beforeEach(() => { vi.restoreAllMocks(); });

describe('uploadStlToThemis', () => {
  it('posts multipart to /api/v1/files/upload and returns file id', async () => {
    global.fetch = mockFetch({ id: 42, original_filename: 'bin_2x3.stl' });
    const result = await uploadStlToThemis(THEMIS, Buffer.from('stl'), 'bin_2x3.stl', '/Gridfinity/my-layout');
    expect(result).toBe(42);
    expect((global.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0])
      .toBe(`${THEMIS}/api/v1/files/upload`);
  });

  it('throws if Themis returns non-ok', async () => {
    global.fetch = mockFetch({ error: { message: 'bad' } }, 422);
    await expect(
      uploadStlToThemis(THEMIS, Buffer.from('stl'), 'bin.stl', '/Gridfinity/x')
    ).rejects.toThrow('422');
  });
});

describe('createThemisProject', () => {
  it('posts to /api/v1/projects and returns project id', async () => {
    global.fetch = mockFetch({ id: 7, name: 'My Layout' });
    const result = await createThemisProject(THEMIS, 'My Layout', 'Imported from Ordinus');
    expect(result).toBe(7);
    const [url, opts] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${THEMIS}/api/v1/projects`);
    const body = JSON.parse(opts.body as string) as Record<string, unknown>;
    expect(body.name).toBe('My Layout');
    expect(body.notes).toBe('Imported from Ordinus');
  });

  it('includes source fields when provided', async () => {
    global.fetch = mockFetch({ id: 7, name: 'My Layout' });
    await createThemisProject(THEMIS, 'My Layout', 'Imported from Ordinus', 'alice', 42);
    const [, opts] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(opts.body as string) as Record<string, unknown>;
    expect(body.source_app).toBe('ordinus');
    expect(body.source_user).toBe('alice');
    expect(body.source_layout_id).toBe(42);
  });

  it('throws if Themis returns non-ok', async () => {
    global.fetch = mockFetch({}, 500);
    await expect(createThemisProject(THEMIS, 'X', '')).rejects.toThrow('500');
  });
});

describe('addThemisProjectItem', () => {
  it('posts item to /api/v1/projects/:id/items', async () => {
    global.fetch = mockFetch({ id: 1 });
    await addThemisProjectItem(THEMIS, 7, 42, 3);
    const [url, opts] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${THEMIS}/api/v1/projects/7/items`);
    const body = JSON.parse(opts.body as string) as Record<string, unknown>;
    expect(body.file_id).toBe(42);
    expect(body.quantity).toBe(3);
    expect(body.filament_profile_uuid).toBe('');
  });

  it('throws if Themis returns non-ok', async () => {
    global.fetch = mockFetch({}, 404);
    await expect(addThemisProjectItem(THEMIS, 7, 99, 1)).rejects.toThrow('404');
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd C:\Users\mgome\Documents\projects\gridfinity-customizer
npm run test:run -- --reporter=verbose server/src/services/themis.service.test.ts
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the Themis service**

Create `server/src/services/themis.service.ts`:

```typescript
async function themisPost(url: string, body: unknown): Promise<unknown> {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`Themis ${resp.status}: POST ${url}`);
  return resp.json();
}

/** Upload an STL buffer to the Themis library. Returns the file id (new or existing via dedup). */
export async function uploadStlToThemis(
  themisUrl: string,
  bytes: Buffer,
  filename: string,
  folder: string,
): Promise<number> {
  const form = new FormData();
  form.append('file', new Blob([bytes], { type: 'application/octet-stream' }), filename);
  form.append('folder', folder);
  const resp = await fetch(`${themisUrl}/api/v1/files/upload`, { method: 'POST', body: form });
  if (!resp.ok) throw new Error(`Themis ${resp.status}: upload ${filename}`);
  const data = await resp.json() as { id: number };
  return data.id;
}

/** Create a Themis project. Returns the project id. */
export async function createThemisProject(
  themisUrl: string,
  name: string,
  notes: string,
  sourceUser?: string,
  sourceLayoutId?: number,
): Promise<number> {
  const data = await themisPost(`${themisUrl}/api/v1/projects`, {
    name,
    notes,
    ...(sourceUser !== undefined && {
      source_app: 'ordinus',
      source_user: sourceUser,
      source_layout_id: sourceLayoutId,
    }),
  }) as { id: number };
  return data.id;
}

/** Add an item to a Themis project. */
export async function addThemisProjectItem(
  themisUrl: string,
  projectId: number,
  fileId: number,
  quantity: number,
): Promise<void> {
  await themisPost(`${themisUrl}/api/v1/projects/${projectId}/items`, {
    file_id: fileId,
    quantity,
    filament_profile_uuid: '',
    color_hex: '#FFFFFF',
  });
}
```

- [ ] **Step 4: Run tests**

```bash
cd C:\Users\mgome\Documents\projects\gridfinity-customizer
npm run test:run -- --reporter=verbose server/src/services/themis.service.test.ts
```

Expected: all 9 tests pass.

- [ ] **Step 5: Run full test suite**

```bash
npm run test:run
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add server/src/services/themis.service.ts server/src/services/themis.service.test.ts
git commit -m "feat(themis): add Themis HTTP client service with bidirectional source field support"
```

---

## Task 6 — Ordinus: send-to-themis endpoint

**Repo:** `C:\Users\mgome\Documents\projects\gridfinity-customizer`

**Files:**
- Create: `server/src/controllers/themis.controller.ts`
- Create: `server/src/controllers/themis.controller.test.ts`
- Modify: `server/src/routes/bom.routes.ts`

### Background

The generation record stores the file manifest as `fileManifest` (JSON string). Individual STL files live at `{GENERATED_STL_DIR}/bom-layout-{layoutId}/{filename}`. The handler:
1. Rejects if THEMIS_URL is unset (503).
2. Looks up the Ordinus `users` table to get the caller's `username` (from `req.user.userId`).
3. Uploads unique STLs with dedup via Themis.
4. Creates a Themis project, passing `source_app = "ordinus"`, `source_user = username`, `source_layout_id = layoutId`.
5. Adds items with correct quantities.
6. **Writes the Themis project ID back** to `bomGenerations.themisProjectId` — completing the Ordinus side of the bidirectional link.
7. Returns `{ data: { projectUrl } }` matching the `ApiResponse<T>` wrapper used everywhere in Ordinus.

`BomGenerationManifestEntry` type from `@gridfinity/shared`:
```typescript
{ filename: string; widthUnits: number; heightUnits: number; customization: BinCustomization; qty: number }
```

`layouts.name` (not `layouts.title`) is used for the layout name and folder slug.

- [ ] **Step 1: Write failing test**

Create `server/src/controllers/themis.controller.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { sendToThemisHandler } from './themis.controller.js';
import type { Request, Response, NextFunction } from 'express';

vi.mock('../services/themis.service.js', () => ({
  uploadStlToThemis: vi.fn().mockResolvedValue(10),
  createThemisProject: vi.fn().mockResolvedValue(5),
  addThemisProjectItem: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('fs/promises', () => ({
  default: { readFile: vi.fn().mockResolvedValue(Buffer.from('stl')) },
}));

vi.mock('../db/connection.js', () => ({
  db: {
    select: vi.fn().mockReturnValue({
      from: vi.fn().mockReturnValue({
        where: vi.fn().mockReturnValue({
          limit: vi.fn().mockResolvedValue([
            {
              id: 1,
              name: 'My Layout',
              userId: 1,
            },
          ]),
        }),
      }),
    }),
    update: vi.fn().mockReturnValue({
      set: vi.fn().mockReturnValue({
        where: vi.fn().mockResolvedValue(undefined),
      }),
    }),
  },
}));

vi.mock('../db/schema.js', () => ({ layouts: {}, bomGenerations: {}, users: {} }));

describe('sendToThemisHandler', () => {
  it('returns 503 when THEMIS_URL is not configured', async () => {
    const origUrl = process.env['THEMIS_URL'];
    delete process.env['THEMIS_URL'];
    const res = { status: vi.fn().mockReturnThis(), json: vi.fn() } as unknown as Response;
    const req = { params: { layoutId: '1' }, user: { userId: 1 } } as unknown as Request;
    await sendToThemisHandler(req, res, vi.fn() as NextFunction);
    expect(res.status).toHaveBeenCalledWith(503);
    process.env['THEMIS_URL'] = origUrl;
  });
});
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd C:\Users\mgome\Documents\projects\gridfinity-customizer
npm run test:run -- --reporter=verbose server/src/controllers/themis.controller.test.ts
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the controller**

Create `server/src/controllers/themis.controller.ts`:

```typescript
import fs from 'fs/promises';
import path from 'path';
import { eq } from 'drizzle-orm';
import { AppError, ErrorCodes } from '@gridfinity/shared';
import type { BomGenerationManifestEntry } from '@gridfinity/shared';
import type { Request, Response, NextFunction } from 'express';
import { db } from '../db/connection.js';
import { layouts, bomGenerations, users } from '../db/schema.js';
import { config } from '../config.js';
import { uploadStlToThemis, createThemisProject, addThemisProjectItem } from '../services/themis.service.js';
import { logger } from '../logger.js';

function slugify(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60);
}

export async function sendToThemisHandler(req: Request, res: Response, next: NextFunction): Promise<void> {
  try {
    const themisUrl = config.THEMIS_URL;
    if (!themisUrl) {
      res.status(503).json({ error: { message: 'THEMIS_URL is not configured' } });
      return;
    }

    const layoutId = parseInt(req.params['layoutId'] as string, 10);
    if (isNaN(layoutId)) throw new AppError(ErrorCodes.VALIDATION_ERROR, 'Invalid layout ID');
    if (!req.user) throw new AppError(ErrorCodes.AUTH_REQUIRED, 'Authentication required');

    const layoutRows = await db.select().from(layouts).where(eq(layouts.id, layoutId)).limit(1);
    if (!layoutRows.length) throw new AppError(ErrorCodes.NOT_FOUND, 'Layout not found');
    const layout = layoutRows[0];
    if (layout.userId !== req.user.userId) throw new AppError(ErrorCodes.FORBIDDEN, 'Not authorized');

    // Look up the caller's username for Themis source_user field.
    const userRows = await db.select({ username: users.username }).from(users)
      .where(eq(users.id, req.user.userId)).limit(1);
    const username = userRows.length ? userRows[0].username : undefined;

    const genRows = await db.select().from(bomGenerations).where(eq(bomGenerations.layoutId, layoutId)).limit(1);
    if (!genRows.length || genRows[0].status !== 'ready') {
      res.status(409).json({ error: { message: 'BOM generation is not ready' } });
      return;
    }

    const gen = genRows[0];
    const manifest: BomGenerationManifestEntry[] = gen.fileManifest
      ? (JSON.parse(gen.fileManifest) as BomGenerationManifestEntry[])
      : [];

    const outDir = path.resolve(config.GENERATED_STL_DIR, `bom-layout-${layoutId}`);
    const folder = `/Gridfinity/${slugify(layout.name)}`;

    // Upload unique STL files; collect filename → Themis file id mapping.
    const fileIdMap = new Map<string, number>();
    const seen = new Set<string>();
    for (const entry of manifest) {
      if (seen.has(entry.filename)) continue;
      seen.add(entry.filename);
      const bytes = await fs.readFile(path.join(outDir, entry.filename));
      const fileId = await uploadStlToThemis(themisUrl, bytes, entry.filename, folder);
      fileIdMap.set(entry.filename, fileId);
      logger.info({ filename: entry.filename, fileId }, 'Uploaded STL to Themis');
    }

    const projectId = await createThemisProject(
      themisUrl,
      layout.name,
      'Imported from Ordinus',
      username,
      layoutId,
    );
    logger.info({ projectId, layoutId }, 'Created Themis project');

    for (const entry of manifest) {
      const fileId = fileIdMap.get(entry.filename);
      if (fileId === undefined) continue;
      await addThemisProjectItem(themisUrl, projectId, fileId, entry.qty);
    }

    // Write Themis project ID back to bom_generations for bidirectional link.
    await db.update(bomGenerations)
      .set({ themisProjectId: projectId })
      .where(eq(bomGenerations.layoutId, layoutId));

    const projectUrl = `${themisUrl}/projects/${projectId}`;
    res.status(200).json({ data: { projectUrl } });
  } catch (err) {
    next(err);
  }
}
```

- [ ] **Step 4: Register the route in bom.routes.ts**

In `server/src/routes/bom.routes.ts`, add the import and route:

```typescript
import { Router } from 'express';
import { requireAuth } from '../middleware/auth.js';
import * as ctrl from '../controllers/bomGeneration.controller.js';
import { sendToThemisHandler } from '../controllers/themis.controller.js';

const router = Router();

router.post('/generate/:layoutId', requireAuth, ctrl.generateHandler);
router.get('/generation/:layoutId', requireAuth, ctrl.getGenerationHandler);
router.get('/generation/:layoutId/files/:filename', requireAuth, ctrl.serveFileHandler);
router.post('/send-to-themis/:layoutId', requireAuth, sendToThemisHandler);

export default router;
```

- [ ] **Step 5: Run all tests**

```bash
cd C:\Users\mgome\Documents\projects\gridfinity-customizer
npm run test:run
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add server/src/controllers/themis.controller.ts server/src/controllers/themis.controller.test.ts server/src/routes/bom.routes.ts
git commit -m "feat(bom): add POST /bom/send-to-themis/:layoutId with bidirectional project link"
```

---

## Task 7 — Ordinus: Send to Themis button

**Repo:** `C:\Users\mgome\Documents\projects\gridfinity-customizer`

**Files:**
- Modify: `app/src/api/bomGeneration.api.ts`
- Modify: `app/src/components/BomGenerationPanel.tsx`

### Background

`VITE_THEMIS_URL` controls whether the button renders at all — if the env var is absent, the button is invisible (no broken state for installs that don't use Themis). The frontend reads `import.meta.env.VITE_THEMIS_URL`.

The button has three visual states:
- **idle**: "Send to Themis" (enabled when `isReady`)
- **sending**: "Sending…" (disabled)
- **sent**: replaced by an anchor "Open in Themis →"

**On load**, if `generation.themisProjectId` is already set (a previous send), the panel immediately shows "Open in Themis →" without a new send. The project URL is reconstructed from `VITE_THEMIS_URL` + `generation.themisProjectId`.

Error falls back to the existing `error` string state already rendered by the panel.

- [ ] **Step 1: Add sendToThemis to the API module**

In `app/src/api/bomGeneration.api.ts`, append:

```typescript
export interface SendToThemisResponse {
  projectUrl: string;
}

export async function sendToThemis(
  layoutId: number,
  accessToken: string,
): Promise<SendToThemisResponse> {
  const result = await apiFetch<ApiResponse<SendToThemisResponse>>(
    `/bom/send-to-themis/${layoutId}`,
    { method: 'POST', headers: JSON_HEADERS },
    accessToken,
  );
  return result.data;
}
```

- [ ] **Step 2: Add VITE_THEMIS_URL to Vite env**

Append to `app/.env.development` (this file already sets `VITE_API_BASE_URL=/api/v1`):

```
VITE_THEMIS_URL=http://localhost:8001
```

- [ ] **Step 3: Update BomGenerationPanel**

Replace `app/src/components/BomGenerationPanel.tsx` with:

```tsx
import { useState, useEffect, useRef, useCallback } from 'react';
import type { ApiBomGeneration, BOMItem } from '@gridfinity/shared';
import { triggerBomGeneration, getBomGeneration, getFileDownloadUrl, sendToThemis } from '../api/bomGeneration.api';

const THEMIS_URL = import.meta.env['VITE_THEMIS_URL'] as string | undefined;

interface BomGenerationPanelProps {
  layoutId: number | null;
  layoutTitle: string;
  bomItems: BOMItem[];
  accessToken: string | null;
}

export function BomGenerationPanel({ layoutId, layoutTitle, bomItems, accessToken }: BomGenerationPanelProps) {
  const [generation, setGeneration] = useState<ApiBomGeneration | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [themisState, setThemisState] = useState<'idle' | 'sending' | 'sent'>('idle');
  const [themisProjectUrl, setThemisProjectUrl] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  }, []);

  const fetchGeneration = useCallback(async () => {
    if (!layoutId || !accessToken) return;
    try {
      const gen = await getBomGeneration(layoutId, accessToken);
      setGeneration(gen);
      if (gen?.status !== 'generating') stopPolling();
    } catch {
      stopPolling();
    }
  }, [layoutId, accessToken, stopPolling]);

  useEffect(() => {
    void fetchGeneration();
    return stopPolling;
  }, [fetchGeneration, stopPolling]);

  useEffect(() => {
    if (generation?.status === 'generating') {
      pollRef.current = setInterval(() => { void fetchGeneration(); }, 3000);
    } else {
      stopPolling();
    }
    return stopPolling;
  }, [generation?.status, fetchGeneration, stopPolling]);

  // Pre-populate Themis link if a previous send is already recorded in the DB.
  useEffect(() => {
    if (generation?.themisProjectId && THEMIS_URL) {
      setThemisProjectUrl(`${THEMIS_URL}/projects/${generation.themisProjectId}`);
      setThemisState('sent');
    }
  }, [generation?.themisProjectId]);

  const handleGenerate = async () => {
    if (!layoutId || !accessToken) return;
    setLoading(true);
    setError(null);
    try {
      const gen = await triggerBomGeneration(layoutId, bomItems, accessToken);
      setGeneration(gen);
      // A new generation clears any existing Themis link.
      setThemisState('idle');
      setThemisProjectUrl(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Generation failed');
    } finally {
      setLoading(false);
    }
  };

  const handleSendToThemis = async () => {
    if (!layoutId || !accessToken) return;
    setThemisState('sending');
    setError(null);
    try {
      const { projectUrl } = await sendToThemis(layoutId, accessToken);
      setThemisProjectUrl(projectUrl);
      setThemisState('sent');
      window.open(projectUrl, '_blank', 'noopener');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Send to Themis failed');
      setThemisState('idle');
    }
  };

  const isGenerating = generation?.status === 'generating' || loading;
  const isReady = generation?.status === 'ready';
  const hasGeneration = generation !== null;

  const threeMfFilename = generation?.threeMfPath
    ? generation.threeMfPath.split('/').pop() ?? ''
    : '';

  const downloadUrl = isReady && layoutId && threeMfFilename
    ? getFileDownloadUrl(layoutId, threeMfFilename)
    : null;

  const handleDownload = async () => {
    if (!downloadUrl || !accessToken) return;
    try {
      const response = await fetch(downloadUrl, {
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({})) as { error?: { message?: string } };
        setError(data?.error?.message ?? 'Download failed');
        return;
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const safeTitle = layoutTitle.replace(/[^a-zA-Z0-9_\- ]/g, '').trim() || 'layout';
      const a = document.createElement('a');
      a.href = url;
      a.download = `${safeTitle}.3mf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setError('Download failed');
    }
  };

  return (
    <div className="bom-generation-panel">
      {generation?.errorMessage && (
        <div className="bom-gen-error">{generation.errorMessage}</div>
      )}
      {error && <div className="bom-gen-error">{error}</div>}
      <div className="bom-gen-actions">
        <button
          type="button"
          className="bom-gen-btn bom-gen-btn-primary"
          onClick={handleGenerate}
          disabled={isGenerating || !layoutId}
        >
          {isGenerating ? 'Generating…' : hasGeneration ? 'Regenerate' : 'Generate'}
        </button>
        <button
          type="button"
          className="bom-gen-btn"
          disabled={!isReady || !downloadUrl}
          onClick={() => { void handleDownload(); }}
        >
          Download 3MF
        </button>
        {THEMIS_URL && (
          themisState === 'sent' && themisProjectUrl ? (
            <a
              className="bom-gen-btn"
              href={themisProjectUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open in Themis →
            </a>
          ) : (
            <button
              type="button"
              className="bom-gen-btn"
              disabled={!isReady || themisState === 'sending'}
              onClick={() => { void handleSendToThemis(); }}
            >
              {themisState === 'sending' ? 'Sending…' : 'Send to Themis'}
            </button>
          )
        )}
      </div>
      {isReady && generation?.generatedAt && (
        <div className="bom-gen-status">
          Generated {new Date(generation.generatedAt).toLocaleString()}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run full test suite + type check**

```bash
cd C:\Users\mgome\Documents\projects\gridfinity-customizer
npm run test:run
npm run build
```

Expected: all tests pass, build succeeds with no type errors.

- [ ] **Step 5: Commit**

```bash
git add app/src/api/bomGeneration.api.ts app/src/components/BomGenerationPanel.tsx server/src/controllers/themis.controller.ts app/.env.development
git commit -m "feat(bom-panel): add Send to Themis button; pre-populate link from stored themisProjectId"
```

---

## Task 8 — Integration smoke test

**Manual verification** — requires both services running.

- [ ] **Step 1: Start the stack**

```bash
# Terminal 1 — Themis
cd C:\Users\mgome\Documents\projects\omnibus
docker compose up

# Terminal 2 — Ordinus backend
cd C:\Users\mgome\Documents\projects\gridfinity-customizer
npm run server:dev

# Terminal 3 — Ordinus frontend
cd C:\Users\mgome\Documents\projects\gridfinity-customizer
npm run dev
```

- [ ] **Step 2: Smoke test the happy path**

1. Open Ordinus at `http://localhost:5173`, log in.
2. Open or create a layout with at least 2 different bin sizes.
3. Generate BOM — wait for status `ready`.
4. Click **Send to Themis**.
5. Confirm Themis project page opens in a new tab.
6. In Themis, verify the project shows the correct items with quantities.
7. Verify the project's detail page shows `source_app = ordinus`, `source_user = <your username>`, `source_layout_id = <layout ID>`.
8. Verify the files appear in the Themis library under `/Gridfinity/{layout-name}/`.

- [ ] **Step 3: Smoke test bidirectional link persistence**

1. Refresh the Ordinus layout page.
2. Confirm the **"Open in Themis →"** link is already shown (not the "Send to Themis" button) — because `generation.themisProjectId` was persisted to the DB.
3. Click the link — it should open the same Themis project that was created in Step 2.

- [ ] **Step 4: Smoke test dedup**

1. Click **Regenerate** in Ordinus to clear `themisProjectId`, then generate again and click **Send to Themis**.
2. Confirm a second Themis project is created but no duplicate files appear in the Themis library under the same folder.

- [ ] **Step 5: Log both branch summaries and merge**

```bash
# Themis
git -C C:\Users\mgome\Documents\projects\themis log --oneline feat/ordinus-themis-integration

# Ordinus
git -C C:\Users\mgome\Documents\projects\gridfinity-customizer log --oneline feat/ordinus-themis-integration
```

Merge both branches once smoke test passes.
