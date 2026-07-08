# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Commands

```bash
# Start the full stack (Orca sidecar + Themis backend)
docker compose up

# Run the E2E integration test (stack must be running)
pytest tests/e2e/test_centauri_slice.py --integration

# Override the Themis URL (default matches HOST_PORT in .env)
THEMIS_URL=http://localhost:8001 pytest tests/e2e/ --integration
```

## Architecture

Omnibus is the **orchestration repo** — it owns `docker-compose.yml` and cross-service E2E tests. The actual application code lives in two sibling repos:

| Repo | Role | Default internal port |
|---|---|---|
| `../themis` | Print queue, job lifecycle, frontend, API | 8000 (host: `HOST_PORT` from `.env`) |
| `../orca` | OrcaSlicer sidecar — profile catalog, slicing, packing | 5000 (internal only) |

Both services build from their own repos (`build: ../themis`, `build: ../orca`). Themis talks to Orca at `http://orca:5000` via `ORCA_SIDECAR_URL`. Orca must pass its healthcheck before Themis starts (`depends_on: condition: service_healthy`).

See `docs/slicing-flow.md` for the full slicing pipeline and `docs/agent/README.md` for the doc ownership map.

## Spec & Plans
- Design specs: `docs/superpowers/specs/`
- Implementation plans: `docs/superpowers/plans/`
- Agent reference docs: `docs/agent/`
