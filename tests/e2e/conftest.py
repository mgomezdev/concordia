from __future__ import annotations

import uuid

import pytest
import requests

from helpers import LAMINUS_URL, THEMIS_URL, _fetch_known_profile, authed_session


@pytest.fixture(scope="session")
def http(request) -> requests.Session:
    s = authed_session()
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

    s = authed_session()
    try:
        s.get(f"{THEMIS_URL}/api/v1/health", timeout=5).raise_for_status()
    except Exception as exc:
        pytest.skip(f"Themis not reachable for seeding: {exc}")

    resp = s.get(f"{THEMIS_URL}/api/v1/printers", timeout=10)
    resp.raise_for_status()
    existing = {p["name"]: p["id"] for p in resp.json()}

    for pdef in PRINTERS:
        if pdef["name"] in existing:
            printer_id = existing[pdef["name"]]
        else:
            payload = {k: v for k, v in pdef.items() if not k.startswith("_")}
            if pdef.get("_inject_laminus_profiles"):
                try:
                    known_profile = _fetch_known_profile(LAMINUS_URL, pdef.get("_known_machine_name"))
                except Exception:
                    known_profile = {}
                if known_profile.get("machine_name"):
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


@pytest.fixture(scope="session", autouse=True)
def group_strategy_default(request) -> str | None:
    """Pin new print groups to "fixed" (packed at generate) for this suite, and restore the setting afterwards.

    Themis' shipped default is "pool": generate queues the group and each printer packs its own plate when it claims it, so
    generate returns no jobs or files. The tests here cover the pack-at-generate pipeline (pack -> library file -> jobs ->
    slice), so they say so explicitly. Yields the value found before pinning (None on a Themis that predates the setting,
    which ignores the key and always packs at generate); `test_pool_groups.py` covers the pool path.
    """
    if not request.config.getoption("--integration", default=False):
        yield None
        return
    s = authed_session()
    url = f"{THEMIS_URL}/api/v1/settings/queue"
    try:
        before = s.get(url, timeout=10).json().get("default_group_strategy")
        s.put(url, json={"default_group_strategy": "fixed"}, timeout=10).raise_for_status()
    except Exception as exc:
        pytest.skip(f"Themis not reachable for settings: {exc}")
    yield before
    if before is not None:
        try:
            s.put(url, json={"default_group_strategy": before}, timeout=10)
        except Exception:
            pass


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
