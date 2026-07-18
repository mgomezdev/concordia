"""Interface compliance tests for MockPrinterClient.

Verifies the mock implements every abstract method of AbstractPrinterClient
and returns the correct types. No network required — pure in-process tests.

Skipped automatically if the Themis sibling repo isn't present at ../themis/.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load Themis services via importlib — avoids package-install requirements
# ---------------------------------------------------------------------------

_THEMIS_SERVICES = Path(__file__).parents[3] / "themis" / "backend" / "app" / "services"


def _load(name: str):
    p = _THEMIS_SERVICES / f"{name}.py"
    if not p.exists():
        return None
    # Use app.services.<name> as the module name so @dataclass can resolve cls.__module__
    # and so relative imports (from .abstract_printer_client import ...) resolve correctly.
    full_name = f"app.services.{name}"
    spec = importlib.util.spec_from_file_location(full_name, p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod   # register BEFORE exec_module — required for @dataclass
    spec.loader.exec_module(mod)
    return mod


_abstract = _load("abstract_printer_client")
if _abstract is None:
    pytest.skip("Themis sibling repo not found at ../themis", allow_module_level=True)

_mock_mod = _load("mock_printer_client")
if _mock_mod is None:
    pytest.skip("mock_printer_client.py not found in Themis repo", allow_module_level=True)

AbstractPrinterClient = _abstract.AbstractPrinterClient
PrinterCapabilities = _abstract.PrinterCapabilities
StartPrintOptions = _abstract.StartPrintOptions
MockPrinterClient = _mock_mod.MockPrinterClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client() -> MockPrinterClient:
    return MockPrinterClient()


# ---------------------------------------------------------------------------
# ── Abstract interface compliance ─────────────────────────────────────────────
# ---------------------------------------------------------------------------

def test_is_subclass_of_abstract():
    assert issubclass(MockPrinterClient, AbstractPrinterClient)


def test_can_instantiate_with_no_args():
    """Constructor must accept empty call — Themis creates mock with connection_config={}."""
    c = MockPrinterClient()
    assert c is not None


def test_can_instantiate_with_extra_kwargs():
    """Factory passes arbitrary kwargs; mock must silently ignore unknown ones."""
    c = MockPrinterClient(ip="192.168.1.1", port=3030, secret="abc")
    assert c is not None


# ---------------------------------------------------------------------------
# ── Connection lifecycle ──────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def test_connected_is_true(client):
    assert client.connected is True


def test_connect_does_not_raise(client):
    client.connect()
    assert client.connected is True


def test_disconnect_does_not_raise(client):
    client.disconnect()
    client.disconnect(timeout=5)


# ---------------------------------------------------------------------------
# ── State properties ──────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def test_initially_idle(client):
    assert client.is_idle is True
    assert client.is_printing is False


def test_start_print_transitions_to_printing(client):
    result = client.start_print("model.gcode")
    assert result is True
    assert client.is_printing is True
    assert client.is_idle is False


def test_stop_print_returns_to_idle(client):
    client.start_print("model.gcode")
    result = client.stop_print()
    assert result is True
    assert client.is_idle is True
    assert client.is_printing is False


def test_pause_returns_true(client):
    client.start_print("model.gcode")
    assert client.pause_print() is True


def test_resume_returns_true(client):
    client.start_print("model.gcode")
    assert client.resume_print() is True


def test_start_print_with_options(client):
    opts = StartPrintOptions(plate_id=2, bed_levelling=False)
    assert client.start_print("model.gcode", opts) is True


# ---------------------------------------------------------------------------
# ── Command interface ─────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def test_send_gcode_returns_true(client):
    assert client.send_gcode("G28") is True
    assert client.send_gcode("M109 S200") is True
    assert client.send_gcode("") is True


def test_request_status_update_does_not_raise(client):
    client.request_status_update()


# ---------------------------------------------------------------------------
# ── File upload ───────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def test_file_upload_supported(client):
    assert client.file_upload_supported is True


def test_upload_file_returns_true(client):
    assert client.upload_file(b"G28\n", "test.gcode") is True
    assert client.upload_file(b"", "empty.gcode") is True


# ---------------------------------------------------------------------------
# ── Capabilities ──────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def test_get_capabilities_returns_instance(client):
    caps = client.get_capabilities()
    assert isinstance(caps, PrinterCapabilities)
    assert caps.file_upload is True
    assert caps.gcode is True
    assert caps.pause_resume is True


# ---------------------------------------------------------------------------
# ── connection_fields — must return empty list (no UI config needed) ──────────
# ---------------------------------------------------------------------------

def test_connection_fields_empty():
    fields = MockPrinterClient.connection_fields()
    assert isinstance(fields, list)
    assert len(fields) == 0, (
        "Mock printer must have no connection fields — tests register it with connection_config={}"
    )


# ---------------------------------------------------------------------------
# ── printer_type class attribute ──────────────────────────────────────────────
# ---------------------------------------------------------------------------

def test_printer_type_attribute():
    assert hasattr(MockPrinterClient, "printer_type")
    assert MockPrinterClient.printer_type == "mock"


# ---------------------------------------------------------------------------
# ── No real network calls ─────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def test_control_endpoint_returns_none(client):
    """Mock must not try to probe a real host — control_endpoint() must return None."""
    assert client.control_endpoint() is None
