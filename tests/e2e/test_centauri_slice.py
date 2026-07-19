"""
Integration test: slice for Elegoo Centauri Carbon via the full Concordia stack.

Profiles under test:
  machine:  Elegoo Centauri Carbon 0.4 nozzle
  process:  0.16mm Optimal @Elegoo CC 0.4 nozzle
  filament: Elegoo PLA @ECC  (basic Elegoo PLA)

The placeholder Elegoo Centauri Carbon (placeholder) printer is seeded at
startup with ip_address 192.0.2.1 (TEST-NET-1, never routes), so the job
parks at "sliced" instead of attempting an upload.

Run from inside the Docker network (port 8000 is internal):
    docker exec concordia-themis-1 sh -c "
        pip install pytest requests -q &&
        cd /app &&
        THEMIS_URL=http://localhost:8000 pytest /e2e/test_centauri_slice.py --integration -v
    "

Or from the host (HOST_PORT=8001 as set in .env):
    pytest tests/e2e/test_centauri_slice.py --integration
    # or with explicit URL:
    THEMIS_URL=http://localhost:8001 pytest tests/e2e/test_centauri_slice.py --integration
"""

from __future__ import annotations

import pytest
import requests
from helpers import (
    FILAMENT_PROFILE,
    MACHINE_PROFILE,
    PROCESS_PROFILE,
    THEMIS_URL,
    _drain_active_jobs_for_printer,
    _find_centauri_placeholder_id,
    _minimal_stl,
)

SLICE_TIMEOUT_S = 300   # slicing can take 2–3 min inside Docker


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def uploaded_file_id(http: requests.Session) -> int:
    """Upload the test STL and return its ID; clean up after the test."""
    stl = _minimal_stl()
    resp = http.post(
        f"{THEMIS_URL}/api/v1/files/upload",
        files={"file": ("centauri_e2e_test.stl", stl, "application/octet-stream")},
        data={"folder": "/Job Uploads"},
        timeout=15,
    )
    resp.raise_for_status()
    file_id = resp.json()["id"]
    yield file_id
    # best-effort cleanup — may fail if FK constraints prevent deletion
    try:
        http.delete(f"{THEMIS_URL}/api/v1/files/{file_id}", timeout=10)
    except Exception:
        pass


# ── test ─────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_centauri_slice_reaches_sliced_status(
    http: requests.Session,
    uploaded_file_id: int,
    project_id: int,
) -> None:
    """
    Full slice pipeline for the Elegoo Centauri Carbon placeholder:
      1. Pack the STL into a 3MF via generate (no printers — geometry only)
      2. Create a job against the 3MF with explicit Centauri profiles
      3. verify-slice: queued → slicing → sliced  (no upload, printer is offline)
    """
    printer_id = _find_centauri_placeholder_id(http)
    if printer_id is None:
        pytest.fail("Placeholder Elegoo Centauri Carbon printer not found — is Themis seeding it?")

    # Cancel any leftover active jobs for this printer so the queue engine
    # doesn't skip slicing the new test job (one pending sliced-job-per-printer).
    _drain_active_jobs_for_printer(http, printer_id)

    # Confirm profiles are served by the Laminus sidecar for this printer
    resp = http.get(f"{THEMIS_URL}/api/v1/printers/{printer_id}/profiles", timeout=15)
    resp.raise_for_status()
    profiles = resp.json()
    assert PROCESS_PROFILE in profiles["print_profiles"], (
        f"{PROCESS_PROFILE!r} not in print_profiles: {profiles['print_profiles']}"
    )
    assert FILAMENT_PROFILE in profiles["filament_profiles"], (
        f"{FILAMENT_PROFILE!r} not in filament_profiles: {profiles['filament_profiles']}"
    )

    # Add the STL to the project
    resp = http.post(
        f"{THEMIS_URL}/api/v1/projects/{project_id}/items",
        json={
            "file_id": uploaded_file_id,
            "quantity": 1,
            "filament_type": "any",
            "filament_color": "any",
        },
        timeout=10,
    )
    assert resp.status_code == 201, f"add item failed: {resp.status_code} {resp.text}"

    # Generate with no printers: packs the STL into a 3MF without creating printer configs.
    resp = http.post(
        f"{THEMIS_URL}/api/v1/projects/{project_id}/generate",
        json={"eligible_printer_ids": []},
        timeout=60,
    )
    assert resp.status_code == 200, f"generate failed: {resp.status_code} {resp.text}"
    gen = resp.json()
    assert gen["files"], "generate returned no files — pack step failed"
    threemf_file_id = gen["files"][0]["id"]

    # Create a job against the 3MF with explicit Centauri profiles so verify-slice
    # has machine, process, and filament presets to resolve.
    resp = http.post(
        f"{THEMIS_URL}/api/v1/jobs",
        json={
            "uploaded_file_id": threemf_file_id,
            "plate_number": 1,
            "printer_configs": [
                {
                    "printer_id": printer_id,
                    "print_profile": PROCESS_PROFILE,
                    "filament_profile": FILAMENT_PROFILE,
                }
            ],
        },
        timeout=15,
    )
    resp.raise_for_status()
    job_id = resp.json()["id"]

    # verify-slice triggers OrcaSlicer directly on the packed 3MF.
    try:
        resp = http.post(
            f"{THEMIS_URL}/api/v1/jobs/{job_id}/verify-slice",
            json={"printer_id": printer_id},
            timeout=SLICE_TIMEOUT_S,
        )
        result = resp.json()
    finally:
        try:
            http.post(f"{THEMIS_URL}/api/v1/jobs/{job_id}/cancel", timeout=10)
        except Exception:
            pass

    assert result.get("ok") is True, (
        f"verify-slice failed for job {job_id}: {result.get('error')!r}\n"
        "Check Laminus logs: docker compose logs laminus | tail -50"
    )
