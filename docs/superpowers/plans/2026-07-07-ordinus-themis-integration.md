# Ordinus → Themis Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Send to Themis" flow that uploads Ordinus BOM STLs to the Themis library, creates a project with correct quantities, and opens the project in a new tab.

**Architecture:** Two repos change independently — Themis gets a dedup-on-upload behaviour and an optional-field schema fix; Ordinus gets a server-side Themis client, one new endpoint, and a frontend button. Tasks 1–2 are in `themis`; Tasks 3–6 are in `gridfinity-customizer`. Each task commits to its own repo.

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

## Task 2 — Themis: optional machine/process in ProjectCreate

**Repos:** `C:\Users\mgome\Documents\projects\themis`

**Files:**
- Modify: `backend/app/api/routes/projects.py` — `ProjectCreate` schema

### Background

`machine_uuid` and `process_uuid` are required strings today. External callers (Ordinus) don't know slicer profile UUIDs, so the project must be creatable without them. The DB column is already nullable in practice (TEXT with no NOT NULL constraint in SQLite). Only the Pydantic schema needs to change.

- [ ] **Step 1: Write a failing test**

Create `backend/tests/api/test_projects_api.py` (the file does not yet exist):

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
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd C:\Users\mgome\Documents\projects\themis\backend
python -m pytest tests/api/ -k "test_create_project_without_machine_process" -v
```

Expected: FAIL with 422 (validation error — required fields missing).

- [ ] **Step 3: Make machine_uuid and process_uuid optional**

In `backend/app/api/routes/projects.py`, update `ProjectCreate`:

```python
class ProjectCreate(BaseModel):
    name: str
    machine_uuid: Optional[str] = None
    process_uuid: Optional[str] = None
    notes: Optional[str] = None
```

(`Optional` is already imported at the top of the file.)

- [ ] **Step 4: Run all tests**

```bash
cd C:\Users\mgome\Documents\projects\themis\backend
python -m pytest -v
```

Expected: 457+ passed, 0 failed.

- [ ] **Step 5: Commit**

```bash
git -C C:\Users\mgome\Documents\projects\themis add backend/app/api/routes/projects.py
git -C C:\Users\mgome\Documents\projects\themis commit -m "feat(projects): make machine_uuid and process_uuid optional for external project creation"
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

- [ ] **Step 4: Verify server still starts**

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

## Task 4 — Ordinus: Themis service

**Repo:** `C:\Users\mgome\Documents\projects\gridfinity-customizer`

**Files:**
- Create: `server/src/services/themis.service.ts`
- Create: `server/src/services/themis.service.test.ts`

### Background

This service is a thin HTTP client over Themis' REST API. It uses Node 24's native `fetch` and `FormData` — no new dependencies. All functions accept a `themisUrl` parameter so they're testable without environment coupling.

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
): Promise<number> {
  const data = await themisPost(`${themisUrl}/api/v1/projects`, { name, notes }) as { id: number };
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

Expected: all 8 tests pass.

- [ ] **Step 5: Run full test suite**

```bash
npm run test:run
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add server/src/services/themis.service.ts server/src/services/themis.service.test.ts
git commit -m "feat(themis): add Themis HTTP client service (upload, create project, add item)"
```

---

## Task 5 — Ordinus: send-to-themis endpoint

**Repo:** `C:\Users\mgome\Documents\projects\gridfinity-customizer`

**Files:**
- Create: `server/src/controllers/themis.controller.ts`
- Modify: `server/src/routes/bom.routes.ts`
- Modify: `server/src/app.ts` (not needed — route mounts under existing `/api/v1/bom`)

### Background

The generation record stores the file manifest as `fileManifest` (JSON string). Individual STL files live at `{GENERATED_STL_DIR}/bom-layout-{layoutId}/{filename}`. The handler reads those files and uploads them, deduplicating via Themis. A layout slug (`my-layout`) derived from `layout.name` is used as the Themis subfolder name.

`BomGenerationManifestEntry` type from `@gridfinity/shared`:
```typescript
{ filename: string; widthUnits: number; heightUnits: number; customization: BinCustomization; qty: number }
```

- [ ] **Step 1: Write failing test**

Create `server/src/routes/generation.routes.test.ts` already exists — check it. Add a new test file `server/src/controllers/themis.controller.test.ts`:

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
              title: 'My Layout',
              userId: 1,
            },
          ]),
        }),
      }),
    }),
  },
}));

