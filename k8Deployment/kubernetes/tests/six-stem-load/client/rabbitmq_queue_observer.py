"""Read RabbitMQ processing/retry/DLQ depths through its private HTTP API.

This adapter uses the existing RabbitMQ ``monitoring`` identity exposed to
KEDA, not an AMQP worker identity. It calls only nine fixed queue-metric URLs,
parses counts, and never reads deliveries or message bodies. RabbitMQ is shared
in this local profile, so the bounded load test requires these queues to be
empty before its authenticated client starts and drained after its work ends.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import json
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener


_MANAGEMENT_BASE_URL = "http://clouddsp-rabbitmq-management.clouddsp-data.svc:15672"
_VHOST_PATH = quote("/clouddsp", safe="")
_QUEUE_NAMES = (
    "clouddsp.demucs.requests",
    "clouddsp.demucs.requests.retry.30s",
    "clouddsp.demucs.requests.dlq",
    "clouddsp.basic-pitch.requests",
    "clouddsp.basic-pitch.requests.retry.30s",
    "clouddsp.basic-pitch.requests.dlq",
    "clouddsp.adtof.requests",
    "clouddsp.adtof.requests.retry.30s",
    "clouddsp.adtof.requests.dlq",
)
_MAX_RESPONSE_BYTES = 32 * 1024


class RabbitMQQueueObserverError(RuntimeError):
    """Safe metric-read failure without credentials, URLs, or broker response text."""


@dataclass(frozen=True)
class RabbitMQQueueDepth:
    """One fixed queue's three public counters; no message data is retained."""

    queue_name: str = field(repr=False)
    messages: int
    messages_ready: int
    messages_unacknowledged: int


@dataclass(frozen=True)
class RabbitMQQueueSnapshot:
    """Immutable counts for the known processing queues and their retry/DLQs."""

    queues: tuple[RabbitMQQueueDepth, ...]

    @property
    def message_count(self) -> int:
        """Count queued and delivered-but-unacknowledged messages together."""

        return sum(queue.messages for queue in self.queues)

    @property
    def ready_count(self) -> int:
        """Sum broker-ready deliveries without inspecting any body."""

        return sum(queue.messages_ready for queue in self.queues)

    @property
    def unacknowledged_count(self) -> int:
        """Sum active deliveries whose consumer has not yet acknowledged."""

        return sum(queue.messages_unacknowledged for queue in self.queues)

    @property
    def is_drained(self) -> bool:
        """Require every known main, retry, and dead-letter queue to be empty."""

        return all(queue.messages == 0 for queue in self.queues)


HttpGet = Callable[[str, Mapping[str, str], float], bytes]


@dataclass(frozen=True)
class RabbitMQQueueObserver:
    """Bounded read-only RabbitMQ management client for one monitoring identity."""

    username: str = field(repr=False)
    password: str = field(repr=False)
    get_bytes: HttpGet | None = field(default=None, repr=False)

    def observe_once(self) -> RabbitMQQueueSnapshot:
        """Fetch each queue's metrics and reject malformed/inconsistent counts."""

        if not self.username or not self.password:
            raise RabbitMQQueueObserverError("RabbitMQ monitoring credential was absent")
        authentication = base64.b64encode(
            f"{self.username}:{self.password}".encode("utf-8")
        ).decode("ascii")
        getter = self.get_bytes or _http_get
        observed: list[RabbitMQQueueDepth] = []
        for queue_name in _QUEUE_NAMES:
            url = (
                f"{_MANAGEMENT_BASE_URL}/api/queues/{_VHOST_PATH}/"
                f"{quote(queue_name, safe='')}"
            )
            try:
                payload = getter(
                    url,
                    {
                        "Authorization": f"Basic {authentication}",
                        "Accept": "application/json",
                    },
                    5.0,
                )
                value = _json_object(payload)
                if value.get("name") != queue_name or value.get("vhost") != "/clouddsp":
                    raise RabbitMQQueueObserverError("RabbitMQ queue identity did not match")
                counts = tuple(
                    _nonnegative_count(value.get(name), purpose=name)
                    for name in ("messages", "messages_ready", "messages_unacknowledged")
                )
                if counts[0] != counts[1] + counts[2]:
                    raise RabbitMQQueueObserverError("RabbitMQ queue counters were inconsistent")
                observed.append(RabbitMQQueueDepth(queue_name=queue_name, **dict(zip(
                    ("messages", "messages_ready", "messages_unacknowledged"),
                    counts,
                    strict=True,
                ))))
            except RabbitMQQueueObserverError:
                raise
            except Exception:
                # urllib, HTTP errors, JSON diagnostics, and server responses
                # can include account details or broker metadata; expose none.
                raise RabbitMQQueueObserverError("RabbitMQ queue observation failed") from None
        return RabbitMQQueueSnapshot(queues=tuple(observed))


class _NoRedirectHandler(HTTPRedirectHandler):
    """Management metrics are pinned to one in-cluster endpoint, not redirects."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _http_get(url: str, headers: Mapping[str, str], timeout: float) -> bytes:
    """Perform one HTTP GET without following redirects or returning error bodies."""

    request = Request(url, headers=dict(headers), method="GET")
    opener = build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise RabbitMQQueueObserverError("RabbitMQ management API returned an error")
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
    except RabbitMQQueueObserverError:
        raise
    except (HTTPError, URLError, OSError, TimeoutError):
        raise RabbitMQQueueObserverError("RabbitMQ management API was unavailable") from None
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise RabbitMQQueueObserverError("RabbitMQ management response exceeded its bound")
    return payload


def _json_object(payload: object) -> Mapping[str, object]:
    """Parse one small object response while hiding JSON parser details."""

    if not isinstance(payload, bytes) or len(payload) > _MAX_RESPONSE_BYTES:
        raise RabbitMQQueueObserverError("RabbitMQ management response was invalid")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RabbitMQQueueObserverError("RabbitMQ management response was invalid") from None
    if not isinstance(value, Mapping):
        raise RabbitMQQueueObserverError("RabbitMQ management response was invalid")
    return value


def _nonnegative_count(value: object, *, purpose: str) -> int:
    """Reject boolean, negative, float, or malformed RabbitMQ counters."""

    if type(value) is not int or value < 0:
        raise RabbitMQQueueObserverError(f"RabbitMQ {purpose} count was invalid")
    return value
