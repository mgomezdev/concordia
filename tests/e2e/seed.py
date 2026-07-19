"""Declarative test data for the E2E integration stack.

Consumed by conftest.seed_test_data, which runs once per session before any test.
The test stack mounts Themis /data as tmpfs, so the DB is always empty on startup.
"""
from __future__ import annotations

# Each dict maps to PrinterCreate fields.
# Keys prefixed with "_" are seed-time directives, not sent to the API:
#   _inject_laminus_profiles: bool — fill orca_printer_profiles + current_orca_printer_profile
#                                     from Laminus /api/test/known-profile at seed time
#   _queue_on: bool — if False, PATCH the printer after create to disable auto-queue processing
#                     (keeps jobs in "queued" state so UI tests can interact before printing starts)
PRINTERS: list[dict] = [
    {
        "name": "Elegoo Centauri Carbon (placeholder)",
        "printer_type": "mock",
        "connection_config": {},
        "bed_x_mm": 256.0,
        "bed_y_mm": 256.0,
        "_inject_laminus_profiles": True,
        "_queue_on": False,
    },
]
