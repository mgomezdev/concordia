# Omnibus — Agent Reference Index

<!-- Maintained by omnibus-docs-sync. Code is source of truth; this is a derived map. -->

## System Overview

Omnibus orchestrates two services — **Themis** (print queue + frontend, repo: `../themis`) and **Orca** (OrcaSlicer sidecar, repo: `../orca`) — via a single `docker-compose.yml`. Omnibus itself contains no application code: it owns cross-service concerns only (compose config, E2E tests, architecture docs). Themis talks to Orca internally at `http://orca:5000`; the host reaches Themis on `HOST_PORT` (default 8001, set in `.env`). Orca is not reachable from the host.

## Doc Ownership

| Changed path | Update |
|---|---|
| `docker-compose.yml` | Update `CLAUDE.md` Commands table if ports or env vars change |
| `.env` | Update `CLAUDE.md` default port reference and `tests/e2e/test_centauri_slice.py` default `THEMIS_URL` |
| `docs/slicing-flow.md` | Source of truth for the slicing pipeline; update when Themis or Orca changes the slice/profile API |
| `docs/superpowers/specs/` | Mark superseded specs when the implemented design diverges; point to `slicing-flow.md` or the relevant plan |
| `docs/superpowers/plans/` | Update **Status** line when a plan moves from Pending → Approved → Implemented |
| `tests/e2e/` | Update when Themis API routes change (job creation, file upload, printer endpoints) |

## Extension Points

| Seam | Where to plug in |
|---|---|
| New sidecar service (e.g. a second slicer) | Add a service block to `docker-compose.yml`; add `depends_on` to Themis if Themis calls it |
| New E2E test scenario | Add a file under `tests/e2e/`; use `@pytest.mark.integration` and the `http` session fixture from `test_centauri_slice.py` |
| New architecture doc | Drop a `.md` in `docs/`; add a row to the Doc Ownership table above |
| New implementation plan | Add to `docs/superpowers/plans/`; link from this file if it introduces a new service or integration seam |
