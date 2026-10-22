from __future__ import annotations

import io
import os
import struct

import requests

_themis_port = os.environ.get("HOST_PORT", "8001")
THEMIS_URL = os.environ.get("THEMIS_URL", f"http://localhost:{_themis_port}")

# Direct URL for Laminus/mock-laminus — used to read /api/v1/profiles.
# In docker-compose.test.yml mock-laminus is exposed on 5100; real Laminus is internal.
LAMINUS_URL = os.environ.get("LAMINUS_URL", "http://localhost:5100")

# Ordinus API — 3002 matches docker-compose.test.yml; override with ORDINUS_URL or ORDINUS_PORT.
_ordinus_port = os.environ.get("ORDINUS_PORT", "3002")
ORDINUS_URL = os.environ.get("ORDINUS_URL", f"http://localhost:{_ordinus_port}")

MACHINE_PROFILE  = "Elegoo Centauri Carbon 0.4 nozzle"
PROCESS_PROFILE  = "0.16mm Optimal @Elegoo CC 0.4 nozzle"
FILAMENT_PROFILE = "Elegoo PLA @ECC"

# Themis gates every /api/v1 route behind an API key (or a login session, or a
# THEMIS_LOCAL_NETWORKS peer); there is no open bootstrap window. THEMIS_BOOTSTRAP_KEY
# is Themis's break-glass full-scope key — set it in the same .env used by
# docker-compose.yml so the stack and the tests share one value.
THEMIS_API_KEY = os.environ.get("THEMIS_BOOTSTRAP_KEY", "")


def authed_session() -> requests.Session:
    """A requests.Session pre-authenticated with THEMIS_API_KEY, if set."""
    s = requests.Session()
    if THEMIS_API_KEY:
        s.headers.update({"X-Api-Key": THEMIS_API_KEY})
    return s


def _minimal_stl() -> bytes:
    """Valid binary STL tetrahedron (~10 mm) for test slicing."""
    triangles = [
        ((0, 0, -1), (0, 0, 0), (10, 0, 0), (0, 10, 0)),
        ((0, -1,  0), (0, 0, 0), (10, 0, 0), (0,  0, 10)),
        ((-1, 0,  0), (0, 0, 0), (0, 10, 0), (0,  0, 10)),
        ((1,  1,  1), (10, 0, 0), (0, 10, 0), (0,  0, 10)),
    ]
    buf = io.BytesIO()
    buf.write(b"Concordia e2e".ljust(80, b" "))
    buf.write(struct.pack("<I", len(triangles)))
    for normal, v1, v2, v3 in triangles:
        for coord in (*normal, *v1, *v2, *v3):
            buf.write(struct.pack("<f", coord))
        buf.write(struct.pack("<H", 0))
    return buf.getvalue()


def _find_centauri_placeholder_id(session: requests.Session | None = None) -> int | None:
    """Return the printer ID of the Elegoo Centauri Carbon placeholder, or None."""
    s = session or authed_session()
    resp = s.get(f"{THEMIS_URL}/api/v1/printers", timeout=10)
    resp.raise_for_status()
    for p in resp.json():
        if "Centauri Carbon" in p["name"] and "placeholder" in p["name"].lower():
            return p["id"]
    return None


def _drain_active_jobs_for_printer(session: requests.Session, printer_id: int) -> None:
    """Cancel any non-terminal jobs assigned to or targeting printer_id."""
    resp = session.get(f"{THEMIS_URL}/api/v1/jobs", timeout=10)
    resp.raise_for_status()
    active_statuses = {"queued", "blocked", "slicing", "sliced", "uploading", "printing"}
    for job in resp.json():
        if job["status"] not in active_statuses:
            continue
        if job.get("assigned_printer_id") == printer_id:
            session.post(f"{THEMIS_URL}/api/v1/jobs/{job['id']}/cancel", timeout=10)
            continue
        detail = session.get(f"{THEMIS_URL}/api/v1/jobs/{job['id']}/details", timeout=10)
        if detail.ok:
            cfgs = detail.json().get("printer_configs", [])
            if any(c.get("printer_id") == printer_id for c in cfgs):
                session.post(f"{THEMIS_URL}/api/v1/jobs/{job['id']}/cancel", timeout=10)


