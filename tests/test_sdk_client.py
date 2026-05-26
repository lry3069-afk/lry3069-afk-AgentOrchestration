"""Regression tests for OrchestratorClient SDK — Issue #4811."""

import json
import pytest
from io import BytesIO
from http.client import HTTPMessage
from unittest.mock import patch, MagicMock

from src.sdk.client import OrchestratorClient


def _make_response(status: int, body: bytes, headers: dict = None):
    """Build a mock context-manager for urlopen returning given status+body."""
    headers = headers or {}
    m = MagicMock()
    m.status = status
    m.read.return_value = body
    m.__enter__ = MagicMock(return_value=m)
    m.__exit__ = MagicMock(return_value=False)
    return m


class TestSDK204NoContent:
    """Issue #4811: SDK must not try to JSON-decode 204 No Content responses."""

    def test_delete_agent_returns_empty_dict(self):
        """delete_agent() (HTTP 204) should return {} without raising JSONDecodeError."""
        with patch("src.sdk.client.urlopen") as uw:
            uw.return_value.__enter__ = MagicMock(
                return_value=_make_response(204, b"")
            )
            uw.return_value.__exit__ = MagicMock(return_value=False)
            client = OrchestratorClient(base_url="http://localhost", api_key="test")
            result = client.delete_agent("agent-123")
            assert result == {}, f"Expected {{}}, got {result!r}"

    def test_stop_agent_returns_empty_dict(self):
        """stop_agent() (HTTP 204) should return {} without raising JSONDecodeError."""
        with patch("src.sdk.client.urlopen") as uw:
            uw.return_value.__enter__ = MagicMock(
                return_value=_make_response(204, b"")
            )
            uw.return_value.__exit__ = MagicMock(return_value=False)
            client = OrchestratorClient(base_url="http://localhost", api_key="test")
            result = client.stop_agent("agent-456")
            assert result == {}, f"Expected {{}}, got {result!r}"

    def test_start_agent_returns_empty_dict(self):
        """start_agent() (HTTP 204) should return {} without raising JSONDecodeError."""
        with patch("src.sdk.client.urlopen") as uw:
            uw.return_value.__enter__ = MagicMock(
                return_value=_make_response(204, b"")
            )
            uw.return_value.__exit__ = MagicMock(return_value=False)
            client = OrchestratorClient(base_url="http://localhost", api_key="test")
            result = client.start_agent("agent-789")
            assert result == {}, f"Expected {{}}, got {result!r}"

    def test_delete_agent_returns_json_on_200(self):
        """delete_agent() on a 200 response still returns parsed JSON."""
        payload = json.dumps({"deleted": True, "agent_id": "a1"}).encode()
        with patch("src.sdk.client.urlopen") as uw:
            uw.return_value.__enter__ = MagicMock(
                return_value=_make_response(200, payload)
            )
            uw.return_value.__exit__ = MagicMock(return_value=False)
            client = OrchestratorClient(base_url="http://localhost", api_key="test")
            result = client.delete_agent("agent-123")
            assert result == {"deleted": True, "agent_id": "a1"}

    def test_get_agent_returns_json_on_200(self):
        """get_agent() on 200 returns parsed JSON."""
        payload = json.dumps({"id": "a1", "name": "TestAgent"}).encode()
        with patch("src.sdk.client.urlopen") as uw:
            uw.return_value.__enter__ = MagicMock(
                return_value=_make_response(200, payload)
            )
            uw.return_value.__exit__ = MagicMock(return_value=False)
            client = OrchestratorClient(base_url="http://localhost", api_key="test")
            result = client.get_agent("a1")
            assert result["id"] == "a1"

    def test_register_agent_returns_json_on_201(self):
        """register_agent() on 201 returns parsed JSON."""
        payload = json.dumps({"id": "new-agent", "status": "registered"}).encode()
        with patch("src.sdk.client.urlopen") as uw:
            uw.return_value.__enter__ = MagicMock(
                return_value=_make_response(201, payload)
            )
            uw.return_value.__exit__ = MagicMock(return_value=False)
            client = OrchestratorClient(base_url="http://localhost", api_key="test")
            result = client.register_agent("TestAgent", "assistant", {})
            assert result["id"] == "new-agent"

    def test_http_error_returns_error_dict(self):
        """HTTP 404 returns error dict instead of raising."""
        with patch("src.sdk.client.urlopen") as uw:
            from urllib.error import HTTPError
            uw.side_effect = HTTPError(
                "http://localhost/api/v2/agents/bad",
                404, "Not Found", {}, None
            )
            client = OrchestratorClient(base_url="http://localhost", api_key="test")
            result = client.get_agent("bad")
            assert result == {"error": 404, "message": "Not Found"}

    def test_malformed_json_returns_error_dict(self):
        """HTTP 200 with whitespace-only body returns JSON decode error dict."""
        with patch("src.sdk.client.urlopen") as uw:
            uw.return_value.__enter__ = MagicMock(
                return_value=_make_response(200, b"   \n  ")
            )
            uw.return_value.__exit__ = MagicMock(return_value=False)
            client = OrchestratorClient(base_url="http://localhost", api_key="test")
            result = client.list_agents()
            # whitespace is not empty bytes — JSON decode fails gracefully
            assert result.get("error") == -1
            assert "JSON" in result.get("message", "")
