"""Offline tests for finite RabbitMQ queue-depth-only observation."""

from __future__ import annotations

import base64
import json
import unittest
from urllib.parse import unquote

from rabbitmq_queue_observer import (
    RabbitMQQueueObserver,
    RabbitMQQueueObserverError,
    _QUEUE_NAMES,
)


class RabbitMQQueueObserverTests(unittest.TestCase):
    """Prove the observer reads nine counters and never receives message bodies."""

    def test_reads_fixed_queue_metrics_with_monitoring_identity(self) -> None:
        calls: list[tuple[str, dict[str, str], float]] = []

        def get_bytes(url, headers, timeout):
            calls.append((url, dict(headers), timeout))
            name = unquote(url.rsplit("/", 1)[-1])
            return json.dumps({
                "name": name,
                "vhost": "/clouddsp",
                "messages": 2,
                "messages_ready": 1,
                "messages_unacknowledged": 1,
            }).encode()

        observer = RabbitMQQueueObserver(
            username="clouddsp-keda-scaler",
            password="observer-not-a-worker-password",
            get_bytes=get_bytes,
        )
        snapshot = observer.observe_once()

        self.assertEqual(len(snapshot.queues), 9)
        self.assertEqual(snapshot.message_count, 18)
        self.assertEqual(snapshot.ready_count, 9)
        self.assertEqual(snapshot.unacknowledged_count, 9)
        self.assertFalse(snapshot.is_drained)
        self.assertEqual(len(calls), 9)
        self.assertTrue(all("/api/queues/" in url for url, _, _ in calls))
        expected = "Basic " + base64.b64encode(
            b"clouddsp-keda-scaler:observer-not-a-worker-password"
        ).decode()
        self.assertTrue(all(headers["Authorization"] == expected for _, headers, _ in calls))
        self.assertNotIn("observer-not-a-worker-password", repr(snapshot))

    def test_rejects_queue_identity_and_inconsistent_counters(self) -> None:
        for payload in (
            {"name": "wrong", "vhost": "/clouddsp", "messages": 0,
             "messages_ready": 0, "messages_unacknowledged": 0},
            {"name": _QUEUE_NAMES[0], "vhost": "/clouddsp", "messages": 1,
             "messages_ready": 0, "messages_unacknowledged": 0},
        ):
            observer = RabbitMQQueueObserver(
                username="monitor",
                password="password",
                get_bytes=lambda *_args, payload=payload: json.dumps(payload).encode(),
            )
            with self.assertRaises(RabbitMQQueueObserverError):
                observer.observe_once()


if __name__ == "__main__":  # pragma: no cover - direct local teaching command.
    unittest.main()
