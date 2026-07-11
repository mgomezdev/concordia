# Advisory Review: Omnibus Print Farm Management System

**Date:** 2026-07-10  
**Reviewer:** claude-fable-5 (via orchestrated research + synthesis)  
**Scope:** Themis + Laminus + Ordinus + Spoolman, orchestrated via Concordia  
**Target:** Personal farm, <10 FDM printers, multi-print projects (cosplay, gridfinity, modular systems)

---

## 1. What's Architecturally Strong

**The queue engine design is genuinely good.** The separation between BLOCKED and FAILED is one of the smartest decisions in the system. A job that can't run because the wrong filament is loaded stays in the queue and re-evaluates every cycle — it doesn't die. Combined with the plate-clear gate (set on print *start*, not on completion, so a missed network event can't cause a collision), you've avoided two of the most common failure modes in hobbyist automation. The float queue position for cheap reordering is a clean choice.

**Laminus's profile catalog approach is solid.** UUID5 from deterministic inputs gives stable IDs across restarts. The inheritance chain resolution for OrcaSlicer profiles is the kind of thing most people skip and regret. The geometry-only retry on slice failure is a thoughtful fallback. These details reflect real experience with OrcaSlicer's quirks.

**Content-hash dedup in the file library is the right primitive.** Scoping dedup to `(content_hash, folder)` rather than global means the same STL can coexist in `/Gridfinity` and `/Projects` without interference. The Ordinus-to-Themis integration leverages this correctly — if the same bin STL appears across multiple layouts, it reuses the same Themis file ID.

**The Docker composition is clean.** Health-checked dependency ordering (`service_healthy` for Laminus → Themis) prevents the startup race that kills most containerized slicing setups. Named volumes are correctly scoped. `shm_size: 1gb` is present and necessary for OrcaSlicer's X11 stack.

**Ordinus's parametric generation is more capable than it appears.** Per-bin customization (wall cutouts, lip, finger slides, patterns), OpenSCAD with Manifold backend, param-hash caching, async status polling, PDF export — this is a genuinely useful gridfinity tool.

**Path traversal guard, defusedxml, Zod env validation, structured Pino logging** — the right tool choices in their respective places. Security posture is appropriate for a trusted local network tool.

---

## 2. What's Architecturally Weak or Risky

### Confirmed runtime bug: `check_overrides` (`jobs.py:233`)

The endpoint references `loop` and `client` that are never defined in scope. `LaminusSidecarClient` is imported but never instantiated. This is a `NameError` at runtime on any call to `POST /api/v1/jobs/check-overrides` — the endpoint that warns users about 3MF settings being overridden before slicing. A useful safety feature that is currently completely broken. Every user who tries it gets a 500 with no explanation.

### Laminus is a hard SPOF with no recovery

Every slicing job routes through one Laminus instance. All in-flight job state lives in a Python dict in memory. Restart Laminus for any reason (OOM, update, crash) and every active slice job silently disappears. Themis polls for up to 620 seconds, eventually times out, marks the job failed. The catalog then takes up to 5 minutes to rebuild. For a sub-10-printer personal farm this is manageable, but it's the system's primary fragility.

### Hand-rolled migration system with no version tracking or rollback

`database.py::_migrate()` runs an `_ALTERS` list of idempotent `ADD COLUMN` statements on every startup. The two table-recreate blocks (working around SQLite's no-`ALTER COLUMN`) are particularly fragile: if interrupted mid-run, the schema is partially migrated with no record of where it stopped. No migration version table, no rollback path.

### Ordinus→Themis hand-off produces an ungeneratable project *(workflow-breaking bug)*

Ordinus sends project items with `filament_profile_uuid: ''` and sets no `machine_uuid` or `process_uuid`. Themis's `generate_project` route raises HTTP 422 if any item lacks a filament profile. "Send to Themis" therefore produces a project the user must manually configure in Themis before hitting generate — and that requirement is not surfaced anywhere in the Ordinus UI. A first-time user following the natural flow hits a 422 with no explanation.

### Generated jobs carry no `project_id`

`generate_project` creates `Job` objects with no `project_id`. Once a project is generated, there's no database-backed way to answer "which jobs belong to this project?" or "what percentage is complete?" The per-item quantities that would power that view exist at generate time and are then discarded.

### Spoolman documented but not deployed

Spoolman is prominently described, its integration code is fully in place, but it's not in `docker-compose.yml`. Out of the box, filament tracking — including the filament-match blocking that is one of the queue engine's strongest features — requires the user to separately stand up Spoolman. A user following the docs will expect this to work automatically.

### Projects vs. Orders are actively confusing

Two disconnected grouping concepts for print work:

- **Projects**: STL files + profiles → auto plate-packing → queued jobs. No progress tracking.
- **Orders**: groupings with a progress bar derived from linked jobs via `jobs.order_id`.

Project-generated jobs don't set `order_id`, so they don't populate Orders. Orders have the progress bar; Projects don't. A user who generates a project can't use the Orders view to track completion. Two features serving related purposes without connecting to each other.

### Documentation has drifted significantly

`docs/agent/data-model.md` states the `projects` table was "removed" — two full screens (`ProjectsScreen`, `ProjectBuilderScreen`) are wired into `App.tsx` and actively used. The Ordinus README describes a `packages/` monorepo structure that doesn't exist on disk. A contributor following the docs is actively misled.

---

## 3. What's Working Well for the User

- **The STL-to-printed-part pipeline is mature.** Upload → assign profiles → queue → slice → print works. Filament-blocking means jobs wait rather than fail when material doesn't match — the right behavior for a farm where you swap between print runs.
- **The gridfinity workflow is strong.** Grid layout, broad bin catalog, per-bin customization, live BOM, favorites, reference-image overlay, PDF export. Param-hash caching means you don't regenerate a bin you've already made with the same parameters.
- **Multi-part project generation via plate packing** groups parts by `(filament_profile_uuid, color_hex)` and packs each group into a 3MF — automatically batching same-material parts, which is how you actually schedule a 50-part cosplay build.
- **Content-hash dedup** means you don't accumulate duplicates as you iterate.
- **Printer fleet management** with per-printer `queue_on` and `awaiting_plate_clear` is well-suited to a mixed fleet running unsupervised.

---

## 4. What the User Experience Is Missing

**No "what have I printed" view.** Completed jobs are filtered out of the queue broadcast, G-code is deleted on completion, there is no print history or archive.

**No project-level completion tracking.** The core missing feature for the stated use case. Quantities live in pre-generation project items and are then discarded. There is no persistent record of "6 of 10 bins printed." The Orders view has a progress bar, but project-generated jobs don't feed it.

**The Ordinus→Themis hand-off requires undocumented manual steps.** "Send to Themis" implies a flow that ends with printable jobs. It ends with an incomplete project that requires manual configuration with no user-facing explanation.

**No onboarding UI or guided setup.** OrcaSlicer profile setup requires container-level knowledge. Spoolman requires external setup. No in-app getting-started screen. No user-facing README.

**No push notifications.** A long print finishing while you're elsewhere means checking the UI manually.

**No cost estimation.** Filament weight, estimated print time, spool consumption — none surfaced before or after printing. Data already exists in the system via Spoolman and the slicer output.

**No mobile-friendly view.** Standing at the printer rack with a phone is the realistic operating posture: clear plate, check next job, unblock filament.

**Ordinus is exclusively gridfinity.** Cosplay props and arbitrary organizers can't use Ordinus's BOM generation or plate-packing.

---

## 5. Summary Assessment

This is better-engineered than most personal-project print farm managers. The queue engine, filament gating, plate-clear logic, and dedup implementation reflect real thought. The gridfinity tooling is genuinely capable. The Docker composition is production-quality for a self-hosted tool.

The problems concentrate in two areas: (1) several concrete bugs that break the advertised workflows — the Ordinus→Themis 422, the `check_overrides` NameError, the missing `project_id` — and (2) a structural gap where the system queues and executes prints well but cannot answer "how far along is this project?", which is the central question for the target use case.

Fix those and this is a genuinely strong personal print farm manager.
