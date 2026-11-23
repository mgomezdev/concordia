"""BIZ-390 acceptance through the real Laminus/OrcaSlicer and disposable Themis APIs."""

import io
import json
import time
import zipfile
from collections import Counter
from uuid import uuid4

import pytest
import requests
from helpers import LAMINUS_URL, THEMIS_URL, _drain_active_jobs_for_printer
from orca_geometry import box_stl, read_layout, read_manifest, split_source_plates

pytestmark = [pytest.mark.integration, pytest.mark.develop, pytest.mark.slow]

THEMIS = f"{THEMIS_URL}/api/v1"
LAMINUS = f"{LAMINUS_URL}/api/v1"
BED_PROFILES = {
    180: ("Bambu Lab A1 mini 0.4 nozzle", "0.20mm Standard @BBL A1M", "Bambu PLA Basic @BBL A1M"),
    256: ("Bambu Lab P1S 0.4 nozzle", "0.20mm Standard @BBL X1C", "Bambu PLA Basic @BBL X1C"),
    350: ("Voron 2.4 350 0.4 nozzle", "0.20mm Standard @Voron", "Generic PLA @System"),
}


def _request(client, method, url, *, expected=200, timeout=30, **kwargs):
    response = client.request(method, url, timeout=timeout, **kwargs)
    assert response.status_code == expected, f"{method} {url}: {response.status_code} {response.text[:2000]}"
    return response


def _themis(http, method, path, **kwargs):
    return _request(http, method, f"{THEMIS}{path}", **kwargs).json()


def _wait(read, ready, description, timeout=180):
    deadline = time.monotonic() + timeout
    while True:
        value = read()
        if ready(value):
            return value
        assert time.monotonic() < deadline, f"Timed out waiting for {description}. Last response: {value}"
        time.sleep(1)


@pytest.fixture(scope="module")
def laminus():
    with requests.Session() as session:
        info = _request(session, "GET", LAMINUS).json()
        assert info["ready"]["catalog"], info
        assert info["engine"]["name"] == "orcaslicer" and info["engine"]["version"], info
        assert {"slice.plate_isolation", "pack.next", "pack.manifest"} <= set(info["features"]), info
        print(f"Real OrcaSlicer engine: {info['engine']}")
        yield session


@pytest.fixture(scope="module")
def beds(laminus):
    profiles = {}
    for bed, (machine_name, process_name, filament_name) in BED_PROFILES.items():
        machine = _profile(laminus, "machine", machine_name)
        assert (machine["bed_size_x"], machine["bed_size_y"]) == (bed, bed), machine
        profiles[bed] = {
            "machine": machine,
            "process": _profile(laminus, "process", process_name, machine["manufacturer"]),
            "filament": _profile(laminus, "filament", filament_name),
        }
    return profiles


def _profile(laminus, kind, name, manufacturer=None):
    response = _request(laminus, "GET", f"{LAMINUS}/profiles", params={"type": kind, "q": name, "limit": 100})
    matches = [profile for profile in response.json()["data"] if profile["name"] == name]
    vendor = [profile for profile in matches if profile.get("manufacturer") == manufacturer]
    assert vendor or matches, f"Laminus has no {kind} preset named {name!r}"
    return (vendor or matches)[0]


def _pack(laminus, bed, parts):
    return _request(
        laminus, "POST", f"{LAMINUS}/pack", timeout=120,
        files=[("files", (name, box_stl(width, depth), "application/octet-stream")) for name, width, depth in parts],
        data={"bed_x": bed, "bed_y": bed, "bed_z": bed},
    ).content


def _slice(laminus, source, profiles):
    job = _request(
        laminus, "POST", f"{LAMINUS}/jobs", expected=202,
        files={"file": ("two-plates.3mf", source, "application/octet-stream")},
        data={
            "machine_uuid": profiles["machine"]["uuid"], "process_uuid": profiles["process"]["uuid"],
            "filament_uuids": json.dumps([profiles["filament"]["uuid"]]), "plate": 2, "export_3mf": "plate.gcode.3mf",
        },
    ).json()
    return _wait(
        lambda: _request(laminus, "GET", f"{LAMINUS}/jobs/{job['id']}").json(),
        lambda state: state["status"] in {"completed", "failed"}, "Laminus slice",
    )


