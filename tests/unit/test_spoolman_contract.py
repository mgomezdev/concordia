"""Contract and parity tests for the Spoolman mock.

Contract tests (always run):
    pytest tests/unit/test_spoolman_contract.py

Parity tests (--integration, SPOOLMAN_URL pointing at a running Spoolman/mock-spoolman):
    SPOOLMAN_URL=http://localhost:7912 pytest tests/unit/test_spoolman_contract.py --integration
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
import requests

fastapi = pytest.importorskip("fastapi", reason="fastapi required for contract tests")
from fastapi.testclient import TestClient  # noqa: E402

_MOCKS_DIR = Path(__file__).parents[2] / "tests" / "e2e" / "mocks"
_spec = importlib.util.spec_from_file_location("spoolman_mock", _MOCKS_DIR / "spoolman_mock.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_tc = TestClient(_mod.app, raise_server_exceptions=True)

# ---------------------------------------------------------------------------
# Fields Themis reads from Spoolman responses (see spoolman_service.py)
# ---------------------------------------------------------------------------

INFO_FIELDS = {"version"}

# spoolman_service.fetch_filaments iterates the list; Themis reads .get("extra")
# catalog_utils reads filament["id"], filament["extra"]["orca_profiles"]
FILAMENT_FIELDS = {"id", "name", "material", "extra"}

# spoolman_service.fetch_spools iterates; Themis reads spool["filament"]["extra"]
SPOOL_FIELDS = {"id", "filament", "remaining_weight"}


def _missing(required: set, actual: dict) -> set:
    return required - actual.keys()


# ---------------------------------------------------------------------------
# ── /api/v1/info ─────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def test_info_200():
    r = _tc.get("/api/v1/info")
    assert r.status_code == 200


def test_info_has_version():
    data = _tc.get("/api/v1/info").json()
    assert "version" in data
    assert isinstance(data["version"], str)


# ---------------------------------------------------------------------------
# ── /api/v1/filament ─────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def test_list_filaments_200():
    r = _tc.get("/api/v1/filament")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


def test_filament_fields():
    f = _tc.get("/api/v1/filament").json()[0]
    assert not _missing(FILAMENT_FIELDS, f), f"Filament missing: {_missing(FILAMENT_FIELDS, f)}"


def test_filament_extra_is_dict():
    f = _tc.get("/api/v1/filament").json()[0]
    assert isinstance(f["extra"], dict)


def test_get_filament_by_id():
    filaments = _tc.get("/api/v1/filament").json()
    fid = filaments[0]["id"]
    r = _tc.get(f"/api/v1/filament/{fid}")
    assert r.status_code == 200
    assert r.json()["id"] == fid


def test_get_filament_404():
    r = _tc.get("/api/v1/filament/99999")
    assert r.status_code == 404


def test_patch_filament_updates_extra():
    filaments = _tc.get("/api/v1/filament").json()
    fid = filaments[0]["id"]
    payload = {"extra": {"orca_profiles": '{"My Printer": ["PLA"]}'}}
    r = _tc.patch(f"/api/v1/filament/{fid}", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["extra"].get("orca_profiles") == '{"My Printer": ["PLA"]}'


def test_patch_filament_404():
    r = _tc.patch("/api/v1/filament/99999", json={"extra": {}})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# ── /api/v1/spool ────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def test_list_spools_200():
    r = _tc.get("/api/v1/spool")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


def test_spool_fields():
    s = _tc.get("/api/v1/spool").json()[0]
    assert not _missing(SPOOL_FIELDS, s), f"Spool missing: {_missing(SPOOL_FIELDS, s)}"


def test_spool_filament_has_extra():
    """Themis reads spool.filament.extra to find orca_profiles."""
    s = _tc.get("/api/v1/spool").json()[0]
    assert "filament" in s
    assert "extra" in s["filament"]


# ---------------------------------------------------------------------------
# ── /api/v1/spool/{id}/use ───────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def test_record_spool_use_decrements_weight():
    spools = _tc.get("/api/v1/spool").json()
    sid = spools[0]["id"]
    before = spools[0]["remaining_weight"]
    r = _tc.put(f"/api/v1/spool/{sid}/use", json={"use_weight": 10.0})
    assert r.status_code == 200
    assert r.json()["remaining_weight"] == pytest.approx(before - 10.0, abs=0.01)


def test_record_spool_use_404():
    r = _tc.put("/api/v1/spool/99999/use", json={"use_weight": 1.0})
    assert r.status_code == 404


def test_record_spool_use_no_negative_weight():
    spools = _tc.get("/api/v1/spool").json()
    sid = spools[0]["id"]
    r = _tc.put(f"/api/v1/spool/{sid}/use", json={"use_weight": 999999.0})
    assert r.status_code == 200
    assert r.json()["remaining_weight"] >= 0.0


# ---------------------------------------------------------------------------
# ── Parity: real Spoolman must return at least what the mock returns ──────────
# ---------------------------------------------------------------------------

def _real_spoolman():
    url = os.environ.get("SPOOLMAN_URL", "").rstrip("/")
    if not url:
        return None, "SPOOLMAN_URL not set"
    try:
        resp = requests.get(f"{url}/api/v1/info", timeout=3)
        if resp.status_code != 200:
            return None, f"SPOOLMAN_URL /api/v1/info returned {resp.status_code}"
    except Exception as e:
        return None, f"SPOOLMAN_URL not reachable: {e}"

    class _Client:
        def get(self, path, **kw):
            return requests.get(f"{url}{path}", timeout=kw.pop("timeout", 5), **kw)
        def patch(self, path, **kw):
            return requests.patch(f"{url}{path}", timeout=kw.pop("timeout", 5), **kw)

    return _Client(), ""


@pytest.mark.integration
def test_parity_info():
    real, reason = _real_spoolman()
    if real is None:
        pytest.skip(reason)
    data = real.get("/api/v1/info").json()
    assert "version" in data, "Real Spoolman /api/v1/info missing 'version'"


@pytest.mark.integration
def test_parity_filament_fields():
    real, reason = _real_spoolman()
    if real is None:
        pytest.skip(reason)
    data = real.get("/api/v1/filament").json()
    if not data:
        pytest.skip("Real Spoolman has no filaments")
    f = data[0]
    assert not _missing(FILAMENT_FIELDS, f), f"Real filament missing: {_missing(FILAMENT_FIELDS, f)}"


@pytest.mark.integration
def test_parity_spool_fields():
    real, reason = _real_spoolman()
    if real is None:
        pytest.skip(reason)
    data = real.get("/api/v1/spool").json()
    if not data:
        pytest.skip("Real Spoolman has no spools")
    s = data[0]
    assert not _missing(SPOOL_FIELDS, s), f"Real spool missing: {_missing(SPOOL_FIELDS, s)}"


@pytest.mark.integration
def test_parity_spool_filament_has_extra():
    real, reason = _real_spoolman()
    if real is None:
        pytest.skip(reason)
    data = real.get("/api/v1/spool").json()
    if not data:
        pytest.skip("Real Spoolman has no spools")
    s = data[0]
    assert "filament" in s, "Real spool missing 'filament'"
    assert "extra" in s["filament"], "Real spool.filament missing 'extra' dict (needed for orca_profiles)"
