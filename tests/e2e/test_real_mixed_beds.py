"""Exercise real Orca arrangement, manifests, and Themis 3MF ingestion."""

import io
import json
import struct
import zipfile
from collections import Counter
from xml.etree import ElementTree

import pytest
import requests
from helpers import LAMINUS_URL, THEMIS_URL, _iter_v1_profiles

pytestmark = [pytest.mark.integration, pytest.mark.real_stack, pytest.mark.slow]


def _box_stl(width, depth, height):
    vertices = [(x, y, z) for z in (0, height) for y in (0, depth) for x in (0, width)]
    faces = [(0, 2, 1), (1, 2, 3), (4, 5, 6), (5, 7, 6), (0, 1, 4), (1, 5, 4),
             (2, 6, 3), (3, 6, 7), (0, 4, 2), (2, 4, 6), (1, 3, 5), (3, 7, 5)]
    return bytes(80) + struct.pack("<I", len(faces)) + b"".join(
        struct.pack("<12fH", 0, 0, 0, *vertices[a], *vertices[b], *vertices[c], 0)
        for a, b, c in faces
    )


def _upload(name, content):
    return name, content, "application/octet-stream"


def _check_plate(response, bed, remaining, bundle_index):
    assert response.ok, response.text
    consumed = json.loads(response.headers["X-Consumed"])
    assert len(consumed) == len(remaining) and 0 < sum(consumed)
    assert all(0 <= used <= amount for used, amount in zip(consumed, remaining))
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("Metadata/laminus_manifest.json"))
        settings = json.loads(archive.read("Metadata/project_settings.config"))
        model_settings = ElementTree.fromstring(archive.read("Metadata/model_settings.config"))
    (plate,) = model_settings.findall("plate")
    (manifest_plate,) = manifest["plates"]
    instances = manifest_plate["instances"]
    assert [item["requested"] for item in manifest["items"]] == remaining
    assert [item["consumed"] for item in manifest["items"]] == consumed
    assert Counter(instance["item"] for instance in instances) == Counter({
        index: count for index, count in enumerate(consumed) if count
    })
    assert Counter(instance["object_id"] for instance in instances) == Counter(
        node.attrib["value"] for node in plate.findall("model_instance/metadata[@key='object_id']")
    )
    bundle_parts = Counter(instance["part"] for instance in instances if instance["item"] == bundle_index)
    if bundle_index is not None and consumed[bundle_index]:
        assert len(bundle_parts) == 2 and len(set(bundle_parts.values())) == 1
    x, y = zip(*(tuple(map(float, corner.split("x"))) for corner in settings["printable_area"]))
    assert (max(x) - min(x), max(y) - min(y)) == pytest.approx((bed, bed))
    return consumed


def test_mixed_source_next_plates_conserve_instances_across_bed_sizes(http):
    health = requests.get(f"{LAMINUS_URL}/api/v1/health", timeout=10)
    health.raise_for_status()
    assert health.json()["orcaslicer_installed"]
    profiles = {p["name"]: p["uuid"] for p in _iter_v1_profiles(LAMINUS_URL, "machine")}
    machines = {
        180: profiles["Bambu Lab A1 mini 0.4 nozzle"],
        256: profiles["Bambu Lab P1S 0.4 nozzle"],
        350: profiles["Voron 2.4 350 0.4 nozzle"],
    }

    bundle = requests.post(
        f"{LAMINUS_URL}/api/v1/pack",
        files=[("files", _upload("a.stl", _box_stl(24, 20, 5))),
               ("files", _upload("b.stl", _box_stl(27, 20, 5)))],
        data={"bed_x": 180, "bed_y": 180, "bed_z": 180}, timeout=90,
    )
    assert bundle.ok, bundle.text
    files = [
        ("files", _upload("bundle.3mf", bundle.content)),
        ("files", _upload("loose.stl", _box_stl(22, 18, 5))),
        ("files", _upload("wide.stl", _box_stl(274, 210, 5))),
    ]
    selection = [
        {"file": "bundle.3mf", "plate": 1, "rule": "together", "qty": 2},
        {"file": "loose.stl", "qty": 6},
        {"file": "wide.stl", "qty": 1},
    ]
    undersized = requests.post(
        f"{LAMINUS_URL}/api/v1/pack/next", files=[files[2]],
        data={"selection": json.dumps([selection[2]]), "machine_uuid": machines[256]}, timeout=90,
    )
    assert undersized.status_code == 422, undersized.text
    assert undersized.json()["error"]["code"] == "nothing_fits"

    remaining = [4, 6, 1]  # Each bundle contains two distinct source objects.
    for bed, cap in ((180, 3), (256, 4), (350, None)):
        data = {"selection": json.dumps([item for item in selection if item["qty"]]),
                "machine_uuid": machines[bed]}
        if cap is not None:
            data["max_parts"] = cap
        packed = requests.post(f"{LAMINUS_URL}/api/v1/pack/next", files=files, data=data, timeout=90)
        active = [index for index, item in enumerate(selection) if item["qty"]]
        on_plate = _check_plate(packed, bed, [remaining[i] for i in active], 0 if 0 in active else None)
        consumed = dict(zip(active, on_plate))
        if cap is not None:
            assert sum(consumed.values()) <= cap and consumed.get(2, 0) == 0
        else:
            assert consumed[2] == 1
        for index, count in consumed.items():
            remaining[index] -= count
            selection[index]["qty"] -= count // 2 if index == 0 else count

    assert remaining == [0, 0, 0]
    uploaded = http.post(
        f"{THEMIS_URL}/api/v1/files/upload", files={"file": _upload("mixed-bed.3mf", packed.content)},
        data={"folder": "/Job Uploads"}, timeout=15,
    )
    assert uploaded.ok, uploaded.text
    file_url = f"{THEMIS_URL}/api/v1/files/{uploaded.json()['id']}"
    try:
        plates = http.get(f"{file_url}/plates", timeout=10)
        assert plates.ok, plates.text
        assert [plate["plate_number"] for plate in plates.json()["plates"]] == [1]
        downloaded = http.get(f"{file_url}/download", timeout=10)
        assert downloaded.ok, downloaded.text
        assert downloaded.content == packed.content
    finally:
        http.delete(file_url, timeout=10).raise_for_status()