def _center(bounds):
    return tuple((low + high) / 2 for low, high in bounds)


def _fits(layout, bed, height):
    assert layout, "plate has no printable geometry"
    for name, _, bounds in layout:
        for (low, high), limit in zip(bounds, (bed, bed, height)):
            assert -0.05 <= low <= high <= limit + 0.05, (name, bounds, bed, height)


def test_source_plate_preserves_instances_and_relative_transforms(laminus, beds):
    source = split_source_plates(_pack(laminus, 256, [
        ("marker.stl", 9, 10), ("twin-a.stl", 16, 20),
        ("turned.stl", 15, 12), ("twin-b.stl", 16, 20),
    ]))
    original = read_layout(source)
    assert set(original) == {1, 2}
    assert [len(original[number]) for number in (1, 2)] == [1, 3]
    # All parts individually fit 180 mm. Only the selected plate's layout is too wide.
    assert all(max(high - low for low, high in bounds) < 180 for _, _, bounds in original[2])
    xs = [bounds[0] for _, _, bounds in original[2]]
    assert 180 < max(high for _, high in xs) - min(low for low, _ in xs) < 256

    for bed in (256, 350):
        job = _slice(laminus, source, beds[bed])
        assert job["status"] == "completed" and job["artifact"]["available"], job
        artifact = _request(laminus, "GET", f"{LAMINUS_URL}{job['links']['artifact']}").content
        with zipfile.ZipFile(io.BytesIO(artifact)) as archive:
            assert [name for name in archive.namelist() if name.endswith(".gcode")] == ["Metadata/plate_1.gcode"]
            assert b"G1 " in archive.read("Metadata/plate_1.gcode")
        sliced = read_layout(artifact)
        assert set(sliced) == {1}
        _fits(sliced[1], bed, beds[bed]["machine"]["bed_size_z"])
        assert Counter(row[0] for row in sliced[1]) == Counter(row[0] for row in original[2])
        expected, actual = sorted(original[2]), sorted(sliced[1])
        source_anchor, target_anchor = _center(expected[0][2]), _center(actual[0][2])
        for (_, want_rotation, want_bounds), (_, rotation, bounds) in zip(expected, actual):
            assert rotation == pytest.approx(want_rotation, abs=0.001)
            for axis, (limits, want_limits) in enumerate(zip(bounds, want_bounds)):
                assert tuple(v - target_anchor[axis] for v in limits) == pytest.approx(
                    tuple(v - source_anchor[axis] for v in want_limits), abs=0.01,
                )
        print(f"{bed} mm: source plate 2 sliced with {len(actual)} instances and unchanged relative transforms")

    rejected = _slice(laminus, source, beds[180])
    assert rejected["status"] == "failed" and not rejected["artifact"]["available"], rejected
    assert all(fragment in rejected["error"] for fragment in ("Plate 2 footprint", "does not fit", "180")), rejected
    print(f"180 mm: {rejected['error']}")


@pytest.fixture
def printer(http, beds, request):
    def create(bed):
        profile = beds[bed]
        name = f"Real Orca acceptance mock {bed} {uuid4().hex[:8]}"
        printer_id = _themis(http, "POST", "/printers", expected=201, json={
            "name": name, "printer_type": "mock", "connection_config": {},
            "bed_x_mm": bed, "bed_y_mm": bed, "bed_z_mm": profile["machine"]["bed_size_z"],
            "machine_profile_override": profile["machine"]["name"],
            "default_process_profile": profile["process"]["name"],
        })["id"]
        path = f"/printers/{printer_id}"
        def remove():
            _drain_active_jobs_for_printer(http, printer_id)
            _request(http, "DELETE", f"{THEMIS}{path}", expected=204)

        request.addfinalizer(remove)
        _themis(http, "PATCH", path, json={"queue_on": False})
        created = _wait(lambda: _themis(http, "GET", path), lambda p: p["configured"], name)
        assert created["printer_type"] == "mock" and not created["queue_on"], created
        return printer_id

    return create


