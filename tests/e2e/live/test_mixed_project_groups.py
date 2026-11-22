"""Mixed-file project journeys through Themis and real Laminus/OrcaSlicer."""

import io
import json
import zipfile
from collections import Counter
from uuid import uuid4

import pytest
import requests
from helpers import LAMINUS_URL, THEMIS_URL, _minimal_stl

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.slow]
API = f"{THEMIS_URL}/api/v1"


def _json(http, method, path, *, timeout=15, **kwargs):
    response = http.request(method, f"{API}{path}", timeout=timeout, **kwargs)
    assert response.ok, f"{method} {path}: {response.status_code} {response.text}"
    return response.json()


@pytest.fixture(scope="module")
def targets(http, request):
    """Use real driver identities with reserved addresses so jobs cannot reach hardware."""
    printers = {}
    for label, plugin, address in (
        ("raw", "elegoo_centauri", "192.0.2.41"),
        ("archive", "bambu", "192.0.2.42"),
    ):
        printer = _json(http, "POST", "/printers", json={
            "name": f"Mixed project {label} {uuid4().hex[:8]}",
            "printer_type": plugin,
            "connection_config": {"ip_address": address},
        })
        path = f"/printers/{printer['id']}"
        request.addfinalizer(lambda path=path: http.delete(f"{API}{path}", timeout=15).raise_for_status())
        _json(http, "PATCH", path, json={"queue_on": False})
        printers[label] = printer
    return printers


@pytest.fixture(scope="module")
def sources(http, targets, request):
    suffix = uuid4().hex[:8]
    stl = suffix.encode().ljust(80, b"\0") + _minimal_stl()[80:]
    packed = requests.post(
        f"{LAMINUS_URL}/api/v1/pack", timeout=90,
        files=[("files", ("bundle.stl", stl, "application/octet-stream"))],
        data={"bed_x": 180, "bed_y": 180, "bed_z": 180,
              "items": json.dumps([{"file": "bundle.stl", "quantity": 2}])},
    )
    assert packed.ok, packed.text
    raw = f"; mixed project {suffix}\n; estimated printing time (normal mode) = 1m 0s\nG4 P0\n".encode()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        for plate in (1, 2):
            package.writestr(f"Metadata/plate_{plate}.gcode", raw + f"; plate {plate}\n".encode())

    files = {}
    for extension, content in (("stl", stl), ("3mf", packed.content),
                               ("gcode", raw), ("gcode.3mf", archive.getvalue())):
        uploaded = _json(http, "POST", "/files/upload", files={
            "file": (f"mixed-{suffix}.{extension}", content, "application/octet-stream"),
        }, data={"folder": "/Job Uploads"})
        files[extension] = uploaded["id"]
        path = f"{API}/files/{uploaded['id']}"
        request.addfinalizer(lambda path=path: http.delete(path, timeout=15).raise_for_status())

    for extension, target in (("gcode", "raw"), ("gcode.3mf", "archive")):
        _json(http, "PUT", f"/files/{files[extension]}/eligibility", json={
            "model_uuids": [targets[target]["model_uuid"]],
        })
    return files


@pytest.fixture
def project(http, sources, project_id):
    # Keep source files until project fixtures have cleaned up their references.
    path = f"/projects/{project_id}"
    groups = _json(http, "GET", f"{path}/groups")
    yield path, next(group["id"] for group in groups if group["is_default"])
    for job in _json(http, "GET", f"{path}/jobs"):
        http.post(f"{API}/jobs/{job['id']}/cancel", timeout=15).raise_for_status()


def _group(http, path, group, strategy, printers, **settings):
    return _json(http, "PATCH", f"{path}/groups/{group}", json={
        "strategy": strategy, "targets": [{"target_key": str(p["id"])} for p in printers], **settings,
    })


def _add(http, path, file_id, **settings):
    return _json(http, "POST", f"{path}/items", json={"file_id": file_id, **settings})


def _generate(http, path):
    return _json(http, "POST", f"{path}/generate", json={}, timeout=120)


def _job_quantities(http, jobs):
    counts = Counter()
    for job in jobs:
        detail = _json(http, "GET", f"/jobs/{job['id']}")
        counts.update({item_id: qty for item_id, qty in detail["project_item_quantities"].items() if qty})
    return counts


def _pool(http, group_id):
    pools = _json(http, "GET", "/queue/pools")
    return next(pool for pool in pools if pool["group_id"] == group_id)


def test_fixed_group_packs_models_and_preserves_both_presliced_formats(http, project, sources, targets):
    path, group = project
    _group(http, path, group, "fixed", targets.values(), envelope_x=180, envelope_y=180)
    stl = _add(http, path, sources["stl"], quantity=2)
    model = _add(http, path, sources["3mf"], pack_rule="together")
    objects = _json(http, "GET", f"/files/{sources['3mf']}/objects")
    selected = _add(http, path, sources["3mf"], kind="3mf_object", source_ref=str(objects[0]["id"]))
    gcode = _add(http, path, sources["gcode"], quantity=2)
    archive = _add(http, path, sources["gcode.3mf"], source_ref="2")
    inputs = (stl, model, selected, gcode, archive)
    assert [(i["kind"], i["pack_rule"]) for i in inputs] == [
        ("stl", "split"), ("3mf_plate", "together"), ("3mf_object", "split"),
        ("gcode", "locked"), ("gcode", "locked"),
    ]
    expected = {str(i["id"]): i["quantity"] for i in inputs}
    items = _json(http, "GET", f"{path}/items")
    assert {str(i["id"]): i["quantity"] for i in items} == expected

    result = _generate(http, path)
    (packed,) = result["files"]
    assert packed["id"] not in sources.values()
    assert Counter((j["uploaded_file_id"], j["plate_number"]) for j in result["jobs"]) == {
        (packed["id"], 1): 1, (sources["gcode"], 1): 2, (sources["gcode.3mf"], 2): 1,
    }
    assert _job_quantities(http, result["jobs"]) == expected
    assert {(g["pack_bed_x"], g["pack_bed_y"]) for g in result["groups"]} == {(180, 180)}
    for job in result["jobs"]:
        if job["uploaded_file_id"] in (sources["gcode"], sources["gcode.3mf"]):
            label = "raw" if job["uploaded_file_id"] == sources["gcode"] else "archive"
            detail = _json(http, "GET", f"/jobs/{job['id']}/details")
            assert {c["printer_id"] for c in detail["printer_configs"]} == {targets[label]["id"]}


