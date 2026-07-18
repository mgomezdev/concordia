"""Contract and parity tests for the Laminus mock.

Contract tests (always run, no stack needed):
    pytest tests/unit/test_laminus_contract.py

Parity tests (--integration, LAMINUS_URL pointing at a running Laminus/mock-laminus):
    LAMINUS_URL=http://localhost:5100 pytest tests/unit/test_laminus_contract.py --integration

Parity checks assert: real service response CONTAINS at least the fields the mock
declares (superset check). Values differ; structure must match.
"""
from __future__ import annotations

import importlib.util
import io
import json
import struct
import zipfile
from pathlib import Path
from typing import Callable

import pytest
import requests

# ---------------------------------------------------------------------------
# Load mock app via importlib — works regardless of sys.path / __init__.py
# ---------------------------------------------------------------------------
fastapi = pytest.importorskip("fastapi", reason="fastapi required for contract tests")
from fastapi.testclient import TestClient  # noqa: E402  (after importorskip)

_MOCKS_DIR = Path(__file__).parents[2] / "tests" / "e2e" / "mocks"


def _load_mock(name: str):
    spec = importlib.util.spec_from_file_location(name, _MOCKS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_lam = _load_mock("laminus_mock")
_tc = TestClient(_lam.app, raise_server_exceptions=True)

MACHINE_UUID = _lam.MACHINE_UUID
PROCESS_UUID = _lam.PROCESS_UUID
FILAMENT_UUID = _lam.FILAMENT_UUID


# ---------------------------------------------------------------------------
# Shared constants — the minimum fields Themis reads from each endpoint
# ---------------------------------------------------------------------------

HEALTH_FIELDS = {"status", "catalog_loaded", "catalog_building", "active_jobs"}

MACHINE_FIELDS = {
    "uuid", "name", "manufacturer", "model", "nozzle",
    "bed_size_x", "bed_size_y", "extruder_count",
}
PROCESS_FIELDS = {"uuid", "name", "layer_height", "compatible_printers"}
FILAMENT_FIELDS = {"uuid", "name", "filament_type", "compatible_printers"}

SLICE_START_FIELDS = {"job_id", "status"}
SLICE_STATUS_FIELDS = {"job_id", "status", "output_format", "sliced_file", "error"}
KNOWN_PROFILE_FIELDS = {
    "machine_uuid", "machine_name",
    "process_uuid", "process_name",
    "filament_uuid", "filament_name",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stl() -> bytes:
    """Minimal valid binary STL for upload tests."""
    buf = io.BytesIO()
    buf.write(b"contract-test".ljust(80))
    buf.write(struct.pack("<I", 1))
    for f in (0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 10.0, 0.0, 0.0, 0.0, 10.0, 0.0):
        buf.write(struct.pack("<f", f))
    buf.write(struct.pack("<H", 0))
    return buf.getvalue()


def _minimal_3mf() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr(
            "Metadata/project_settings.config",
            json.dumps({"printable_area": ["0x0", "200x0", "200x200", "0x200"], "printable_height": 200}),
        )
    return buf.getvalue()


def _missing(required: set, actual: dict) -> set:
    return required - actual.keys()


# ---------------------------------------------------------------------------
# ── /api/health ──────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def test_health_200():
    r = _tc.get("/api/health")
    assert r.status_code == 200


def test_health_fields():
    data = _tc.get("/api/health").json()
    assert not _missing(HEALTH_FIELDS, data), f"Missing: {_missing(HEALTH_FIELDS, data)}"


def test_health_catalog_ready():
    data = _tc.get("/api/health").json()
    assert data["catalog_loaded"] is True
    assert data["catalog_building"] is False
    assert isinstance(data["active_jobs"], int)


# ---------------------------------------------------------------------------
# ── /api/profiles ────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def test_profiles_top_level_keys():
    data = _tc.get("/api/profiles").json()
    assert {"machine", "process", "filament"} <= data.keys()


def test_profiles_non_empty():
    data = _tc.get("/api/profiles").json()
    assert len(data["machine"]) >= 1
    assert len(data["process"]) >= 1
    assert len(data["filament"]) >= 1


def test_profiles_machine_fields():
    m = _tc.get("/api/profiles").json()["machine"][0]
    assert not _missing(MACHINE_FIELDS, m), f"Machine missing: {_missing(MACHINE_FIELDS, m)}"


def test_profiles_process_fields():
    p = _tc.get("/api/profiles").json()["process"][0]
    assert not _missing(PROCESS_FIELDS, p), f"Process missing: {_missing(PROCESS_FIELDS, p)}"


def test_profiles_filament_fields():
    f = _tc.get("/api/profiles").json()["filament"][0]
    assert not _missing(FILAMENT_FIELDS, f), f"Filament missing: {_missing(FILAMENT_FIELDS, f)}"


def test_profile_detail_by_uuid():
    for uid in (MACHINE_UUID, PROCESS_UUID, FILAMENT_UUID):
        r = _tc.get(f"/api/profiles/{uid}")
        assert r.status_code == 200
        assert r.json()["uuid"] == uid


def test_profile_detail_404():
    assert _tc.get("/api/profiles/no-such-uuid").status_code == 404


def test_merged_config_returns_dict():
    r = _tc.post(
        "/api/profiles/merged-config",
        json={
            "machine_uuid": MACHINE_UUID,
            "process_uuid": PROCESS_UUID,
            "filament_uuids": [FILAMENT_UUID],
        },
    )
    assert r.status_code == 200
    assert isinstance(r.json(), dict)
    assert len(r.json()) > 0


# ---------------------------------------------------------------------------
# ── /api/test/known-profile ──────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def test_known_profile_fields():
    r = _tc.get("/api/test/known-profile")
    assert r.status_code == 200
    assert not _missing(KNOWN_PROFILE_FIELDS, r.json()), \
        f"Missing: {_missing(KNOWN_PROFILE_FIELDS, r.json())}"


def test_known_profile_uuids_match_catalog():
    kp = _tc.get("/api/test/known-profile").json()
    # Each UUID should be retrievable from /api/profiles/{uuid}
    for field, uuid_val in [
        ("machine_uuid", kp["machine_uuid"]),
        ("process_uuid", kp["process_uuid"]),
        ("filament_uuid", kp["filament_uuid"]),
    ]:
        r = _tc.get(f"/api/profiles/{uuid_val}")
        assert r.status_code == 200, f"{field} {uuid_val!r} not in catalog"


# ---------------------------------------------------------------------------
# ── /api/slice/* ─────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def _start_job() -> str:
    r = _tc.post(
        "/api/slice/start",
        files={"file": ("m.stl", _stl(), "application/octet-stream")},
        data={
            "machine_uuid": MACHINE_UUID,
            "process_uuid": PROCESS_UUID,
            "filament_uuids": json.dumps([FILAMENT_UUID]),
            "plate": "1",
        },
    )
    assert r.status_code == 200
    return r.json()["job_id"]


def test_slice_start_fields():
    r = _tc.post(
        "/api/slice/start",
        files={"file": ("m.stl", _stl(), "application/octet-stream")},
        data={
            "machine_uuid": MACHINE_UUID,
            "process_uuid": PROCESS_UUID,
            "filament_uuids": json.dumps([FILAMENT_UUID]),
            "plate": "1",
        },
    )
    assert r.status_code == 200
    assert not _missing(SLICE_START_FIELDS, r.json())
    assert isinstance(r.json()["job_id"], str) and r.json()["job_id"]


def test_slice_prepared_fields():
    r = _tc.post(
        "/api/slice/prepared",
        files={"file": ("m.3mf", _minimal_3mf(), "application/octet-stream")},
        data={"plate": "1"},
    )
    assert r.status_code == 200
    assert "job_id" in r.json()


def test_slice_status_completes_immediately():
    """Mock contract: status must be 'completed' on the first poll — no waiting."""
    job_id = _start_job()
    r = _tc.get(f"/api/slice/status/{job_id}")
    assert r.status_code == 200
    data = r.json()
    assert not _missing(SLICE_STATUS_FIELDS, data)
    assert data["status"] == "completed"
    assert data["error"] is None
    assert data["job_id"] == job_id


def test_slice_download_returns_non_empty_bytes():
    job_id = _start_job()
    r = _tc.get(f"/api/slice/download/{job_id}")
    assert r.status_code == 200
    assert len(r.content) > 0


def test_slice_download_evicts_job():
    job_id = _start_job()
    _tc.get(f"/api/slice/download/{job_id}")
    assert _tc.get(f"/api/slice/download/{job_id}").status_code == 404
    assert _tc.get(f"/api/slice/status/{job_id}").status_code == 404


def test_slice_status_404_for_unknown():
    assert _tc.get("/api/slice/status/nonexistent-id-xyz").status_code == 404


# ---------------------------------------------------------------------------
# ── /api/pack and /api/arrange ────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def test_pack_returns_valid_zip():
    r = _tc.post(
        "/api/pack",
        files=[("files", ("m.stl", _stl(), "application/octet-stream"))],
        data={"bed_x": "200", "bed_y": "200", "bed_z": "200"},
    )
    assert r.status_code == 200
    assert zipfile.is_zipfile(io.BytesIO(r.content)), "Pack response is not a valid ZIP/3MF"


def test_arrange_returns_valid_zip():
    r = _tc.post(
        "/api/arrange",
        files={"file": ("m.3mf", _minimal_3mf(), "application/octet-stream")},
        data={"arrange": "true", "orient": "true"},
    )
    assert r.status_code == 200
    assert zipfile.is_zipfile(io.BytesIO(r.content)), "Arrange response is not a valid ZIP/3MF"


# ---------------------------------------------------------------------------
# ── Parity: real Laminus must return at least what the mock returns ──────────
# (requires --integration and LAMINUS_URL env var)
# ---------------------------------------------------------------------------

import os as _os


def _real_laminus() -> "tuple[_RealClient | None, str]":
    url = _os.environ.get("LAMINUS_URL", "").rstrip("/")
    if not url:
        return None, "LAMINUS_URL not set"
    try:
        resp = requests.get(f"{url}/api/health", timeout=3)
        if resp.status_code != 200:
            return None, f"LAMINUS_URL returned {resp.status_code}"
    except Exception as e:
        return None, f"LAMINUS_URL not reachable: {e}"
    return _RealClient(url), ""


class _RealClient:
    def __init__(self, base: str):
        self._base = base

    def get(self, path: str, **kw) -> requests.Response:
        return requests.get(f"{self._base}{path}", timeout=kw.pop("timeout", 5), **kw)

    def post(self, path: str, **kw) -> requests.Response:
        return requests.post(f"{self._base}{path}", timeout=kw.pop("timeout", 15), **kw)


@pytest.mark.integration
def test_parity_health():
    real, reason = _real_laminus()
    if real is None:
        pytest.skip(reason)
    data = real.get("/api/health").json()
    assert not _missing(HEALTH_FIELDS, data), f"Real /api/health missing: {_missing(HEALTH_FIELDS, data)}"


@pytest.mark.integration
def test_parity_profiles_machine():
    real, reason = _real_laminus()
    if real is None:
        pytest.skip(reason)
    data = real.get("/api/profiles").json()
    assert data.get("machine"), "Real /api/profiles returned no machine entries"
    m = data["machine"][0]
    assert not _missing(MACHINE_FIELDS, m), f"Real machine entry missing: {_missing(MACHINE_FIELDS, m)}"


@pytest.mark.integration
def test_parity_profiles_process():
    real, reason = _real_laminus()
    if real is None:
        pytest.skip(reason)
    data = real.get("/api/profiles").json()
    assert data.get("process"), "Real /api/profiles returned no process entries"
    p = data["process"][0]
    assert not _missing(PROCESS_FIELDS, p), f"Real process entry missing: {_missing(PROCESS_FIELDS, p)}"


@pytest.mark.integration
def test_parity_profiles_filament():
    real, reason = _real_laminus()
    if real is None:
        pytest.skip(reason)
    data = real.get("/api/profiles").json()
    assert data.get("filament"), "Real /api/profiles returned no filament entries"
    f = data["filament"][0]
    assert not _missing(FILAMENT_FIELDS, f), f"Real filament entry missing: {_missing(FILAMENT_FIELDS, f)}"


@pytest.mark.integration
def test_parity_known_profile():
    real, reason = _real_laminus()
    if real is None:
        pytest.skip(reason)
    r = real.get("/api/test/known-profile")
    if r.status_code == 503:
        pytest.skip("Real Laminus catalog not ready")
    assert r.status_code == 200, f"Real /api/test/known-profile returned {r.status_code}"
    assert not _missing(KNOWN_PROFILE_FIELDS, r.json()), \
        f"Real /api/test/known-profile missing: {_missing(KNOWN_PROFILE_FIELDS, r.json())}"


@pytest.mark.integration
def test_parity_slice_lifecycle():
    """Real Laminus: slice/start + status responses must contain the required fields."""
    real, reason = _real_laminus()
    if real is None:
        pytest.skip(reason)
    kp_r = real.get("/api/test/known-profile")
    if kp_r.status_code != 200:
        pytest.skip("Real /api/test/known-profile not available — cannot resolve valid UUIDs")
    kp = kp_r.json()
    r = real.post(
        "/api/slice/start",
        files={"file": ("contract.stl", _stl(), "application/octet-stream")},
        data={
            "machine_uuid": kp["machine_uuid"],
            "process_uuid": kp["process_uuid"],
            "filament_uuids": json.dumps([kp["filament_uuid"]]),
            "plate": "1",
        },
    )
    assert r.status_code == 200, f"Real slice/start returned {r.status_code}: {r.text[:200]}"
    assert not _missing(SLICE_START_FIELDS, r.json()), \
        f"Real slice/start missing: {_missing(SLICE_START_FIELDS, r.json())}"
    job_id = r.json()["job_id"]
    rs = real.get(f"/api/slice/status/{job_id}")
    assert rs.status_code == 200
    assert not _missing(SLICE_STATUS_FIELDS, rs.json()), \
        f"Real slice/status missing: {_missing(SLICE_STATUS_FIELDS, rs.json())}"
