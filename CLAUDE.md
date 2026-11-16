# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Communication style
When reporting information, be extremely concise and sacrifice grammar for the sake of concision.


## Commands

```bash
# Start the full stack (Laminus sidecar + Themis backend)
docker compose up

# Run the E2E integration test (stack must be running)
pytest tests/e2e/test_centauri_slice.py --integration

# Override the Themis URL (default matches HOST_PORT in .env)
THEMIS_URL=http://localhost:8001 pytest tests/e2e/ --integration
```

## Architecture

Concordia is the **orchestration repo** — it owns `docker-compose.yml` and cross-service E2E tests. The actual application code lives in two sibling repos:

| Repo | Role | Default internal port |
|---|---|---|
| `../themis` | Print queue, job lifecycle, frontend, API | 8000 (host: `HOST_PORT` from `.env`) |
| `../laminus` | OrcaSlicer sidecar — profile catalog, slicing, packing | 5000 (internal only) |

Both services build from their own repos (`build: ../themis`, `build: ../laminus`). Themis talks to Laminus at `http://laminus:5000` via `LAMINUS_SIDECAR_URL`. Laminus must pass its healthcheck before Themis starts (`depends_on: condition: service_healthy`).

See `docs/slicing-flow.md` for the full slicing pipeline.

## Running the full e2e (real OrcaSlicer) locally — gotchas

Mirrors `.github/workflows/e2e-full.yml`. Verified green on develop 2026-11-15 (31 passed, 1 skipped, ~2 min after build).

1. **Don't switch branches in sibling repos.** Dev work lives there. For a develop run: `git worktree add --detach <scratch>/<repo> origin/develop` for concordia, themis, laminus, ordinus (siblings must share a parent dir; `docker-compose.local.yml` uses `../<repo>`). Fetch first. Remove worktrees after.
2. **For main/release checks: no worktrees, no `docker-compose.local.yml`.** Use `docker-compose.yml` + `docker-compose.ci.yml` only (pulls Docker Hub `:latest`, same as CI). Pin images via `THEMIS_IMAGE`/`LAMINUS_IMAGE`/`ORDINUS_IMAGE`. Check out concordia `main` for the suite.
3. **`.env` = copy of `.env.example`, never the repo's own `.env`** (that has Tailscale `THEMIS_BIND_IP` + different bootstrap key).
4. **Create `.ordinus-admin-password` BEFORE `compose up`** (CI step: `python -c "import secrets; from pathlib import Path; Path('.ordinus-admin-password').write_text(secrets.token_urlsafe(32))"`). If missing, Docker bind-mounts a *directory* → ordinus crashes `EISDIR` → "ordinus is unhealthy" → `up --wait` fails. If it already happened: delete the dir, create the file, `up -d --force-recreate --wait ordinus`.
5. **Port clashes with running dev stack** (`themis-dev` :8001, `laminus` :5000): set `HOST_PORT=8011` in `.env`, use `-p concordia-e2e`. `LAMINUS_PORT` irrelevant; ci override publishes laminus on **5100**.
6. **`mkdir -p .ci-cache/orcaslicer`** before up (ci override bind-mounts it; AppImage downloads on first run).
7. Command:
   ```
   docker compose -p concordia-e2e -f docker-compose.yml [-f docker-compose.local.yml  # develop only] -f docker-compose.ci.yml up -d --build --wait
   pip install -r requirements-test.txt && playwright install chromium
   THEMIS_URL=http://localhost:8011 HOST_PORT=8011 LAMINUS_URL=http://localhost:5100 ORDINUS_LAMINUS_URL=http://laminus:5000 pytest tests/e2e/ --integration -m integration -v
   docker compose -p concordia-e2e -f ... down -v
   ```
   Missing `ORDINUS_LAMINUS_URL` / `LAMINUS_URL` → ordinus tests hit mock hostnames / seeded printers get no profiles.
8. Known skip: `test_project_review_measures_stl_through_laminus` (reason not yet investigated).
9. Only themis, laminus, ordinus are in the stack; orca-plugin is not covered by this suite.

## Design Specs
- `docs/superpowers/specs/` — feature design documents