def test_pool_group_defers_models_and_queues_solo_and_presliced_items(http, project, sources, targets):
    path, group = project
    configured = _group(http, path, group, "pool", targets.values(), max_parts=3)
    assert configured["strategy"] == "pool" and configured["max_parts"] == 3
    _add(http, path, sources["stl"], quantity=4)
    _add(http, path, sources["3mf"], quantity=2, pack_rule="together")
    solo = _add(http, path, sources["stl"], pack_rule="solo")
    raw = _add(http, path, sources["gcode"])
    archive = _add(http, path, sources["gcode.3mf"], source_ref="2")

    result = _generate(http, path)
    assert len(result["jobs"]) == 3
    assert _job_quantities(http, result["jobs"]) == {str(i["id"]): 1 for i in (solo, raw, archive)}
    pool = _pool(http, group)
    assert (pool["left"], pool["printing"], pool["done"]) == (6, 0, 0)


def test_fixed_group_keeps_solo_copies_separate_and_reuses_a_locked_plate(http, project, sources, targets):
    path, group = project
    _group(http, path, group, "fixed", [targets["raw"]])
    solo = _add(http, path, sources["stl"], quantity=2, pack_rule="solo")
    locked = _add(http, path, sources["3mf"], quantity=2, source_ref="1", pack_rule="locked")
    together = _add(http, path, sources["3mf"], pack_rule="together")

    result = _generate(http, path)
    (packed,) = result["files"]
    download = http.get(f"{API}/files/{packed['id']}/download", timeout=15)
    download.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(download.content)) as generated:
        manifest = json.loads(generated.read("Metadata/laminus_manifest.json"))
    sizes = {plate["plate"]: len(plate["instances"]) for plate in manifest["plates"]}
    actual = Counter()
    for job in result["jobs"]:
        detail = _json(http, "GET", f"/jobs/{job['id']}")
        quantities = {item: qty for item, qty in detail["project_item_quantities"].items() if qty}
        assert len(quantities) == 1 and sum(quantities.values()) == 1, quantities
        item_id = next(iter(quantities))
        assert (job["uploaded_file_id"] == sources["3mf"]) == (item_id == str(locked["id"]))
        if item_id == str(locked["id"]):
            assert job["plate_number"] == 1
        else:
            assert sizes[job["plate_number"]] == (1 if item_id == str(solo["id"]) else 2)
        actual[item_id] += 1
    assert actual == {str(solo["id"]): 2, str(locked["id"]): 2, str(together["id"]): 1}


def test_mixed_strategies_respect_group_order_and_item_moves(http, project, sources, targets):
    path, fixed = project
    _group(http, path, fixed, "fixed", [targets["raw"]])
    pooled = _json(http, "POST", f"{path}/groups", json={"name": "Pack on demand"})["id"]
    _group(http, path, pooled, "pool", [targets["archive"]])
    stl = _add(http, path, sources["stl"])
    raw = _add(http, path, sources["gcode"])
    model = _add(http, path, sources["3mf"], quantity=3, pack_rule="together")
    archive = _add(http, path, sources["gcode.3mf"], group_id=pooled, source_ref="2")
    moved = _json(http, "PUT", f"{path}/items/{model['id']}", json={"group_id": pooled})
    assert moved["group_id"] == pooled
    _json(http, "PUT", f"{path}/groups/order", json={"group_ids": [pooled, fixed]})

    result = _generate(http, path)
    assert [group["id"] for group in result["groups"]] == [pooled, fixed]
    pool_jobs = [job for job in result["jobs"] if job["group_id"] == pooled]
    fixed_jobs = [job for job in result["jobs"] if job["group_id"] == fixed]
    assert _job_quantities(http, pool_jobs) == {str(archive["id"]): 1}
    assert _job_quantities(http, fixed_jobs) == {str(stl["id"]): 1, str(raw["id"]): 1}
    assert _pool(http, pooled)["left"] == 3


def test_incompatible_presliced_target_blocks_generation_until_group_is_corrected(http, project, sources, targets):
    path, group = project
    _group(http, path, group, "fixed", [targets["archive"]])
    stl = _add(http, path, sources["stl"])
    raw = _add(http, path, sources["gcode"])

    invalid = http.post(f"{API}{path}/generate", json={}, timeout=30)
    assert invalid.status_code == 422 and "as sliced" in invalid.json()["detail"]
    assert _json(http, "GET", f"{path}/jobs") == []

    _group(http, path, group, "fixed", [targets["raw"]])
    result = _generate(http, path)
    assert _job_quantities(http, result["jobs"]) == {str(stl["id"]): 1, str(raw["id"]): 1}
    (raw_job,) = [job for job in result["jobs"] if job["uploaded_file_id"] == sources["gcode"]]
    detail = _json(http, "GET", f"/jobs/{raw_job['id']}/details")
    assert {config["printer_id"] for config in detail["printer_configs"]} == {targets["raw"]["id"]}