def _upload(http, name, content):
    unique_name = f"{uuid4().hex[:8]}-{name}"
    return _themis(http, "POST", "/files/upload", expected=201, files={
        "file": (unique_name, content, "application/octet-stream"),
    }, data={"folder": "/Job Uploads"})


def _project(http, project_id, printers, strategy, **settings):
    path = f"/projects/{project_id}"
    group_id = next(group["id"] for group in _themis(http, "GET", f"{path}/groups") if group["is_default"])
    _themis(http, "PATCH", f"{path}/groups/{group_id}", json={
        "strategy": strategy, "targets": [{"target_key": str(pid)} for pid in printers], **settings,
    })
    return path, group_id


def _item(http, project, file, quantity=1, **settings):
    return _themis(http, "POST", f"{project}/items", expected=201, json={
        "file_id": file["id"], "quantity": quantity, **settings,
    })


def _pool(http, project, group_id):
    group = next(group for group in _themis(http, "GET", f"{project}/groups") if group["id"] == group_id)
    return group["pool"]


def _download(http, file_id):
    return _request(http, "GET", f"{THEMIS}/files/{file_id}/download").content


def _quantities(job):
    return Counter({int(item_id): qty for item_id, qty in job["project_item_quantities"].items() if qty})


def _claim(http, project, printer_id):
    path = f"/printers/{printer_id}"
    _themis(http, "PATCH", path, json={"queue_on": True})
    try:
        jobs = _wait(
            lambda: _themis(http, "GET", f"{project}/jobs"),
            lambda rows: any(job["assigned_printer_id"] == printer_id and job["status"] == "printing" for job in rows),
            f"mock printer {printer_id} to claim and slice a pool plate",
        )
        (job,) = (job for job in jobs if job["assigned_printer_id"] == printer_id)
        claim = _themis(http, "GET", f"/jobs/{job['id']}")
        assert claim["actual_seconds"] > 0, claim
        return claim
    finally:
        _themis(http, "PATCH", path, json={"queue_on": False})


def _object_id(http, file, source_name):
    objects = _themis(http, "GET", f"/files/{file['id']}/objects")
    (obj,) = (obj for obj in objects if obj["name"].removesuffix(".stl") == source_name)
    return str(obj["id"])


def _pool_for_claim(http, claim):
    return next(job["pool_group_id"] for job in _themis(http, "GET", "/queue") if job["id"] == claim["id"])


