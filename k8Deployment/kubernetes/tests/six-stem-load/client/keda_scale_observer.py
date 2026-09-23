"""Read-only observer for the three worker Deployments and their KEDA objects.

Only this container receives a short-lived projected ServiceAccount token. Its
RBAC grants ``get`` on three named Deployments, the corresponding generated
HPAs, and three named ScaledObjects in ``clouddsp-app``; it cannot list, watch,
patch, scale, or create Kubernetes resources.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import ssl
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


_NAMESPACE = "clouddsp-app"
_KUBERNETES_API_BASE_URL = "https://kubernetes.default.svc:443"
_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
_CA_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
_WORKERS = {
    "demucs": ("clouddsp-demucs", "clouddsp-demucs-rabbitmq-scaler", 1),
    "basic-pitch": ("clouddsp-basic-pitch", "clouddsp-basic-pitch-rabbitmq-scaler", 3),
    "adtof": ("clouddsp-adtof", "clouddsp-adtof-rabbitmq-scaler", 2),
}
_MAX_RESPONSE_BYTES = 128 * 1024


class KEDAScaleObserverError(RuntimeError):
    """Safe Kubernetes metric-read failure without tokens or API diagnostics."""


@dataclass(frozen=True)
class WorkerScaleState:
    """One point-in-time scale and KEDA condition for a worker stage."""

    stage: str
    replicas: int
    ready_replicas: int
    hpa_current_replicas: int
    hpa_desired_replicas: int
    maximum_replicas: int
    active: bool | None
    ready: bool


@dataclass(frozen=True)
class KEDAScaleSnapshot:
    """The immutable three-stage state captured by one finite API read."""

    workers: tuple[WorkerScaleState, ...]

    @property
    def is_idle(self) -> bool:
        """True only when all workers are at zero and KEDA reports inactive."""

        return all(
            item.replicas == item.ready_replicas == item.hpa_current_replicas
            == item.hpa_desired_replicas == 0 and item.active is False and item.ready
            for item in self.workers
        )


JsonGet = Callable[[str], Mapping[str, object]]


@dataclass(frozen=True)
class KubernetesReadOnlyClient:
    """Use the observer's projected token and API CA; never use ambient kubectl."""

    get_json: JsonGet | None = None

    def get(self, path: str) -> Mapping[str, object]:
        """GET a fixed namespaced path and discard all non-200 response bodies."""

        if self.get_json is not None:
            value = self.get_json(path)
            if not isinstance(value, Mapping):
                raise KEDAScaleObserverError("Kubernetes API object was invalid")
            return value
        try:
            token = _TOKEN_PATH.read_text(encoding="ascii").strip()
            context = ssl.create_default_context(cafile=str(_CA_PATH))
            request = Request(
                f"{_KUBERNETES_API_BASE_URL}{path}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                method="GET",
            )
            with urlopen(request, timeout=5, context=context) as response:
                if response.status != 200:
                    raise KEDAScaleObserverError("Kubernetes API returned an error")
                payload = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(payload) > _MAX_RESPONSE_BYTES:
                raise KEDAScaleObserverError("Kubernetes API response exceeded its bound")
            value = json.loads(payload)
            if not isinstance(value, Mapping):
                raise KEDAScaleObserverError("Kubernetes API object was invalid")
            return value
        except KEDAScaleObserverError:
            raise
        except (HTTPError, URLError, OSError, TimeoutError, ValueError):
            # Never surface bearer-token, CA, address, or API error details.
            raise KEDAScaleObserverError("Kubernetes API observation failed") from None


@dataclass(frozen=True)
class KEDAScaleObserver:
    """Poll only the three reviewed worker controllers and their scalers."""

    client: KubernetesReadOnlyClient

    def observe_once(self) -> KEDAScaleSnapshot:
        """Read Deployment, generated HPA, and ScaledObject state per stage."""

        states: list[WorkerScaleState] = []
        for stage, (deployment_name, scaled_object_name, cap) in _WORKERS.items():
            prefix = f"/apis/apps/v1/namespaces/{_NAMESPACE}/deployments"
            deployment = self.client.get(f"{prefix}/{deployment_name}")
            hpa = self.client.get(
                f"/apis/autoscaling/v2/namespaces/{_NAMESPACE}/horizontalpodautoscalers/"
                f"keda-hpa-{scaled_object_name}"
            )
            scaled = self.client.get(
                f"/apis/keda.sh/v1alpha1/namespaces/{_NAMESPACE}/scaledobjects/"
                f"{scaled_object_name}"
            )
            _check_identity(deployment, name=deployment_name)
            _check_identity(hpa, name=f"keda-hpa-{scaled_object_name}")
            _check_identity(scaled, name=scaled_object_name)
            spec = _mapping(scaled.get("spec"), "ScaledObject spec")
            if (
                spec.get("minReplicaCount") != 0
                or spec.get("maxReplicaCount") != cap
                or _mapping(spec.get("scaleTargetRef"), "ScaledObject target").get("name")
                != deployment_name
            ):
                raise KEDAScaleObserverError("KEDA worker scale contract did not match")
            deployment_status = _mapping(deployment.get("status"), "Deployment status")
            hpa_status = _mapping(hpa.get("status"), "HPA status")
            states.append(WorkerScaleState(
                stage=stage,
                replicas=_count(deployment_status.get("replicas", 0)),
                ready_replicas=_count(deployment_status.get("readyReplicas", 0)),
                hpa_current_replicas=_count(hpa_status.get("currentReplicas", 0)),
                hpa_desired_replicas=_count(hpa_status.get("desiredReplicas", 0)),
                maximum_replicas=cap,
                active=_condition(scaled, "Active", "True"),
                ready=_condition(scaled, "Ready", "True") is True,
            ))
        return KEDAScaleSnapshot(workers=tuple(states))


def _check_identity(value: Mapping[str, object], *, name: str) -> None:
    """Refuse an API response for another resource or namespace."""

    metadata = _mapping(value.get("metadata"), "Kubernetes metadata")
    if metadata.get("name") != name or metadata.get("namespace") != _NAMESPACE:
        raise KEDAScaleObserverError("Kubernetes resource identity did not match")


def _mapping(value: object, purpose: str) -> Mapping[str, object]:
    """Require object-shaped Kubernetes JSON without exposing its contents."""

    if not isinstance(value, Mapping):
        raise KEDAScaleObserverError(f"Kubernetes {purpose} was invalid")
    return value


def _count(value: object) -> int:
    """Read a bounded non-negative replica count, rejecting JSON booleans."""

    if type(value) is not int or not 0 <= value <= 10_000:
        raise KEDAScaleObserverError("Kubernetes replica count was invalid")
    return value


def _condition(value: Mapping[str, object], condition_type: str, expected: str) -> bool | None:
    """Return a matching KEDA condition only when its status is canonical."""

    status = _mapping(value.get("status"), "ScaledObject status")
    conditions = status.get("conditions")
    if not isinstance(conditions, list):
        raise KEDAScaleObserverError("KEDA conditions were invalid")
    matches = [item for item in conditions if isinstance(item, Mapping) and item.get("type") == condition_type]
    if len(matches) != 1 or matches[0].get("status") not in {"True", "False", "Unknown"}:
        raise KEDAScaleObserverError("KEDA condition was missing or invalid")
    actual = matches[0]["status"]
    return actual == expected if actual in {"True", "False"} else None
