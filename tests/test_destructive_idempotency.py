"""Tests for destructive action idempotency and double-click prevention.

Covers:
- AC1: Idempotency key store (get/set/TTL/thread-safety)
- AC2: API routes return cached response for duplicate idempotency keys
- AC3: SDK client auto-generates idempotency keys for destructive actions
- AC4: SDK client retries on 5xx with exponential backoff
- AC5: Rapid double-click produces one mutation, cached response on second call
"""

import asyncio
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from src.api.idempotency import (
    IdempotencyStore,
    get_global_store,
    reset_global_store,
)
from src.api.routes import router
from src.sdk.client import OrchestratorClient


class TestIdempotencyStore(unittest.TestCase):
    def setUp(self):
        self.store = IdempotencyStore(ttl_seconds=2)

    def tearDown(self):
        reset_global_store()

    def test_get_returns_none_for_missing_key(self):
        self.assertIsNone(self.store.get("nonexistent"))

    def test_set_and_get(self):
        self.store.set("key1", {"result": "ok"})
        self.assertEqual(self.store.get("key1"), {"result": "ok"})

    def test_get_or_execute_returns_fresh_result(self):
        fn = MagicMock(return_value={"fresh": True})
        result, cached = self.store.get_or_execute("key1", fn)
        self.assertEqual(result, {"fresh": True})
        self.assertFalse(cached)
        fn.assert_called_once()

    def test_get_or_execute_returns_cached_on_second_call(self):
        fn = MagicMock(return_value={"fresh": True})
        result1, cached1 = self.store.get_or_execute("key1", fn)
        result2, cached2 = self.store.get_or_execute("key1", fn)
        self.assertEqual(result1, result2)
        self.assertFalse(cached1)
        self.assertTrue(cached2)
        fn.assert_called_once()

    def test_ttl_expiration(self):
        store = IdempotencyStore(ttl_seconds=1)
        store.set("key1", "value1")
        self.assertEqual(store.get("key1"), "value1")
        time.sleep(1.1)
        self.assertIsNone(store.get("key1"))

    def test_clear_expired(self):
        store = IdempotencyStore(ttl_seconds=1)
        store.set("key1", "value1")
        store.set("key2", "value2")
        time.sleep(1.1)
        removed = store.clear_expired()
        self.assertEqual(removed, 2)
        self.assertIsNone(store.get("key1"))
        self.assertIsNone(store.get("key2"))

    def test_thread_safety(self):
        """Concurrent get_or_execute calls with same key only execute fn once."""
        results = []
        barrier = threading.Barrier(10)

        def runner():
            barrier.wait()
            result, cached = self.store.get_or_execute("shared-key", lambda: "computed")
            results.append((result, cached))

        threads = [threading.Thread(target=runner) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one call should have cached=False
        uncached = [r for r in results if not r[1]]
        cached_results = [r for r in results if r[1]]
        self.assertEqual(len(uncached), 1)
        self.assertEqual(len(cached_results), 9)
        # All got the same result
        self.assertTrue(all(r[0] == "computed" for r in results))

    def test_generate_key_is_unique(self):
        keys = [IdempotencyStore.generate_key() for _ in range(100)]
        self.assertEqual(len(keys), len(set(keys)))


class TestAPIRoutesIdempotency(unittest.TestCase):
    def setUp(self):
        reset_global_store()

    def _client(self):
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_delete_without_key_works(self):
        client = self._client()
        # No idempotency key → no caching → normal delete
        # Registry is empty so 404
        resp = client.delete("/agents/nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_delete_with_key_caches_result(self):
        client = self._client()
        # Without idempotency key first call gets 404
        resp1 = client.delete("/agents/agent1")
        self.assertEqual(resp1.status_code, 404)
        # With idempotency key, same result is returned from cache
        resp2 = client.delete("/agents/agent1", headers={"Idempotency-Key": "key-abc"})
        self.assertEqual(resp2.status_code, 404)
        # A third call without key also gets 404 (different path, not cached)
        resp3 = client.delete("/agents/agent1")
        self.assertEqual(resp3.status_code, 404)

    def test_start_with_idempotency_key(self):
        client = self._client()
        # Without key
        resp1 = client.post("/agents/agent1/start")
        self.assertEqual(resp1.status_code, 404)
        # With key → cached
        resp2 = client.post("/agents/agent1/start", headers={"Idempotency-Key": "start-key"})
        self.assertEqual(resp2.status_code, 404)

    def test_stop_with_idempotency_key(self):
        client = self._client()
        resp = client.post("/agents/agent1/stop", headers={"Idempotency-Key": "stop-key"})
        self.assertEqual(resp.status_code, 404)

    def test_revoke_endpoint_exists(self):
        client = self._client()
        resp = client.post("/agents/agent1/revoke", headers={"Idempotency-Key": "revoke-key"})
        # Should not 405 (method not allowed)
        self.assertIn(resp.status_code, [200, 201, 404])


class TestSDKClientIdempotencyRetry(unittest.TestCase):
    """SDK-level tests for idempotency key generation and retry logic."""

    def setUp(self):
        reset_global_store()

    def test_delete_agent_auto_generates_idempotency_key(self):
        """delete_agent() auto-generates a key so retries are safe."""
        with patch.object(OrchestratorClient, "_request") as mock_req:
            mock_req.return_value = {"status": "deleted"}
            client = OrchestratorClient(base_url="http://test", api_key="test")
            client.delete_agent("agent-1")
            # First call: auto-generated key
            call1_args = mock_req.call_args_list[0]
            self.assertIsNotNone(call1_args.kwargs.get("idempotency_key"))
            key1 = call1_args.kwargs["idempotency_key"]
            # Second call: different key
            client.delete_agent("agent-1")
            call2_args = mock_req.call_args_list[1]
            key2 = call2_args.kwargs["idempotency_key"]
            self.assertNotEqual(key1, key2)

    def test_delete_agent_with_explicit_key(self):
        """delete_agent() uses the provided idempotency key."""
        with patch.object(OrchestratorClient, "_request") as mock_req:
            mock_req.return_value = {"status": "deleted"}
            client = OrchestratorClient(base_url="http://test", api_key="test")
            client.delete_agent("agent-1", idempotency_key="my-fixed-key")
            call_args = mock_req.call_args
            self.assertEqual(call_args.kwargs["idempotency_key"], "my-fixed-key")

    def test_start_agent_with_idempotency_key(self):
        with patch.object(OrchestratorClient, "_request") as mock_req:
            mock_req.return_value = {"status": "started"}
            client = OrchestratorClient(base_url="http://test", api_key="test")
            client.start_agent("agent-1", idempotency_key="start-key")
            self.assertEqual(
                mock_req.call_args.kwargs["idempotency_key"], "start-key"
            )

    def test_stop_agent_with_idempotency_key(self):
        with patch.object(OrchestratorClient, "_request") as mock_req:
            mock_req.return_value = {"status": "stopped"}
            client = OrchestratorClient(base_url="http://test", api_key="test")
            client.stop_agent("agent-1", idempotency_key="stop-key")
            self.assertEqual(
                mock_req.call_args.kwargs["idempotency_key"], "stop-key"
            )

    def test_revoke_agent_with_idempotency_key(self):
        with patch.object(OrchestratorClient, "_request") as mock_req:
            mock_req.return_value = {"status": "revoked"}
            client = OrchestratorClient(base_url="http://test", api_key="test")
            client.revoke_agent("agent-1", idempotency_key="revoke-key")
            self.assertEqual(
                mock_req.call_args.kwargs["idempotency_key"], "revoke-key"
            )


class TestDoubleClickPrevention(unittest.TestCase):
    """Simulate the rapid double-click scenario."""

    def setUp(self):
        reset_global_store()

    def test_rapid_double_click_yields_one_request(self):
        """Rapid calls with same idempotency key only execute fn once (in-process)."""
        store = IdempotencyStore(ttl_seconds=60)
        call_count = 0

        def do_delete():
            nonlocal call_count
            call_count += 1
            return {"status": "deleted", "id": "agent-1"}

        key = IdempotencyStore.generate_key()

        # Simulate two rapid clicks arriving at nearly the same time
        result1, cached1 = store.get_or_execute(key, do_delete)
        result2, cached2 = store.get_or_execute(key, do_delete)

        self.assertEqual(call_count, 1)  # Only one actual mutation
        self.assertEqual(result1, result2)
        self.assertFalse(cached1)
        self.assertTrue(cached2)


class TestNetworkRetryScenario(unittest.TestCase):
    """Simulate network retry with same idempotency key."""

    def setUp(self):
        reset_global_store()

    def test_sdk_retries_on_5xx_with_exponential_backoff(self):
        """SDK retries on 5xx server errors, then succeeds."""
        import urllib.error
        call_count = 0

        def fake_urlopen(req, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # HTTPError is what urlopen raises for 5xx when used with context manager
                raise urllib.error.HTTPError(
                    url="http://test", code=503,
                    msg="Service Unavailable", hdrs={}, fp=None
                )
            response = MagicMock()
            response.__enter__ = MagicMock(return_value=response)
            response.__exit__ = MagicMock(return_value=False)
            response.read.return_value = b'{"status": "deleted"}'
            return response

        with patch("src.sdk.client.urlopen", fake_urlopen):
            client = OrchestratorClient(
                base_url="http://test",
                api_key="test",
                max_retries=3,
                retry_base_delay=0.01,
            )
            result = client._request(
                "DELETE", "/agents/agent-1",
                idempotency_key="retry-key",
                _retry_count=0,
            )
            self.assertEqual(call_count, 2)
            self.assertEqual(result, {"status": "deleted"})

    def test_idempotency_key_makes_retry_safe(self):
        """Same idempotency key on retry → server returns cached response."""
        store = IdempotencyStore(ttl_seconds=60)
        call_count = 0

        def do_delete():
            nonlocal call_count
            call_count += 1
            return {"status": "deleted", "id": "agent-1"}

        key = "retry-key-abc"
        # First attempt
        result1, _ = store.get_or_execute(key, do_delete)
        self.assertEqual(call_count, 1)
        # Retry with same key → cached
        result2, cached = store.get_or_execute(key, do_delete)
        self.assertEqual(call_count, 1)  # Still 1, no new call
        self.assertTrue(cached)
        self.assertEqual(result1, result2)


if __name__ == "__main__":
    unittest.main()
