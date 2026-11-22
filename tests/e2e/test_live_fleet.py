"""Opt-in browser journey against a real Elegoo printer and camera."""

import ipaddress
import os
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from helpers import THEMIS_API_KEY, THEMIS_URL
from playwright.sync_api import Page, expect


@pytest.mark.integration
@pytest.mark.real_stack
def test_scan_quick_add_shows_real_camera_without_browser_reload(page: Page, http):
    address = os.environ.get("CONCORDIA_CAMERA_PRINTER_IP")
    if not address:
        pytest.skip("set CONCORDIA_CAMERA_PRINTER_IP to a reachable Elegoo on the isolated test stack")
    address = str(ipaddress.IPv4Address(address))
    api = f"{THEMIS_URL}/api/v1"
    queue = http.get(f"{api}/queue", timeout=10)
    queue.raise_for_status()
    assert not queue.json(), "Run the hardware journey on a fresh isolated stack with an empty queue"
    name = f"E2E scan {uuid4().hex[:8]}"
    if THEMIS_API_KEY:
        page.add_init_script(f"localStorage.setItem('themis.apiKey', {THEMIS_API_KEY!r});")
    page.goto(f"{THEMIS_URL}/fleet")
    page.get_by_role("button", name="Add printer", exact=True).click()
    page.get_by_role("button", name="Scan network for printers").click()
    page.get_by_label("Network ranges").fill(address)
    page.get_by_role("button", name="Scan", exact=True).click()
    row = page.get_by_role("row").filter(has=page.get_by_label(f"Nickname for {address}"))
    row.get_by_label(f"Nickname for {address}").fill(name, timeout=60_000)
    expect(row.get_by_label(f"Model for {address}")).to_have_value("Elegoo Centauri Carbon")

    with page.expect_response(lambda r: urlparse(r.url).path == "/api/v1/printers"
                              and r.request.method == "POST") as added:
        row.get_by_role("button", name=f"Add {address}", exact=True).click()
    assert added.value.ok, added.value.text()
    printer_id = added.value.json()["id"]
    printer_url = f"{api}/printers/{printer_id}"
    try:
        http.patch(printer_url, json={"queue_on": False}, timeout=10).raise_for_status()
        expect(row.get_by_test_id("row-status")).to_have_text("Added · configured", timeout=30_000)
        with page.expect_response(
            lambda r: urlparse(r.url).path == f"/api/v1/printers/{printer_id}/snapshot"
            and r.ok and r.headers.get("content-type", "").startswith("image/jpeg"),
            timeout=30_000,
        ) as camera:
            page.get_by_role("button", name="Fleet", exact=True).click()
        expect(page.get_by_text(name, exact=True)).to_be_visible()
        assert camera.value.body().startswith(b"\xff\xd8\xff")
    finally:
        http.delete(printer_url, timeout=10).raise_for_status()
