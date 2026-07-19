from __future__ import annotations

import uuid

import pytest
import requests

from helpers import LAMINUS_URL, THEMIS_URL, _fetch_known_profile


@pytest.fixture(scope="session")
def http(request) -> requests.Session:
    s = requests.Session()
    try:
        s.get(f"{THEMIS_URL}/api/v1/health", timeout=5).raise_for_status()
    except Exception as exc:
        pytest.skip(f"Themis not reachable at {THEMIS_URL}: {exc}")
    return s


@pytest.fixture(scope="session", autouse=True)
def seed_test_data(request) -> None:
    """Seed Themis with known printers before any test runs. No-op without --integration."""
    if not request.config.getoption("--integration", default=False):
        return

    from seed import PRINTERS

    s = requests.Session()
    try:
        s.get(f"{THEMIS_URL}/api/v1/health", timeout=5).raise_for_status()
    except Exception as exc:
        pytest.skip(f"Themis not reachable for seeding: {exc}")

    try:
        known_profile = _fetch_known_profile(LAMINUS_URL)
    except Exception:
        known_profile = {}

    resp = s.get(f"{THEMIS_URL}/api/v1/printers", timeout=10)
    resp.raise_for_status()
    existing = {p["name"]: p["id"] for p in resp.json()}

    for pdef in PRINTERS:
        if pdef["name"] in existing:
            printer_id = existing[pdef["name"]]
        else:
            payload = {k: v for k, v in pdef.items() if not k.startswith("_")}
            if pdef.get("_inject_laminus_profiles") and known_profile.get("machine_name"):
                payload["orca_printer_profiles"] = [known_profile["machine_name"]]
                payload["current_orca_printer_profile"] = known_profile["machine_name"]
            resp = s.post(f"{THEMIS_URL}/api/v1/printers", json=payload, timeout=10)
            resp.raise_for_status()
            printer_id = resp.json()["id"]
        if pdef.get("_queue_on") is False:
            s.patch(
                f"{THEMIS_URL}/api/v1/printers/{printer_id}",
                json={"queue_on": False},
                timeout=10,
            ).raise_for_status()


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
