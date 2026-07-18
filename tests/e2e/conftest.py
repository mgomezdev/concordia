from __future__ import annotations

import uuid

import pytest
import requests

from helpers import THEMIS_URL


@pytest.fixture(scope="module")
def http() -> requests.Session:
    s = requests.Session()
    try:
        s.get(f"{THEMIS_URL}/api/v1/health", timeout=5).raise_for_status()
    except Exception as exc:
        pytest.skip(f"Themis not reachable at {THEMIS_URL}: {exc}")
    return s


@pytest.fixture
def project_id(http: requests.Session):
    resp = http.post(
        f"{THEMIS_URL}/api/v1/projects",
        json={"name": f"E2E Test {uuid.uuid4().hex[:6]}", "order_type": "internal"},
        timeout=10,
    )
    resp.raise_for_status()
    pid = resp.json()["id"]
    yield pid
    try:
        http.delete(f"{THEMIS_URL}/api/v1/projects/{pid}", timeout=10)
    except Exception:
        pass
