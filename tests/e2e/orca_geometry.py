"""Read the geometry and membership of the small box projects used by acceptance tests."""

import io
import json
import struct
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict

MODEL = "3D/3dmodel.model"
SETTINGS = "Metadata/model_settings.config"
MANIFEST = "Metadata/laminus_manifest.json"
NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
COMPONENT_PATH = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}path"
IDENTITY = "1 0 0 0 1 0 0 0 1 0 0 0"


def box_stl(width, depth, height=1.2):
    vertices = [(x, y, z) for x in (0, width) for y in (0, depth) for z in (0, height)]
    triangles = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
                 (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    output = io.BytesIO()
    output.write(struct.pack("<80sI", b"Concordia synthetic box", len(triangles)))
    for triangle in triangles:
        coordinates = [value for vertex in triangle for value in vertices[vertex]]
        output.write(struct.pack("<12fH", 0, 0, 0, *coordinates, 0))
    return output.getvalue()


def metadata(element, key):
    return element.find(f"metadata[@key='{key}']").get("value")


def plate_members(settings):
    return {
        int(metadata(plate, "plater_id")): [
            (metadata(instance, "object_id"), int(metadata(instance, "instance_id")))
            for instance in plate.findall("model_instance")
        ]
        for plate in settings.findall("plate")
    }


def _apply_transform(points, transform):
    return [
        tuple(sum(point[j] * transform[3 * j + i] for j in range(3)) + transform[9 + i] for i in range(3))
        for point in points
    ]


def _transform(element):
    return tuple(map(float, element.get("transform", IDENTITY).split()))


def read_layout(content):
    """Return every plate's instance name, linear transform and world-space mesh bounds."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        models = {}

        def vertices(path, object_id):
            if path not in models:
                root = ET.fromstring(archive.read(path))
                models[path] = {node.get("id"): node for node in root.findall("m:resources/m:object", NS)}
            model = models[path][object_id]
            points = [
                tuple(float(node.get(axis)) for axis in "xyz")
                for node in model.findall("m:mesh/m:vertices/m:vertex", NS)
            ]
            for child in model.findall("m:components/m:component", NS):
                child_path = child.get(COMPONENT_PATH, path).lstrip("/")
                points.extend(_apply_transform(vertices(child_path, child.get("objectid")), _transform(child)))
            return points

        settings = ET.fromstring(archive.read(SETTINGS))
        names = {node.get("id"): metadata(node, "name") for node in settings.findall("object")}
        root = ET.fromstring(archive.read(MODEL))
        builds = defaultdict(list)
        for item in root.findall("m:build/m:item", NS):
            builds[item.get("objectid")].append(item)
        members = plate_members(settings)
        assert Counter(pair for plate in members.values() for pair in plate) == Counter(
            (object_id, index) for object_id, items in builds.items() for index in range(len(items))
        ), "build instances and plate membership disagree"
        layout = {}
        for plate, instances in members.items():
            layout[plate] = []
            for object_id, index in instances:
                item = builds[object_id][index]
                transform = _transform(item)
                points = _apply_transform(vertices(MODEL, object_id), transform)
                bounds = tuple((min(axis), max(axis)) for axis in zip(*points))
                assert len(bounds) == 3, f"missing mesh for object {object_id}"
                layout[plate].append((names[object_id], transform[:9], bounds))
        return layout


def read_manifest(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        manifest = json.loads(archive.read(MANIFEST))
        members = plate_members(ET.fromstring(archive.read(SETTINGS)))
    assert manifest["version"] == 1
    assert len(manifest["plates"]) == len(members)
    for plate in manifest["plates"]:
        assert plate["instances"], manifest
        assert Counter(entry["object_id"] for entry in plate["instances"]) == Counter(
            object_id for object_id, index in members[plate["plate"]]
        ), manifest
    return manifest


def split_source_plates(content):
    """Move an Orca-generated fixture's build instances into a known two-plate layout."""
    with zipfile.ZipFile(io.BytesIO(content)) as source:
        model = ET.fromstring(source.read(MODEL))
        settings = ET.fromstring(source.read(SETTINGS))
        (first,) = settings.findall("plate")
        (source_plate,) = json.loads(source.read(MANIFEST))["plates"]
        order = {entry["object_id"]: entry["item"] for entry in source_plate["instances"]}
        instances = sorted(first.findall("model_instance"), key=lambda entry: order[metadata(entry, "object_id")])
        assert len(instances) == 4
        second = ET.SubElement(settings, "plate")
        ET.SubElement(second, "metadata", key="plater_id", value="2")
        positions = [(128, 128), (30, 95), (119, 156), (225, 110)]
        builds = {item.get("objectid"): item for item in model.findall("m:build/m:item", NS)}
        for index, (instance, (x, y)) in enumerate(zip(instances, positions)):
            item = builds[metadata(instance, "object_id")]
            transform = _transform(item)
            if index:
                first.remove(instance)
                second.append(instance)
                x += 256 * 1.2  # Orca's second plate origin in a 256 mm source project
            rotation = "0 1 0 -1 0 0 0 0 1" if index == 2 else IDENTITY.rsplit(" ", 3)[0]
            item.set("transform", f"{rotation} {x} {y} {transform[11]}")
        output = io.BytesIO()
        # Preserve the 3MF serializer's namespace aliases referenced by requiredextensions.
        ET.register_namespace("", NS["m"])
        ET.register_namespace("p", COMPONENT_PATH.split("}")[0][1:])
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
            for member in source.namelist():
                if member == MODEL:
                    target.writestr(member, ET.tostring(model, encoding="utf-8", xml_declaration=True))
                elif member == SETTINGS:
                    target.writestr(member, ET.tostring(settings, encoding="utf-8", xml_declaration=True))
                elif member != MANIFEST:
                    target.writestr(member, source.read(member))
    return output.getvalue()
