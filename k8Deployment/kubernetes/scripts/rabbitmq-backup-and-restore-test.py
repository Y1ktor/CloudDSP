#!/usr/bin/env python3
"""Archive the stopped local broker PVC and rehearse an isolated restore.

RabbitMQ keeps its node identity, users, topology, and durable queue data in
one PVC. The script checks that no message is unacknowledged, briefly stops
only the broker, copies that exact bound node-local directory, resumes the
original StatefulSet, and starts the reviewed image against an extracted
copy with the same short node name and no Docker network. It compares full
definitions (including password hashes) and queue depths in memory without
printing or writing those values separately from the private volume archive.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
BACKUP_ROOT = ROOT / ".local" / "backups"
CONTEXT = "k3d-clouddsp-local"
NAMESPACE = "clouddsp-data"
STATEFULSET = "clouddsp-rabbitmq"
POD = "clouddsp-rabbitmq-0"
PVC = "rabbitmq-data-clouddsp-rabbitmq-0"
NODE_NAME = "rabbit@clouddsp-rabbitmq-0"
SERVER_IMAGE = (
    "docker.io/library/rabbitmq@"
    "sha256:b1188ba346ab5748add82f46ac58c7df993ee91d762915f8ac6fcadf2e6c07df"
)


class BackupFailure(RuntimeError):
    """A diagnostic with no Secret values, user names, or message contents."""


def execute(label, *args, output=None):
    """Run an argument vector and suppress child stderr and private stdout."""
    if output is None:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode:
            raise BackupFailure(f"{label} failed")
        return result.stdout
    result = subprocess.run(args, stdout=output, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise BackupFailure(f"{label} failed")
    return b""


def kube(label, *args):
    return execute(label, "kubectl", "--context", CONTEXT, "-n", NAMESPACE, *args)


def kube_json(label, *args):
    try:
        return json.loads(kube(label, *args, "-o", "json"))
    except ValueError as error:
        raise BackupFailure(f"{label} returned invalid JSON") from error


def check_storage():
    """Identify the exact PVC/PV and node-local directory before any pause."""
    statefulset = kube_json("RabbitMQ StatefulSet lookup", "get", f"statefulset/{STATEFULSET}")
    claim = kube_json("RabbitMQ PVC lookup", "get", f"pvc/{PVC}")
    if statefulset.get("spec", {}).get("replicas") != 1 or statefulset.get("status", {}).get("readyReplicas") != 1:
        raise BackupFailure("RabbitMQ backup requires exactly one Ready replica")
    if claim.get("status", {}).get("phase") != "Bound":
        raise BackupFailure("RabbitMQ PVC is not Bound")
    volume_name = claim.get("spec", {}).get("volumeName", "")
    if not volume_name:
        raise BackupFailure("RabbitMQ PVC has no bound PV")
    volume = kube_json("RabbitMQ PV lookup", "get", f"pv/{volume_name}")
    local_path = volume.get("spec", {}).get("local", {}).get("path", "")
    terms = volume.get("spec", {}).get("nodeAffinity", {}).get("required", {}).get("nodeSelectorTerms", [])
    nodes = [value for term in terms for expression in term.get("matchExpressions", [])
             if expression.get("key") == "kubernetes.io/hostname" for value in expression.get("values", [])]
    if len(nodes) != 1 or nodes[0] not in {
        "k3d-clouddsp-local-server-0", "k3d-clouddsp-local-agent-0", "k3d-clouddsp-local-agent-1"
    }:
        raise BackupFailure("RabbitMQ PV node binding is outside the reviewed k3d cluster")
    if not local_path.startswith("/var/lib/rancher/k3s/storage/") or claim["metadata"]["uid"] not in local_path:
        raise BackupFailure("RabbitMQ PV path does not match the bound PVC")
    execute("RabbitMQ volume directory check", "docker", "exec", nodes[0], "test", "-d", local_path)
    return {
        "statefulSetUid": statefulset["metadata"]["uid"],
        "pvcUid": claim["metadata"]["uid"],
        "pvName": volume_name,
        "node": nodes[0],
        "path": local_path,
    }


def rabbit_cli(target, label, *args):
    """Invoke the installed broker CLI on either the live or scratch node."""
    if target == "live":
        return kube(label, "exec", POD, "--", "rabbitmqctl", "-q", *args)
    return execute(label, "docker", "exec", "--user", "rabbitmq", target,
                   "rabbitmqctl", "-q", *args)


def rabbit_json(target, label, *args):
    try:
        return json.loads(rabbit_cli(target, label, *args))
    except ValueError as error:
        raise BackupFailure(f"{label} returned invalid JSON") from error


def broker_inventory(target):
    """Compare all definitions and ready/unacknowledged queue counts."""
    actual_node = rabbit_cli(target, "RabbitMQ node identity lookup", "eval", "node().").decode().strip()
    if actual_node != f"'{NODE_NAME}'":
        raise BackupFailure("RabbitMQ node name differs from the persisted identity")
    definitions = rabbit_json(target, "RabbitMQ definitions export", "export_definitions", "-")
    # Definition arrays have no semantic order. Canonical sorting retains
    # every field, including password hashes, without emitting their values.
    for key, value in definitions.items():
        if isinstance(value, list):
            definitions[key] = sorted(value, key=lambda item: json.dumps(item, sort_keys=True))
    queues = {}
    for vhost in definitions.get("vhosts", []):
        name = vhost["name"]
        entries = rabbit_json(target, "RabbitMQ queue inventory", "list_queues", "-p", name,
                              "name", "durable", "messages_ready", "messages_unacknowledged",
                              "--formatter", "json")
        queues[name] = sorted(entries, key=lambda item: item["name"])
    return {"definitions": definitions, "queues": queues}


def total(inventory, field):
    return sum(row[field] for entries in inventory["queues"].values() for row in entries)


def start_live():
    kube("RabbitMQ scale-up", "scale", f"statefulset/{STATEFULSET}", "--replicas=1")
    kube("RabbitMQ StatefulSet rollout", "rollout", "status", f"statefulset/{STATEFULSET}", "--timeout=240s")


def snapshot(node, path, archive):
    """Stream a consistent stopped-volume tar into an owner-only file."""
    descriptor = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            execute("RabbitMQ stopped-volume archive", "docker", "exec", node,
                    "tar", "-C", path, "-czf", "-", ".", output=output)
    except Exception:
        archive.unlink(missing_ok=True)
        raise
    if archive.stat().st_size == 0:
        archive.unlink()
        raise BackupFailure("RabbitMQ stopped-volume archive is empty")


def load_archive(archive, volume):
    """Unpack into a Linux Docker volume so the broker can own its cookie."""
    with archive.open("rb") as stream:
        result = subprocess.run(
            ["docker", "run", "--rm", "--interactive", "--network", "none", "--volume", f"{volume}:/var/lib/rabbitmq",
             "--entrypoint", "tar", SERVER_IMAGE, "-xzf", "-", "-C", "/var/lib/rabbitmq"],
            stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
    if result.returncode:
        raise BackupFailure("RabbitMQ archive extraction into isolated volume failed")


def wait_for_scratch(container):
    for _ in range(120):
        state = subprocess.run(["docker", "inspect", "--format", "{{.State.Running}}", container],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        if state.returncode or state.stdout.strip() != b"true":
            raise BackupFailure("isolated RabbitMQ container stopped during restore")
        result = subprocess.run(["docker", "exec", "--user", "rabbitmq", container,
                                 "rabbitmq-diagnostics", "-q", "check_running"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if result.returncode == 0:
            return
        time.sleep(1)
    raise BackupFailure("isolated RabbitMQ restore did not become Ready")


def main():
    if len(sys.argv) != 1:
        raise BackupFailure("usage: rabbitmq-backup-and-restore-test.py")
    execute("Docker readiness check", "docker", "info", "--format", "{{.ServerVersion}}")
    storage = check_storage()
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    BACKUP_ROOT.chmod(0o700)
    backup = BACKUP_ROOT / f"rabbitmq-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{os.getpid()}"
    backup.mkdir(mode=0o700)
    archive = backup / "volume.tar.gz"
    container = f"clouddsp-rabbitmq-restore-{os.getpid()}"
    volume = f"clouddsp-rabbitmq-restore-{os.getpid()}"
    scaled_down = False
    container_started = False
    volume_created = False
    error = None
    try:
        print("RabbitMQ backup: recording private topology and queue inventory", flush=True)
        before = broker_inventory("live")
        if total(before, "messages_unacknowledged"):
            raise BackupFailure("RabbitMQ has unacknowledged messages; wait for consumers to drain before backup")
        print("RabbitMQ backup: stopping the single broker for a consistent PVC archive", flush=True)
        kube("RabbitMQ scale-down", "scale", f"statefulset/{STATEFULSET}", "--replicas=0")
        scaled_down = True
        kube("RabbitMQ Pod termination", "wait", f"pod/{POD}", "--for=delete", "--timeout=240s")
        snapshot(storage["node"], storage["path"], archive)
        print("RabbitMQ backup: restarting the original StatefulSet", flush=True)
        start_live()
        scaled_down = False
        after_storage = check_storage()
        for key in ("statefulSetUid", "pvcUid", "pvName"):
            if storage[key] != after_storage[key]:
                raise BackupFailure("original RabbitMQ StatefulSet or PVC/PV identity changed during backup")
        if broker_inventory("live") != before:
            raise BackupFailure("live RabbitMQ topology or queue inventory changed during backup")

        execute("isolated RabbitMQ volume creation", "docker", "volume", "create",
                "--name", volume, "--label", "clouddsp.io/purpose=rabbitmq-restore-test")
        volume_created = True
        load_archive(archive, volume)
        # The broker's entrypoint adjusts permissions inside this disposable
        # Linux volume. A macOS bind mount cannot reliably chown its copied
        # Erlang cookie. Matching the original short hostname makes Erlang
        # open the persisted node directory rather than create a fresh node.
        # No network or production Secret is passed to this test container.
        print("RabbitMQ backup: starting an isolated broker from the archive", flush=True)
        execute("isolated RabbitMQ start", "docker", "run", "--detach",
                "--name", container, "--hostname", POD, "--network", "none",
                "--volume", f"{volume}:/var/lib/rabbitmq", SERVER_IMAGE)
        container_started = True
        wait_for_scratch(container)
        if broker_inventory(container) != before:
            raise BackupFailure("isolated RabbitMQ restore differs in definitions or queue inventory")

        with archive.open("rb") as stream:
            archive_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        manifest = {
            "statefulSetUid": storage["statefulSetUid"],
            "pvcUid": storage["pvcUid"],
            "pvName": storage["pvName"],
            "archiveSha256": archive_sha256,
            "archiveBytes": archive.stat().st_size,
            "vhosts": len(before["definitions"].get("vhosts", [])),
            "queues": sum(len(entries) for entries in before["queues"].values()),
            "readyMessages": total(before, "messages_ready"),
            "verifiedAtUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        (backup / "verified.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (backup / "verified.json").chmod(0o600)
        print(f"RabbitMQ backup and isolated restore passed: {manifest['vhosts']} vhosts, {manifest['queues']} queues, {manifest['readyMessages']} ready messages; original PVC retained.")
        print(f"Private snapshot retained at {archive} ({manifest['archiveBytes']} bytes, mode 600).")
    except Exception as exc:
        error = exc if isinstance(exc, BackupFailure) else BackupFailure("unexpected error while testing the RabbitMQ backup")
    finally:
        if scaled_down:
            try:
                start_live()
            except BackupFailure:
                error = BackupFailure("RabbitMQ restore failed to restart the original StatefulSet")
        if container_started:
            cleanup = subprocess.run(["docker", "rm", "-f", container],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if cleanup.returncode:
                error = BackupFailure("isolated RabbitMQ restore container cleanup failed")
        if volume_created:
            cleanup = subprocess.run(["docker", "volume", "rm", "-f", volume],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if cleanup.returncode:
                error = BackupFailure("isolated RabbitMQ restore volume cleanup failed")
    if error:
        raise error


if __name__ == "__main__":
    try:
        main()
    except BackupFailure as failure:
        print(f"RabbitMQ backup stopped: {failure}", file=sys.stderr)
        raise SystemExit(1) from None