vi.mock('../db/schema.js', () => ({ layouts: {}, bomGenerations: {} }));

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
import { layouts, bomGenerations } from '../db/schema.js';
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

    // Upload unique STL files; collect filename → Themis file id mapping
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

    const projectId = await createThemisProject(themisUrl, layout.name, 'Imported from Ordinus');
    logger.info({ projectId, layoutId }, 'Created Themis project');

    for (const entry of manifest) {
      const fileId = fileIdMap.get(entry.filename);
      if (fileId === undefined) continue;
      await addThemisProjectItem(themisUrl, projectId, fileId, entry.qty);
    }

    const projectUrl = `${themisUrl}/projects/${projectId}`;
    res.status(200).json({ projectUrl });
  } catch (err) {
    next(err);
  }
}
```

- [ ] **Step 4: Register the route in bom.routes.ts**

In `server/src/routes/bom.routes.ts`, add:

```typescript
import { sendToThemisHandler } from '../controllers/themis.controller.js';

// add after existing routes:
router.post('/send-to-themis/:layoutId', requireAuth, sendToThemisHandler);
```

Full file after edit:

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
git commit -m "feat(bom): add POST /bom/send-to-themis/:layoutId endpoint"
```

---

## Task 6 — Ordinus: Send to Themis button

**Repo:** `C:\Users\mgome\Documents\projects\gridfinity-customizer`

**Files:**
- Modify: `app/src/api/bomGeneration.api.ts`
- Modify: `app/src/components/BomGenerationPanel.tsx`

### Background

`VITE_THEMIS_URL` controls whether the button renders at all — if the env var is absent, the button is invisible (no broken state for installs that don't use Themis). The frontend reads `import.meta.env.VITE_THEMIS_URL`.

The button has three visual states:
- **idle**: "Send to Themis" (enabled when `isReady`)
- **sending**: "Sending…" (disabled)
- **sent**: replaced by a link "Open in Themis →"

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

Note: `sendToThemis` returns `result.data` — but the controller returns `{ projectUrl }` directly (not wrapped in `{ data: ... }`). Fix the controller to match Ordinus' `ApiResponse` wrapper, or unwrap here. Looking at the controller — it returns `res.status(200).json({ projectUrl })` without the `data` wrapper. To stay consistent with every other API handler in Ordinus which uses `ApiResponse<T>`, update the controller:

In `server/src/controllers/themis.controller.ts`, change the final response line to:

```typescript
res.status(200).json({ data: { projectUrl } });
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

  const handleGenerate = async () => {
    if (!layoutId || !accessToken) return;
    setLoading(true);
    setError(null);
    try {
      const gen = await triggerBomGeneration(layoutId, bomItems, accessToken);
      setGeneration(gen);
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
git add app/src/api/bomGeneration.api.ts app/src/components/BomGenerationPanel.tsx server/src/controllers/themis.controller.ts
git commit -m "feat(bom-panel): add Send to Themis button with idle/sending/sent states"
```

---

## Task 7 — Integration smoke test

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
7. Verify the files appear in the Themis library under `/Gridfinity/{layout-name}/`.

- [ ] **Step 3: Smoke test dedup**

1. Click **Send to Themis** again on the same layout.
2. Confirm a second project is created but no duplicate files appear in the Themis library.

- [ ] **Step 4: Commit Themis branch and Ordinus branch**

```bash
# Themis
git -C C:\Users\mgome\Documents\projects\themis log --oneline feat/ordinus-themis-integration

# Ordinus
git -C C:\Users\mgome\Documents\projects\gridfinity-customizer log --oneline feat/ordinus-themis-integration
```

Merge both branches once smoke test passes.
