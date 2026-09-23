"""Offline tests for the read-only Kubernetes/KEDA API adapter."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from keda_scale_observer import (
    KEDAScaleObserver,
    KEDAScaleObserverError,
    KubernetesReadOnlyClient,
)


class KEDAScaleObserverTests(unittest.TestCase):
    """Keep all API paths and observed objects constrained to known workers."""

    def _objects(self, *, replicas: dict[str, int] | None = None):
        replicas = replicas or {"demucs": 0, "basic-pitch": 0, "adtof": 0}

        def get_json(path: str):
            stage = next((name for name in ("demucs", "basic-pitch", "adtof") if name in path), None)
            if stage is None:
                raise AssertionError("unexpected API path")
            if "/deployments/" in path:
                name = {"demucs": "clouddsp-demucs", "basic-pitch": "clouddsp-basic-pitch", "adtof": "clouddsp-adtof"}[stage]
                count = replicas[stage]
                return {"metadata": {"name": name, "namespace": "clouddsp-app"},
                        "status": {"replicas": count, "readyReplicas": count}}
            if "/horizontalpodautoscalers/" in path:
                name = f"keda-hpa-clouddsp-{stage}-rabbitmq-scaler"
                count = replicas[stage]
                return {"metadata": {"name": name, "namespace": "clouddsp-app"},
                        "status": {"currentReplicas": count, "desiredReplicas": count}}
            scaled = f"clouddsp-{stage}-rabbitmq-scaler"
            cap = {"demucs": 1, "basic-pitch": 3, "adtof": 2}[stage]
            deployment = {"demucs": "clouddsp-demucs", "basic-pitch": "clouddsp-basic-pitch", "adtof": "clouddsp-adtof"}[stage]
            return {
                "metadata": {"name": scaled, "namespace": "clouddsp-app"},
                "spec": {"minReplicaCount": 0, "maxReplicaCount": cap,
                         "scaleTargetRef": {"name": deployment}},
                "status": {"conditions": [
                    {"type": "Active", "status": "False" if replicas[stage] == 0 else "True"},
                    {"type": "Ready", "status": "True"},
                ]},
            }

        return get_json

    def test_observes_idle_deployments_hpas_and_scaledobjects(self) -> None:
        paths: list[str] = []
        getter = self._objects()
        observer = KEDAScaleObserver(KubernetesReadOnlyClient(
            get_json=lambda path: (paths.append(path), getter(path))[1]
        ))

        snapshot = observer.observe_once()

        self.assertTrue(snapshot.is_idle)
        self.assertEqual(len(snapshot.workers), 3)
        self.assertEqual(len(paths), 9)
        self.assertTrue(all("/namespaces/clouddsp-app/" in path for path in paths))
        self.assertFalse(any(path.endswith("/scale") for path in paths))

    def test_observes_active_scale_and_rejects_a_changed_scaledobject_cap(self) -> None:
        observer = KEDAScaleObserver(KubernetesReadOnlyClient(
            get_json=self._objects(replicas={"demucs": 1, "basic-pitch": 2, "adtof": 1})
        ))
        snapshot = observer.observe_once()
        self.assertFalse(snapshot.is_idle)
        self.assertEqual(tuple(item.hpa_current_replicas for item in snapshot.workers), (1, 2, 1))

        getter = self._objects()
        def wrong_cap(path: str):
            response = getter(path)
            if "/scaledobjects/" in path and "basic-pitch" in path:
                response["spec"]["maxReplicaCount"] = 99
            return response

        with self.assertRaises(KEDAScaleObserverError):
            KEDAScaleObserver(KubernetesReadOnlyClient(get_json=wrong_cap)).observe_once()

    def test_live_api_client_uses_fixed_service_dns_projected_token_and_ca(self) -> None:
        """The observer works with service links disabled and never skips TLS validation."""

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b'{"kind":"Status"}'

        captured = {}

        def fake_urlopen(request, *, timeout, context):
            captured.update(request=request, timeout=timeout, context=context)
            return Response()

        with (
            patch.object(Path, "read_text", return_value="projected-token"),
            patch("keda_scale_observer.ssl.create_default_context", return_value="verified-ca") as tls,
            patch("keda_scale_observer.urlopen", side_effect=fake_urlopen),
        ):
            result = KubernetesReadOnlyClient().get("/apis/apps/v1/namespaces/clouddsp-app/deployments/clouddsp-demucs")

        request = captured["request"]
        self.assertEqual(result["kind"], "Status")
        self.assertEqual(request.full_url, "https://kubernetes.default.svc:443/apis/apps/v1/namespaces/clouddsp-app/deployments/clouddsp-demucs")
        self.assertEqual(request.get_header("Authorization"), "Bearer projected-token")
        self.assertEqual(captured["timeout"], 5)
        self.assertEqual(captured["context"], "verified-ca")
        tls.assert_called_once()


if __name__ == "__main__":  # pragma: no cover - direct local teaching command.
    unittest.main()
