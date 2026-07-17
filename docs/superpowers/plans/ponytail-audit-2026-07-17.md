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
| T-4 | delete | done | `check_staleness` removed from base + all 3 vendor impls. ⚠️ Bambu/Snapmaker impls had real reconnect-on-silence logic (no caller, so already inert). Orphaned constants `STALE_TIMEOUT`, `STALE_RECONNECT_COOLDOWN`, `_last_reconnect_time`, `_last_message_time` left in clients — pending cleanup decision. |
| T-5 | delete | done | `FilamentProfilePicker.tsx` deleted. |
| T-6 | yagni | todo | `importlib` dotted-string `REGISTRY` + `_load_class` in `printer_client_factory.py` — only 3 static classes, all eagerly loaded anyway. Replace with plain `dict[str, type]`, drop `importlib`/`_load_class`. |
| T-7 | delete | done | `on_forced_offline` no-op removed from base. |
| T-8 | delete | done | `filament_profile_uuid` column removed from `models.py`. Commit `8a0937a`. |
| T-9 | delete | skipped | `PrinterCapabilities` fields `flow_calibration`, `skip_objects`, `multi_nozzle`, `file_timelapse` — all False on every vendor client, never read by name (frontend uses generic `Record<string,boolean>`). **Decision: keep spec; Todoist task created to audit real printer capabilities and wire integration if applicable.** |
| T-10 | yagni | todo | `PrinterResolution` and `JobResolution` are byte-identical TS interfaces. Collapse to one. [`frontend/src/api/laminus.ts:51–61`] |
| T-11 | shrink | todo | Stale `"copied verbatim from mesh_3mf_builder.py"` comment — those helpers no longer exist there. Drop it. [`snapmaker/remap.py:50–52`] |
| T-12 | yagni | todo | `ui.tsx` and `FleetScreen.tsx` import hardcoded `PRINTERS`/`JOBS` mock arrays instead of live fleet API — scaffolding to wire or remove. [`ui.tsx:3`, `FleetScreen.tsx:2`] |

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
| L-7 | yagni | todo | `_find_file_by_name` helper + `search_roots` fallback in `resolve_inheritance` never run (every caller passes `_name_index`). Require the index, drop fallback + helper. [`app/profile_catalog.py:16`, `:110–113`] |
| L-8 | delete | skipped | `flatten_profiles.py` (96 lines) — KEPT. `CLAUDE.md:31`, `README.md:75-78`, `CONTRIBUTING.md:33` document the `docker exec laminus python3 flatten_profiles.py` workflow; `app/main.py:1584` emits a runtime error pointing users to it. Live references confirm it's still needed. |
| L-9 | yagni | todo | 7 env-overridable knobs (`JOB_TTL_SECONDS`, `JOB_SWEEP_INTERVAL_SECONDS`, `SLICE_TIMEOUT_SECONDS`, `ARRANGE_TIMEOUT_SECONDS`, `MAX_CONCURRENT_JOBS`, `USER_CONFIG_DIR`, `SYSTEM_PROFILES_DIR`) nothing in compose/Dockerfile/entrypoint ever sets. Inline as constants. [`app/main.py:23–35`] |
| L-10 | shrink | todo | `_build_model_xml` and `_object_model_xml` are near-identical mesh→XML emitters. Extract one `_mesh_body(tris)` helper. ~15 lines saved. [`app/stl_to_3mf.py:71`, `:105`] |
| L-11 | yagni | todo | `thumbnail_endpoint: True` in `/api/health` — hardcoded sentinel that can never be false. Delete once Themis stops checking it. [`app/main.py:1671`] |
| L-12 | yagni | todo | Process `speed` and machine `nozzle_diameter`/`bed_size_x/y` collected into catalog entries but no server-side endpoint/builder/filter consumes them. Drop if not a public contract. [`app/profile_catalog.py:253`, `:216–217`] |

---

## Ordinus (O-#)

| ID | Tag | Status | Finding |
|----|-----|--------|---------|
| O-1 | delete | done | `StaticAdapter` + its branch in `DataSourceContext.tsx` — deleted. Note: `adapter?` prop kept (test harness uses it). Commit `926336a` on `feat/story-improvements`. |
| O-2 | yagni | todo | Collapse `DataSourceAdapter` interface — with `StaticAdapter` gone (O-1), one implementor remains. Drop interface + `adapter?` injection seam; import `ApiAdapter` directly. [`app/src/api/adapters/types.ts:11`] Note: `adapter?` prop still present (see O-1). |
| O-3 | delete | done | Dialog wrappers `openDialog`/`closeDialog`/`toggleDialog` removed from `useDialogState.ts`; `'load'` and `'admin'` dropped from `DialogName`/`DialogState`/reducer. 2 test files updated. |
| O-4 | delete | done | `server/src/db/client.ts` shim deleted. 5 importers updated to `db/connection`. |
| O-5 | delete | done | `Request.user` field removed from `express.d.ts`; `requestId` kept. |
| O-6 | shrink | todo | Redundant `wallPatternEnabled` if/else — both branches set `result.wallPattern` to same value. Collapse to two assignments. [`app/src/utils/generatorParams.ts:26`] |
| O-7 | yagni | todo | `migrate-cli.ts` creates its own `libsql` client, duplicating logic already in `connection.ts`. Import `client` from `connection`. [`server/src/db/migrate-cli.ts:5`] |
| O-8 | shrink | todo | `LibraryInfo.path` always `''` from `ApiAdapter` (only meaningful in `StaticAdapter`, deleted by O-1). Drop the field. [`app/src/api/adapters/types.ts:6`] |

---

## Cross-cutting (C-#)

| ID | Tag | Status | Finding |
|----|-----|--------|---------|
| C-0 | docs | todo | Update documentation (including CLAUDE.md) for each app to reflect all changes made in this audit. |

---

## Summary

- **Total findings:** 32 (T: 12, L: 12, O: 8)
- **In-progress (subagents running):** T-1–5, T-7–8, L-1–4, L-8, O-1, O-3–5
- **Skipped:** T-9 (keeping spec; Todoist task to audit real printer capabilities)
- **Todo:** T-6, T-10–12, L-5–7, L-9–12, O-2, O-6–8
- **Net estimate:** ~−1,800 lines, −2 deps (jinja2, aiofiles in Laminus)
