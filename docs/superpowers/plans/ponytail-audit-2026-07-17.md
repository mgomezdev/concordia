# Ponytail Audit — 2026-07-17

Cross-repo over-engineering findings for Themis, Laminus, Ordinus.
Tags: `delete` `stdlib` `native` `yagni` `shrink`
Status: `todo` `in-progress` `done` `skipped`

---

## Themis (T-#)

| ID | Tag | Status | Finding |
|----|-----|--------|---------|
| T-1 | delete | done | Spike scripts deleted. Commit `330dfda`. |
| T-2 | delete | done | `ProfileService` + `test_profile_service.py` deleted. |
| T-3 | delete | done | Dead `ORDERS`, `PROCESS_PRESETS`, `getPrinter`, `getOrder`, `getPart` removed from `mock.ts` + test. |
| T-4 | delete | done | `check_staleness` removed from base + all 3 vendor impls. Orphaned constants `STALE_TIMEOUT`, `STALE_RECONNECT_COOLDOWN`, `_last_reconnect_time`, `_last_message_time` + `import time` cleaned up in bambu_mqtt.py + snapmaker_client.py. Commit `1cf5a77`. |
| T-5 | delete | done | `FilamentProfilePicker.tsx` deleted. |
| T-6 | yagni | done | `importlib` registry replaced with plain `dict[str, type]`. Commit `7ed7c2c` on `ponytail/yagni-shrink`. |
| T-7 | delete | done | `on_forced_offline` no-op removed from base. |
| T-8 | delete | done | `filament_profile_uuid` column removed from `models.py` + v009 migration to drop from DB. |
| T-9 | delete | skipped | `PrinterCapabilities` dead fields — keeping spec; Todoist task to audit real printer capabilities. |
| T-10 | yagni | done | `PrinterResolution` + `JobResolution` → `ProfileResolution`. Commit `7ed7c2c`. |
| T-11 | shrink | done | Stale `"copied verbatim"` comment removed. Commit `7ed7c2c`. |
| T-12 | yagni | done | Mock `PRINTERS`/`JOBS` scaffolding removed. FleetScreen current-job panel removed (live API shape incompatible — new feature ticket if needed). Commit `7ed7c2c`. |

---

## Laminus (L-#)

| ID | Tag | Status | Finding |
|----|-----|--------|---------|
| L-1 | delete | done | Two hand-maintained OpenAPI specs (`openapi.yaml`, `docs/laminus-openapi.yaml`, 273+891 lines) — never served; FastAPI auto-generates `/openapi.json`. |
| L-2 | delete | done | `find_profiles_in_config()` (~22 lines, `app/main.py:389`) — never called, superseded by `ProfileCatalog`. |
| L-3 | delete | done | `SliceConfig` pydantic model + `filaments_not_empty` validator (~22 lines, `app/main.py:207`) — no route references it. Also removed orphaned `field_validator` import. |
| L-4 | delete | done | `_STRIP_META` set (`app/profile_catalog.py:136`) — defined, never read. `_STRIP_KEYS` in `project_config_builder` is the live one. |
| L-5 | native | done | `jinja2` dropped from `requirements.txt`. Commit `5d4178f`. |
| L-6 | native | done | `aiofiles` dropped from `requirements.txt`. Commit `5d4178f`. |
| L-7 | yagni | done | `_find_file_by_name` + fallback removed; `_name_index` now required. 4 unit tests updated to pass index directly. Commit `906e277` on `ponytail/yagni-shrink`. |
| L-8 | delete | skipped | `flatten_profiles.py` (96 lines) — KEPT. `CLAUDE.md:31`, `README.md:75-78`, `CONTRIBUTING.md:33` document the `docker exec laminus python3 flatten_profiles.py` workflow; `app/main.py:1584` emits a runtime error pointing users to it. Live references confirm it's still needed. |
| L-9 | yagni | skipped | 7 env-overridable knobs — won't fix (useful for ops overrides even if unused today). |
| L-10 | shrink | done | `_mesh_body(tris, indent)` extracted; both emitters call it. ~11 lines removed. Commit `906e277`. |
| L-11 | yagni | done | `thumbnail_endpoint: True` removed — zero runtime code in Themis reads it (only a docs markdown spec). Commit `906e277`. |
| L-12 | yagni | skipped | Process speed / machine dims in catalog — won't fix (public contract surface, useful for future consumers). |

---

## Ordinus (O-#)

| ID | Tag | Status | Finding |
|----|-----|--------|---------|
| O-1 | delete | done | `StaticAdapter` + its branch in `DataSourceContext.tsx` — deleted. Note: `adapter?` prop kept (test harness uses it). Commit `926336a` on `feat/story-improvements`. |
| O-2 | yagni | skipped | `DataSourceAdapter` interface IS the test seam — 4 test files mock against it via `adapter?` prop. Collapsing would break mock typing. Keep. |
| O-3 | delete | done | Dialog wrappers `openDialog`/`closeDialog`/`toggleDialog` removed from `useDialogState.ts`; `'load'` and `'admin'` dropped from `DialogName`/`DialogState`/reducer. 2 test files updated. |
| O-4 | delete | done | `server/src/db/client.ts` shim deleted. 5 importers updated to `db/connection`. |
| O-5 | delete | done | `Request.user` field removed from `express.d.ts`; `requestId` kept. |
| O-6 | shrink | done | `wallPatternEnabled` if/else collapsed to two assignments. Commit `51f2a0e` on `ponytail/yagni-shrink`. |
| O-7 | yagni | done | `migrate-cli.ts` now imports `client` from `connection.ts`. Bonus: now handles `:memory:` correctly. Commit `51f2a0e`. |
| O-8 | shrink | done | `LibraryInfo.path` dropped from interface. 5 test construction sites + 1 assertion updated. Commit `51f2a0e`. |

---

## Cross-cutting (C-#)

| ID | Tag | Status | Finding |
|----|-----|--------|---------|
| C-0 | docs | done | CLAUDE.md updated in all 3 repos. Themis: migration runner, plain-dict registry. Laminus: ProfileCatalog replaces find_profiles_in_config. Ordinus: fix packages/ paths, remove auth/StaticAdapter, add customers/connection.ts. |

---

## Summary

- **Total findings:** 32 (T: 12, L: 12, O: 8)
- **In-progress (subagents running):** T-1–5, T-7–8, L-1–4, L-8, O-1, O-3–5
- **Skipped:** T-9 (keeping spec; Todoist task to audit real printer capabilities)
- **Todo:** T-6, T-10–12, L-5–7, L-9–12, O-2, O-6–8
- **Net estimate:** ~−1,800 lines, −2 deps (jinja2, aiofiles in Laminus)
