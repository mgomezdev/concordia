# Ordinus → Themis Integration — Design Spec

**Date:** 2026-07-07
**Status:** Approved
**Repos:** `gridfinity-customizer` (Ordinus), `themis`

---

## Goal

From Ordinus' BOM generation panel, a user can send the generated STL parts directly to Themis — uploading each unique STL to the Themis library, creating a project pre-populated with the correct quantities, and opening that project in Themis ready for filament assignment and print generation.

---

## User Flow

1. User plans a Gridfinity layout in Ordinus and clicks **Generate** to produce the BOM STLs.
2. When generation is `ready`, a **Send to Themis** button appears alongside the existing Download 3MF button.
3. Clicking it triggers a server-side call chain: upload STLs → create project → add items.
4. On success the Themis project URL opens in a new tab and a link appears inline.
5. In Themis the user sets machine profile, process profile, and filament per item group, then clicks **Generate**.

---

## Architecture

### Deduplication (Themis — upload endpoint)

`POST /api/v1/files/upload` is modified to check for a content-hash duplicate **within the same target folder** before writing to disk:

1. Read all incoming bytes into memory.
2. Compute SHA-256 (same algorithm already stored in `UploadedFile.content_hash`).
3. Query `UploadedFile` where `content_hash = <hash>` AND `folder = <target_folder>`.
4. If a match exists, return the existing record immediately — no write, no DB insert.
5. Otherwise write bytes and proceed as today.

Scoping to the target folder (not the whole library) means:
- The same STL in two different layout folders is stored twice (correct — separate layouts).
- Re-running "Send to Themis" for the same layout reuses existing files (correct — idempotent).
- An unrelated file that happens to share bytes in a different folder is never matched (correct — no cross-folder surprises).

Ordinus always targets `/Gridfinity/{layout-slug}`. The folder scoping is enforced by Ordinus; Themis stays general-purpose.

### ProjectCreate — optional machine/process

`machine_uuid` and `process_uuid` are made `Optional[str] = None` in Themis' `ProjectCreate` schema so external callers can create a project without knowing slicer profile UUIDs. The user sets them in Themis before generating.

### Ordinus server — send-to-themis endpoint

`POST /api/v1/bom/:layoutId/send-to-themis`

Handler logic:
1. Load generation record; reject with 409 if status is not `ready`.
2. Load layout name for project naming and folder slugging.
3. Parse `fileManifest` from the generation record.
4. For each unique STL filename in the manifest: read bytes from `{GENERATED_STL_DIR}/bom-layout-{layoutId}/{filename}`, POST to `{THEMIS_URL}/api/v1/files/upload` with `folder=/Gridfinity/{layout-slug}`.
5. POST to `{THEMIS_URL}/api/v1/projects` with `{ name: layout.name, notes: "Imported from Ordinus" }`.
6. For each manifest entry: POST to `{THEMIS_URL}/api/v1/projects/{id}/items` with `{ file_id, quantity, filament_profile_uuid: "", color_hex: "#FFFFFF" }`.
7. Return `{ projectUrl: "{THEMIS_URL}/projects/{projectId}" }`.

`THEMIS_URL` is an optional env var. If unset, the endpoint returns 503.

### Ordinus frontend — BomGenerationPanel

Adds a **Send to Themis** button (only rendered when `VITE_THEMIS_URL` is configured and generation is `ready`). State: `idle | sending | sent | error`. On `sent`, renders a link to the Themis project.

---

## File Changelist

| Repo | File | Change |
|---|---|---|
| `themis` | `backend/app/api/routes/files.py` | Dedup check before write in `upload_file` |
| `themis` | `backend/app/api/routes/projects.py` | `machine_uuid`/`process_uuid` → `Optional[str] = None` |
| `themis` | `backend/tests/api/test_files_api.py` | Two new dedup tests |
| `ordinus` | `server/src/config.ts` | Add `THEMIS_URL: z.string().url().optional()` |
| `ordinus` | `.env.example` | Document `THEMIS_URL` |
| `ordinus` | `.env.development` | Set `THEMIS_URL=http://localhost:8001` |
| `ordinus` | `server/src/services/themis.service.ts` | New — Themis HTTP client (upload, create project, add item) |
| `ordinus` | `server/src/services/themis.service.test.ts` | Unit tests with fetch mock |
| `ordinus` | `server/src/controllers/themis.controller.ts` | New — send-to-themis handler |
| `ordinus` | `server/src/routes/bom.routes.ts` | Register `POST /send-to-themis/:layoutId` |
| `ordinus` | `app/src/api/bomGeneration.api.ts` | Add `sendToThemis(layoutId, token)` |
| `app/src/components/BomGenerationPanel.tsx` | Add Send to Themis button + state |

---

## Key Invariants

| Invariant | Where enforced |
|---|---|
| Dedup scoped to target folder only | `upload_file` — query filters on `folder` |
| Re-send is idempotent | Hash match returns existing record |
| `THEMIS_URL` unset → graceful 503 | `themis.controller.ts` |
| Button only visible when generation ready | `BomGenerationPanel` — `isReady` gate |
| No migrations needed | `machine_uuid`/`process_uuid` nullable via Pydantic only; SQLite column remains TEXT |

---

## Out of Scope

| Not in scope | Reason |
|---|---|
| Filament pre-assignment | Themis owns filament profiles; Ordinus doesn't know them |
| Auth between Ordinus and Themis | Same local trust boundary |
| Progress streaming during send | Upload is fast on local network; loading state is sufficient |
| Cleanup of Gridfinity folder | User manages library; outside integration contract |
