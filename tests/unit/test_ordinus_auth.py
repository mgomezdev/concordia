"""Authentication contract for the Ordinus integration-test client."""
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

spec = importlib.util.spec_from_file_location("ordinus_helpers", Path(__file__).parents[1] / "e2e" / "helpers.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


def response(status):
    result = requests.Response()
    result.status_code = status
    result.url = "http://localhost/api/v1/auth/session"
    return result


@pytest.fixture
def session(monkeypatch):
    client = Mock()
    client.headers = {}
    monkeypatch.setattr(helpers.requests, "Session", lambda: client)
    monkeypatch.setenv("ORDINUS_E2E_ADMIN_PASSWORD", "test-password")
    return client


def test_login_retains_session_and_sets_mutation_header(session):
    session.get.side_effect = [response(401), response(200)]
    session.post.return_value = response(200)
    assert helpers.ordinus_session() is session
    assert session.headers == {"X-Ordinus-Request": "1"}
    session.post.assert_called_once_with(
        f"{helpers.ORDINUS_URL}/api/v1/auth/login",
        json={"username": "admin", "password": "test-password"}, timeout=10)
    assert session.get.call_count == 2
    session.close.assert_not_called()


def test_legacy_image_without_auth_routes(session):
    session.get.return_value = response(404)
    assert helpers.ordinus_session() is session
    session.post.assert_not_called()


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_failed_login_is_not_bypassed(session, status):
    session.get.return_value = response(401)
    session.post.return_value = response(status)
    with pytest.raises(requests.HTTPError):
        helpers.ordinus_session()
    session.close.assert_called_once()


def test_login_without_a_usable_session_fails(session):
    session.get.side_effect = [response(401), response(401)]
    session.post.return_value = response(200)
    with pytest.raises(requests.HTTPError):
        helpers.ordinus_session()


def test_reads_private_password_file(session, monkeypatch):
    monkeypatch.delenv("ORDINUS_E2E_ADMIN_PASSWORD")
    monkeypatch.setattr(helpers.Path, "is_file", lambda self: True)
    monkeypatch.setattr(helpers.Path, "read_text", lambda self: "file-password\n")
    session.get.side_effect = [response(401), response(200)]
    session.post.return_value = response(200)
    helpers.ordinus_session()
    assert session.post.call_args.kwargs["json"]["password"] == "file-password"


def test_missing_password_fails_before_mutating_settings(session, monkeypatch):
    monkeypatch.delenv("ORDINUS_E2E_ADMIN_PASSWORD")
    monkeypatch.setattr(helpers.Path, "is_file", lambda self: False)
    session.get.return_value = response(401)
    with pytest.raises(RuntimeError, match="Provision Ordinus"):
        helpers.ordinus_session()
    session.post.assert_not_called()
    session.close.assert_called_once()