def test_mixed_pool_claims_conserve_parts_and_fall_through(http, laminus, beds, printer, project_id):
    printers = {bed: printer(bed) for bed in beds}
    fallthrough_printer = printer(180)
    project, group = _project(http, project_id, [*printers.values(), fallthrough_printer], "pool", max_parts=3)
    stl = _upload(http, "small.stl", box_stl(17, 15))
    mixed = _upload(http, "mixed.3mf", _pack(laminus, 256, [
        ("medium.stl", 195, 195), ("small-object.stl", 18, 13),
    ]))
    bundle_bytes = _pack(laminus, 350, [("large.stl", 270, 270), ("partner.stl", 16, 19)])
    bundle_source = read_manifest(bundle_bytes)
    (source_plate,) = bundle_source["plates"]
    bundle_parts = Counter(entry["object_id"] for entry in source_plate["instances"])
    assert len(bundle_parts) == 2
    bundle = _upload(http, "together.3mf", bundle_bytes)
    split = _item(http, project, stl, quantity=2)
    small_obj = _item(http, project, mixed, kind="3mf_object", source_ref=_object_id(http, mixed, "small-object"))
    medium_obj = _item(http, project, mixed, kind="3mf_object", source_ref=_object_id(http, mixed, "medium"))
    together = _item(http, project, bundle, kind="3mf_plate", source_ref=str(source_plate["plate"]), pack_rule="together")
    next_group = _themis(http, "POST", f"{project}/groups", expected=201, json={"name": "Next pool"})["id"]
    _themis(http, "PATCH", f"{project}/groups/{next_group}", json={
        "strategy": "pool", "targets": [{"target_key": str(fallthrough_printer)}],
    })
    next_item = _item(http, project, stl, group_id=next_group)
    generated = _themis(http, "POST", f"{project}/generate", json={}, timeout=120)
    assert generated["jobs"] == [] and generated["files"] == []
    requested = Counter({item["id"]: item["quantity"] for item in (split, small_obj, medium_obj, together)})
    files_by_item = {split["id"]: stl, small_obj["id"]: mixed, medium_obj["id"]: mixed, together["id"]: bundle}
    active_pools = _themis(http, "GET", "/queue/pools")
    assert [pool["group_id"] for pool in active_pools if pool["project_id"] == project_id] == [group, next_group]
    assert _pool(http, project, group) == {"done": 0, "printing": 0, "left": sum(requested.values())}

    expected_claims = {
        180: {split["id"]: 2, small_obj["id"]: 1},
        256: {medium_obj["id"]: 1},
        350: {together["id"]: 1},
    }
    remaining = requested.copy()
    job_ids = set()
    for bed, printer_id in printers.items():
        claim = _claim(http, project, printer_id)
        assert claim["id"] not in job_ids and _pool_for_claim(http, claim) == group and claim["plate_number"] == 1, claim
        job_ids.add(claim["id"])
        packed = _download(http, claim["uploaded_file_id"])
        manifest = read_manifest(packed)
        (plate,) = manifest["plates"]
        assert plate["plate"] == 1
        _fits(read_layout(packed)[1], bed, beds[bed]["machine"]["bed_size_z"])
        counts = Counter(entry["item"] for entry in plate["instances"])
        assert counts == Counter({entry["item"]: entry["consumed"] for entry in manifest["items"] if entry["consumed"]})
        got = _quantities(claim)
        assert got == expected_claims[bed] and got <= remaining, (bed, got, remaining, manifest)
        offered = [item_id for item_id in sorted(remaining) if remaining[item_id]]
        (asserted_counts, instance_counts) = (Counter(), Counter())
        for entry in manifest["items"]:
            item_id = offered[entry["item"]]
            per_copy = bundle_parts.total() if item_id == together["id"] else 1
            assert entry["source"].endswith(f"_{files_by_item[item_id]['original_filename']}")
            asserted_counts[item_id] += entry["requested"]
            assert entry["requested"] == remaining[item_id] * per_copy
            instance_counts[item_id] += entry["consumed"]
            assert entry["consumed"] == got[item_id] * per_copy
        assert set(asserted_counts) == set(offered)
        assert instance_counts.total() == len(plate["instances"])
        if bed == 350:
            assert Counter(entry["part"] for entry in plate["instances"]) == bundle_parts
        else:
            assert len(plate["instances"]) == sum(got.values())
        remaining.subtract(got)
        assert _pool(http, project, group) == {
            "done": 0, "printing": sum(requested.values()) - sum(remaining.values()), "left": sum(remaining.values()),
        }
        print(f"{bed} mm: job {claim['id']} reserved {dict(got)} from a {len(plate['instances'])}-instance plate")

        if bed == 180:
            # Larger parts are still pending in the earlier pool. This other 180 mm printer
            # must pass that pool and claim the next eligible one.
            skipped = _claim(http, project, fallthrough_printer)
            assert _pool_for_claim(http, skipped) == next_group and _quantities(skipped) == {next_item["id"]: 1}
            assert _pool(http, project, group)["left"] == sum(remaining.values())
            print(f"180 mm fall-through: job {skipped['id']} claimed the later pool")

    assert remaining.total() == 0 and len(job_ids) == 3


