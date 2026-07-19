"""
Integration test: project job list endpoint.

Creates a project, uploads a minimal STL, adds it as a project item,
generates (no eligible printers — geometry-only pack, no dispatch),
then asserts GET /api/v1/projects/{id}/jobs returns a valid list.

Run from host:
    pytest tests/e2e/test_project_jobs.py --integration
"""
from __future__ import annotations

import pytest
import requests
from helpers import THEMIS_URL, _minimal_stl


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def stl_file_id(http: requests.Session):
    """Upload a minimal STL and clean up after the test."""
    resp = http.post(
        f"{THEMIS_URL}/api/v1/files/upload",
        files={"file": ("project_jobs_e2e.stl", _minimal_stl(), "application/octet-stream")},
        data={"folder": "/Job Uploads"},
        timeout=15,
    )
    resp.raise_for_status()
    file_id = resp.json()["id"]
    yield file_id
    try:
        http.delete(f"{THEMIS_URL}/api/v1/files/{file_id}", timeout=10)
    except Exception:
        pass


# ── tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_project_job_list(
    http: requests.Session,
    stl_file_id: int,
    project_id: int,
) -> None:
    """
    Full project → generate → job list round trip:
      1. Add the uploaded STL as a project item.
      2. Generate with no eligible printers (geometry-only pack, 256×256×250mm bed).
      3. GET /api/v1/projects/{id}/jobs returns a list with the expected fields.
    """
    # 0. Purge any stale items / jobs left by a previous run (FK was off, so orphans survive)
    existing = http.get(f"{THEMIS_URL}/api/v1/projects/{project_id}/items", timeout=10)
    for item in (existing.json() if existing.ok else []):
        http.delete(
            f"{THEMIS_URL}/api/v1/projects/{project_id}/items/{item['id']}",
            timeout=10,
        )
    stale_jobs = http.get(f"{THEMIS_URL}/api/v1/projects/{project_id}/jobs", timeout=10)
    for job in (stale_jobs.json() if stale_jobs.ok else []):
        http.post(f"{THEMIS_URL}/api/v1/jobs/{job['id']}/cancel", timeout=10)

    # 1. Add STL item to project
    resp = http.post(
        f"{THEMIS_URL}/api/v1/projects/{project_id}/items",
        json={"file_id": stl_file_id, "quantity": 1,
              "filament_type": "any", "filament_color": "any"},
        timeout=10,
    )
    assert resp.status_code == 201, f"add item failed: {resp.status_code} {resp.text}"

    # 2. Generate (no printers → jobs created but not dispatched)
    resp = http.post(
        f"{THEMIS_URL}/api/v1/projects/{project_id}/generate",
        json={"eligible_printer_ids": []},
        timeout=60,
    )
    assert resp.status_code == 200, f"generate failed: {resp.status_code} {resp.text}"
    gen = resp.json()
    assert len(gen["jobs"]) >= 1, "expected at least one job from generate"

    # 3. List jobs for the project
    resp = http.get(f"{THEMIS_URL}/api/v1/projects/{project_id}/jobs", timeout=10)
    assert resp.status_code == 200, f"job list failed: {resp.status_code} {resp.text}"

    jobs = resp.json()
    assert isinstance(jobs, list), f"expected list, got {type(jobs)}"

    # Match only the jobs created by this generate call (orphans from prior runs may exist).
    generated_ids = {j["id"] for j in gen["jobs"]}
    new_jobs = [j for j in jobs if j["id"] in generated_ids]
    assert len(new_jobs) == len(gen["jobs"]), (
        f"expected {len(gen['jobs'])} newly-generated jobs in list, found {len(new_jobs)}"
    )

    required_fields = {
        "id", "plate_number", "status", "queue_position",
        "assigned_printer_id", "block_reason", "outcome",
        "created_at", "updated_at", "completed_at", "file_name", "total_parts",
    }
    for job in new_jobs:
        missing = required_fields - job.keys()
        assert not missing, f"job {job.get('id')} missing fields: {missing}"
        assert job["status"] == "queued", f"expected queued, got {job['status']!r}"
        assert job["file_name"] is not None, "file_name should not be None"