def _iter_v1_profiles(laminus_url: str, profile_type: str):
    """Yield every Laminus v1 catalog entry of `profile_type`, following cursors.

    GET /api/v1/profiles is cursor-paginated: {data: [...], page: {limit, next_cursor}}.
    Raises requests.HTTPError (v1 error envelope) while the catalog is not ready.
    """
    cursor = None
    while True:
        params = {"type": profile_type, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(f"{laminus_url}/api/v1/profiles", params=params, timeout=10)
        resp.raise_for_status()
        body = resp.json()
        yield from body["data"]
        cursor = body["page"]["next_cursor"]
        if not cursor:
            return


def _fetch_known_profile(laminus_url: str = LAMINUS_URL, machine_name: str | None = None) -> dict:
    """Return a machine/process/filament UUID triple from the Laminus v1 catalog.

    Built from GET /api/v1/profiles only (the old /api/test/known-profile helper
    was versionless).
    Without `machine_name`: the first machine in the catalog — fine for a generic
    mock printer that doesn't care which hardware it gets.
    With `machine_name`: that exact machine (e.g. MACHINE_PROFILE), for seeding a
    printer whose fixtures assume a specific model's profiles.
    Process/filament: the first entry compatible with the machine.

    Returns: {machine_uuid, machine_name, process_uuid, process_name, filament_uuid, filament_name}
    Raises requests.HTTPError if Laminus is unreachable or the catalog is not ready,
    LookupError if the catalog has no matching machine/process/filament.
    """
    machines = _iter_v1_profiles(laminus_url, "machine")
    if machine_name is None:
        machine = next(machines, None)
    else:
        machine = next((m for m in machines if m["name"] == machine_name), None)
    if machine is None:
        raise LookupError(f"Machine {machine_name!r} not in Laminus catalog")

    def _first_compatible(profile_type: str) -> dict:
        fallback = None
        for entry in _iter_v1_profiles(laminus_url, profile_type):
            fallback = fallback or entry
            cp = entry.get("compatible_printers") or []
            if not cp or machine["name"] in cp:
                return entry
        if fallback is None:
            raise LookupError(f"No {profile_type} profiles in Laminus catalog")
        return fallback

    process = _first_compatible("process")
    filament = _first_compatible("filament")
    return {
        "machine_uuid": machine["uuid"],
        "machine_name": machine["name"],
        "process_uuid": process["uuid"],
        "process_name": process["name"],
        "filament_uuid": filament["uuid"],
        "filament_name": filament["name"],
    }


def _find_mock_printer_id(session: requests.Session) -> int | None:
    """Return the Themis printer ID of the first registered mock printer, or None."""
    resp = session.get(f"{THEMIS_URL}/api/v1/printers", timeout=10)
    resp.raise_for_status()
    for p in resp.json():
        if p.get("printer_type") == "mock":
            return p["id"]
    return None


def _find_or_create_mock_printer(session: requests.Session, name: str = "Mock Printer (E2E)") -> int:
    """Return the ID of the E2E mock printer, creating it if needed.

    Uses the Laminus v1 profile catalog at LAMINUS_URL to seed the printer's active preset
    with stable profile UUIDs from the running Laminus (or mock-laminus).
    """
    existing = _find_mock_printer_id(session)
    if existing is not None:
        return existing

    try:
        profile = _fetch_known_profile()
    except Exception:
        profile = {}

    payload: dict = {
        "name": name,
        "printer_type": "mock",
        "connection_config": {},
    }
    if profile.get("machine_uuid"):
        payload["active_machine_preset"] = profile["machine_uuid"]
        payload["default_process_preset"] = profile["process_uuid"]
        payload["default_filament_presets"] = [profile["filament_uuid"]]

    resp = session.post(f"{THEMIS_URL}/api/v1/printers", json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()["id"]