def test_persisted_uneven_plate_manifests_drive_progress_and_restoration(http, printer, project_id):
    printer_id = printer(180)
    project, _ = _project(http, project_id, [printer_id], "fixed", envelope_x=180, envelope_y=180)
    wide = _upload(http, "wide.stl", box_stl(150, 150))
    tiles = _upload(http, "tiles.stl", box_stl(80, 80))
    items = [_item(http, project, file, quantity) for file, quantity in ((wide, 1), (tiles, 4))]
    demand = Counter({item["id"]: item["quantity"] for item in items})
    generated = _themis(http, "POST", f"{project}/generate", json={}, timeout=120)
    (file,) = generated["files"]
    manifest = read_manifest(_download(http, file["id"]))
    assert [entry["requested"] for entry in manifest["items"]] == [1, 4]
    for entry, source in zip(manifest["items"], (wide, tiles)):
        assert entry["source"].endswith(f"_{source['original_filename']}")
    plate_counts = {
        plate["plate"]: Counter(items[entry["item"]]["id"] for entry in plate["instances"])
        for plate in manifest["plates"]
    }
    assert len({counts.total() for counts in plate_counts.values()}) > 1, plate_counts
    assert sum(plate_counts.values(), Counter()) == demand
    n = len(plate_counts)
    round_robin = {plate: Counter({item: qty // n + (index < qty % n) for item, qty in demand.items()})
                   for index, plate in enumerate(sorted(plate_counts))}
    assert plate_counts != round_robin, "fixture must distinguish the actual manifest from legacy counts"
    jobs = _themis(http, "GET", f"{project}/jobs")
    assert Counter(job["plate_number"] for job in jobs) == Counter({plate: 1 for plate in plate_counts})
    tiles_id = items[1]["id"]
    failing_plate = max(plate_counts, key=lambda number: plate_counts[number][tiles_id])
    credited = Counter()
    failed = Counter()

    for job in jobs:
        job_id, plate_no = job["id"], job["plate_number"]
        counts = plate_counts[plate_no]
        persisted = _themis(http, "GET", f"/jobs/{job_id}")
        assert _quantities(persisted) == counts and job["total_parts"] == counts.total()
        completed = _themis(http, "POST", f"/jobs/{job_id}/complete-manually", timeout=180, json={"printer_id": printer_id})
        assert completed["status"] == "complete", completed
        report_path = f"/printers/{printer_id}/plate-results"
        report = _themis(http, "GET", report_path)
        assert report["job_id"] == job_id
        assert {part["project_item_id"]: part["quantity"] for part in report["parts"]} == counts
        failures = [{"project_item_id": tiles_id, "quantity_failed": 1}] if plate_no == failing_plate else []
        cleared = _themis(http, "POST", f"/printers/{printer_id}/plate-cleared", timeout=120,
                          json={"job_id": job_id, "results": failures})
        assert cleared["ok"] and cleared["held"] == [] and "requeue_errors" not in cleared, cleared
        credited.update(counts)
        if failures:
            (retry_id,) = cleared["requeued_job_ids"]
            retry = _themis(http, "GET", f"/jobs/{retry_id}")
            assert _quantities(retry) == {tiles_id: 1} and retry["status"] == "queued", retry
            retry_manifest = read_manifest(_download(http, retry["uploaded_file_id"]))
            (retry_plate,) = retry_manifest["plates"]
            (retried,) = retry_plate["instances"]
            assert retried["source"].endswith(f"_{tiles['original_filename']}")
            credited[tiles_id] -= 1
            failed[tiles_id] += 1
        else:
            assert cleared["requeued_job_ids"] == []
        assert _themis(http, "GET", report_path)["job_id"] is None
        after = _themis(http, "GET", f"{project}/items")
        assert {item["id"]: (item["quantity_completed"], item["quantity_failed"], item["quantity_held"])
                for item in after} == {item: (credited[item], failed[item], 0) for item in demand}
        print(f"plate {plate_no}: manifest {dict(counts)}, credited {dict(credited)}, restored {dict(failed)}")

    assert credited + failed == demand and failed == {tiles_id: 1}
