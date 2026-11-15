"""
Integration test: pooled print groups (Themis' default packing strategy).

A print group with strategy "pool" is one queue entry: generate packs nothing and creates no jobs; each idle printer packs
its own next plate when it claims the group. The rest of this suite pins new groups to "fixed" (see
`conftest.group_strategy_default`), so this file is where the pool path is asserted.

Run from host:
    pytest tests/e2e/test_pool_groups.py --integration
"""
from __future__ import annotations

import pytest
import requests
from helpers import THEMIS_URL, _minimal_stl


@pytest.fixture
def stl_file_id(http: requests.Session):
    resp = http.post(
        f"{THEMIS_URL}/api/v1/files/upload",
        files={"file": ("pool_groups_e2e.stl", _minimal_stl(), "application/octet-stream")},
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


@pytest.mark.integration
def test_new_groups_default_to_pool(group_strategy_default: str | None) -> None:
    """The setting this suite found before pinning it: Themis ships with "pool"."""
    if group_strategy_default is None:
        pytest.skip("this Themis predates the default_group_strategy setting")
    assert group_strategy_default == "pool"


@pytest.mark.integration
def test_generating_a_pool_group_queues_the_pool_and_no_jobs(
    http: requests.Session,
    stl_file_id: int,
    project_id: int,
) -> None:
    """
    Project -> pool group -> generate:
      1. The project's Default Group is switched to "pool" (explicit, so the test holds whatever the setting is).
      2. Generate answers 200 with no jobs and no packed files, and names the group as pooled.
      3. The group reports its parts as left to print, and the project's job list is empty until a printer claims.
    """
    groups = http.get(f"{THEMIS_URL}/api/v1/projects/{project_id}/groups", timeout=10)
    assert groups.status_code == 200, f"groups failed: {groups.status_code} {groups.text}"
    group_id = groups.json()[0]["id"]
    resp = http.patch(
        f"{THEMIS_URL}/api/v1/projects/{project_id}/groups/{group_id}",
        json={"strategy": "pool"},
        timeout=10,
    )
    assert resp.status_code == 200, f"set strategy failed: {resp.status_code} {resp.text}"
    assert resp.json()["strategy"] == "pool"

    resp = http.post(
        f"{THEMIS_URL}/api/v1/projects/{project_id}/items",
        json={"file_id": stl_file_id, "quantity": 3, "filament_type": "any", "filament_color": "any"},
        timeout=10,
    )
    assert resp.status_code == 201, f"add item failed: {resp.status_code} {resp.text}"

    resp = http.post(
        f"{THEMIS_URL}/api/v1/projects/{project_id}/generate",
        json={"eligible_printer_ids": []},
        timeout=60,
    )
    assert resp.status_code == 200, f"generate failed: {resp.status_code} {resp.text}"
    gen = resp.json()
    assert gen["jobs"] == [], "a pool group is packed when a printer claims it, not at generate"
    assert gen["files"] == []
    pooled = [g for g in gen.get("groups", []) if g.get("pool")]
    assert [g["id"] for g in pooled] == [group_id]

    group = next(g for g in http.get(f"{THEMIS_URL}/api/v1/projects/{project_id}/groups", timeout=10).json()
                 if g["id"] == group_id)
    assert group["pool"] == {"done": 0, "printing": 0, "left": 3}

    jobs = http.get(f"{THEMIS_URL}/api/v1/projects/{project_id}/jobs", timeout=10)
    assert jobs.status_code == 200 and jobs.json() == []
