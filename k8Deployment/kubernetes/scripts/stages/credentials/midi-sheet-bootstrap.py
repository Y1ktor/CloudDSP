#!/usr/bin/env python3
"""Provision restricted score worker identities without committing secrets.

Run after committing versioned manifests and image references. Credentials
persist only in the ignored local JSON and namespaced Kubernetes Secrets.
"""

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
SERVICE = ROOT / "services" / "midi-sheet"
CONTEXT = "k3d-clouddsp-local"


def run(*args, stdin=None):
    completed = subprocess.run(["kubectl", "--context", CONTEXT, *args], input=stdin,
                               text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(f"kubectl operation failed: {' '.join(args[:3])}: {completed.stderr[:400]}")
    return completed.stdout


def apply_file(name):
    run("apply", "-f", str(SERVICE / name))


def wait_job(namespace, name):
    run("-n", namespace, "wait", "--for=condition=complete", f"job/{name}", "--timeout=240s")


def secret(namespace, name, values):
    # JSON is accepted by kubectl apply -f -. No credential appears in an
    # argument, shell interpolation, a log, or a committed source file.
    manifest = {"apiVersion": "v1", "kind": "Secret",
                "metadata": {"name": name, "namespace": namespace},
                "type": "Opaque", "stringData": values}
    run("apply", "-f", "-", stdin=json.dumps(manifest))


def load_or_create(path):
    if path.exists():
        data = json.loads(path.read_text())
    else:
        data = {"db_password": secrets.token_urlsafe(40),
                "rabbit_password": secrets.token_urlsafe(40),
                "s3_secret": secrets.token_urlsafe(40)}
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle)
            handle.write("\n")
    if not all(isinstance(data.get(key), str) and len(data[key]) >= 32
               for key in ("db_password", "rabbit_password", "s3_secret")):
        raise ValueError("Score worker credential file is incomplete.")
    return data


def bootstrap(path):
    creds = load_or_create(path)
    apply_file("rabbitmq-midi-sheet-topology-v001-configmap.yaml")
    apply_file("rabbitmq-midi-sheet-topology-v001-job.yaml")
    wait_job("clouddsp-data", "rabbitmq-midi-sheet-topology-v001-bootstrap")
    apply_file("migration-v012-configmap.yaml")
    apply_file("migration-v012-job.yaml")
    wait_job("clouddsp-app", "midi-sheet-migration-v012")

    secret("clouddsp-app", "clouddsp-midi-sheet-database-credentials",
           {"SHEET_DB_USER": "clouddsp-midi-sheet", "SHEET_DB_PASSWORD": creds["db_password"]})
    secret("clouddsp-data", "clouddsp-midi-sheet-database-bootstrap-credentials",
           {"SHEET_DB_USER": "clouddsp-midi-sheet", "SHEET_DB_PASSWORD": creds["db_password"]})
    secret("clouddsp-app", "clouddsp-midi-sheet-rabbitmq-credentials",
           {"RABBITMQ_MIDI_SHEET_USERNAME": "clouddsp-midi-sheet",
            "RABBITMQ_MIDI_SHEET_PASSWORD": creds["rabbit_password"]})
    secret("clouddsp-data", "clouddsp-midi-sheet-rabbitmq-bootstrap-credentials",
           {"RABBITMQ_MIDI_SHEET_USERNAME": "clouddsp-midi-sheet",
            "RABBITMQ_MIDI_SHEET_PASSWORD": creds["rabbit_password"]})
    secret("clouddsp-app", "clouddsp-midi-sheet-minio-credentials",
           {"MIDI_SHEET_S3_ACCESS_KEY": "clouddsp-midi-sheet",
            "MIDI_SHEET_S3_SECRET_KEY": creds["s3_secret"]})
    secret("clouddsp-data", "clouddsp-midi-sheet-minio-bootstrap-credentials",
           {"MIDI_SHEET_S3_ACCESS_KEY": "clouddsp-midi-sheet",
            "MIDI_SHEET_S3_SECRET_KEY": creds["s3_secret"]})

    apply_file("minio-policy-v001-configmap.yaml")
    apply_file("job-api-result-read-policy-v001-configmap.yaml")
    for file, namespace, name in (
        ("database-bootstrap-v001-job.yaml", "clouddsp-data", "midi-sheet-database-bootstrap-v001"),
        ("rabbitmq-bootstrap-v001-job.yaml", "clouddsp-data", "rabbitmq-midi-sheet-consumer-bootstrap"),
        ("minio-bootstrap-v001-job.yaml", "clouddsp-data", "minio-midi-sheet-bootstrap"),
        ("job-api-result-read-bootstrap-v001-job.yaml", "clouddsp-data", "minio-job-api-midi-sheet-results-bootstrap-v001"),
    ):
        apply_file(file)
        wait_job(namespace, name)

    # These temporary data-namespace copies were read by finite admin Jobs.
    # Runtime Pods receive only the app-namespace restricted Secrets.
    for name in ("clouddsp-midi-sheet-database-bootstrap-credentials",
                 "clouddsp-midi-sheet-rabbitmq-bootstrap-credentials",
                 "clouddsp-midi-sheet-minio-bootstrap-credentials"):
        run("-n", "clouddsp-data", "delete", "secret", name)
    apply_file("notification-v001-job.yaml")
    wait_job("clouddsp-data", "minio-midi-sheet-notification-bootstrap-v001")
    print("Score worker migration and three restricted identities are ready.")


def bootstrap_score_history():
    """Add only the existing API identity's read-only score-preview policy.

    No worker credentials, migration, processing queue, or permanent workload
    changes here. Existing clusters can run this stage independently; fresh
    worker bootstrap includes it after the existing API account is available.
    """

    apply_file("job-api-source-read-policy-v001-configmap.yaml")
    apply_file("job-api-source-read-bootstrap-v001-job.yaml")
    wait_job("clouddsp-data", "minio-job-api-midi-sheet-sources-bootstrap-v001")
    print("Job API score-source history preview permission is ready.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--secrets-file", type=Path)
    parser.add_argument("--history-only", action="store_true",
                        help="Apply only the score-history source-preview IAM stage.")
    args = parser.parse_args()
    if not args.history_only and args.secrets_file is None:
        parser.error("--secrets-file is required unless --history-only is selected")
    try:
        if not args.history_only:
            bootstrap(args.secrets_file)
        bootstrap_score_history()
    except Exception as error:
        print(f"Score worker bootstrap failed: {error}", file=sys.stderr)
        raise SystemExit(1)
