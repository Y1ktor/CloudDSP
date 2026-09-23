"""Source-only guardrails for the six-stem broker and observer image."""

from __future__ import annotations

from pathlib import Path
import unittest


_DIRECTORY = Path(__file__).resolve().parent
_DOCKERFILE = _DIRECTORY / "Dockerfile"


class SixStemLoadClientDockerfileTests(unittest.TestCase):
    """Keep the broker and read-only observer in a locked non-root runtime."""

    def test_image_contains_the_broker_runtime_graph_and_only_locked_dependencies(self) -> None:
        """The observer source is packaged, while other data-plane clients stay absent."""

        source = _DOCKERFILE.read_text(encoding="utf-8")
        for required in (
            "FROM docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS dependencies",
            "COPY requirements.lock ./requirements.lock",
            "RUN pip install --no-cache-dir --require-hashes --requirement requirements.lock",
            "COPY postgresql_observer_bootstrap.py ./postgresql_observer_bootstrap.py",
            "COPY postgresql_durable_state_observer.py ./postgresql_durable_state_observer.py",
            "COPY postgresql_observer_report.py ./postgresql_observer_report.py",
            "COPY load_test_terminal_outcome.py ./load_test_terminal_outcome.py",
            "COPY postgresql_observer_contract.py ./postgresql_observer_contract.py",
            "COPY keycloak_identity_lifecycle.py ./keycloak_identity_lifecycle.py",
            "COPY lifecycle_handoff.py ./lifecycle_handoff.py",
            "COPY lifecycle_broker.py ./lifecycle_broker.py",
            "COPY private_volume_setup.py ./private_volume_setup.py",
            "COPY test_private_volume_setup.py ./test_private_volume_setup.py",
            "COPY authenticated_load_client.py ./authenticated_load_client.py",
            "COPY owner_bound_job_verifier.py ./owner_bound_job_verifier.py",
            "COPY test_postgresql_durable_state_observer.py ./test_postgresql_durable_state_observer.py",
            "COPY test_postgresql_observer_report.py ./test_postgresql_observer_report.py",
            "COPY test_load_test_terminal_outcome.py ./test_load_test_terminal_outcome.py",
            "groupadd --gid 10012 clouddsp-six-stem-load",
            "USER 10012:10012",
            "COPY --from=validation --chown=10012:10012 /app/postgresql_durable_state_observer.py ./postgresql_durable_state_observer.py",
            "COPY --from=validation --chown=10012:10012 /app/postgresql_observer_report.py ./postgresql_observer_report.py",
            "COPY --from=validation --chown=10012:10012 /app/load_test_terminal_outcome.py ./load_test_terminal_outcome.py",
            "COPY --from=validation --chown=10012:10012 /app/private_volume_setup.py ./private_volume_setup.py",
            'ENTRYPOINT ["python", "/app/lifecycle_broker.py"]',
            "python -W error::ResourceWarning -m unittest discover",
        ):
            self.assertIn(required, source)
        for forbidden in (
            "apt-get",
            "boto3",
            "pika",
            "kubectl",
            "curl",
            "COPY . .",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":  # pragma: no cover - direct local teaching command.
    unittest.main()
