#!/usr/bin/env python3
"""Capture and restore-test the one-Pod local MinIO volume before Helm takeover.

The local-path PVC contains objects, versions, IAM state, bucket settings, and
the persistent AMQP event queue. A stopped-volume archive preserves all of
these together. This script briefly stops only the MinIO StatefulSet, copies
its PVC from the bound k3d node, restores the same replica, and starts a
disposable server against an extracted copy. No command prints credentials,
object keys, object data, or MinIO's administrative responses.
"""

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[2]
BACKUP_ROOT = ROOT / ".local" / "backups"
CONTEXT = "k3d-clouddsp-local"
NAMESPACE = "clouddsp-data"
STATEFULSET = "clouddsp-minio"
POD = "clouddsp-minio-0"
PVC = "minio-data-clouddsp-minio-0"
LIVE_ENDPOINT = "http://minio.localhost:8080"
SERVER_IMAGE = (
    "clouddsp-registry.localhost:5001/minio@"
    "sha256:96dd150e3e8c93ebc407344078829c43240df19c74db58de17dd777d7d28ab5e"
)


class BackupFailure(RuntimeError):
    """A fixed diagnostic that contains no credentials or object names."""


def execute(label, *args, env=None, output=None):
    """Run an argument-vector command without shell interpolation or log data."""
    if output is None:
        result = subprocess.run(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode:
            raise BackupFailure(f"{label} failed")
        return result.stdout
    result = subprocess.run(args, env=env, stdout=output, stderr=subprocess.PIPE, check=False)
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
    """Return durable IDs and the exact node-local directory to archive."""
    statefulset = kube_json("MinIO StatefulSet lookup", "get", f"statefulset/{STATEFULSET}")
    claim = kube_json("MinIO PVC lookup", "get", f"pvc/{PVC}")
    if statefulset.get("spec", {}).get("replicas") != 1 or statefulset.get("status", {}).get("readyReplicas") != 1:
        raise BackupFailure("MinIO backup requires exactly one Ready replica")
    if claim.get("status", {}).get("phase") != "Bound":
        raise BackupFailure("MinIO PVC is not Bound")
    volume_name = claim.get("spec", {}).get("volumeName", "")
    if not volume_name:
        raise BackupFailure("MinIO PVC has no bound PV")
    volume = kube_json("MinIO PV lookup", "get", f"pv/{volume_name}")
    local_path = volume.get("spec", {}).get("local", {}).get("path", "")
    terms = volume.get("spec", {}).get("nodeAffinity", {}).get("required", {}).get("nodeSelectorTerms", [])
    nodes = [value for term in terms for expression in term.get("matchExpressions", [])
             if expression.get("key") == "kubernetes.io/hostname" for value in expression.get("values", [])]
    if len(nodes) != 1 or nodes[0] not in {
        "k3d-clouddsp-local-server-0", "k3d-clouddsp-local-agent-0", "k3d-clouddsp-local-agent-1"
    }:
        raise BackupFailure("MinIO PV node binding is outside the reviewed k3d cluster")
    if not local_path.startswith("/var/lib/rancher/k3s/storage/") or claim["metadata"]["uid"] not in local_path:
        raise BackupFailure("MinIO PV path does not match the bound PVC")
    execute("MinIO volume directory check", "docker", "exec", nodes[0], "test", "-d", local_path)
    return {
        "statefulSetUid": statefulset["metadata"]["uid"],
        "pvcUid": claim["metadata"]["uid"],
        "pvName": volume_name,
        "node": nodes[0],
        "path": local_path,
    }


def root_environment():
    """Use the live Kubernetes Secrets only in child-process environments."""
    secret = kube_json("MinIO root Secret lookup", "get", "secret/clouddsp-minio-root-credentials")
    amqp_secret = kube_json("MinIO AMQP Secret lookup", "get", "secret/clouddsp-minio-source-intake-rabbitmq-credentials")
    try:
        user = base64.b64decode(secret["data"]["MINIO_ROOT_USER"], validate=True).decode("utf-8")
        password = base64.b64decode(secret["data"]["MINIO_ROOT_PASSWORD"], validate=True).decode("utf-8")
        amqp_url = base64.b64decode(amqp_secret["data"]["MINIO_NOTIFY_AMQP_URL_INTAKE"], validate=True).decode("utf-8")
    except (KeyError, ValueError, UnicodeError) as error:
        raise BackupFailure("MinIO root Secret is incomplete") from error
    if not user or not password or not amqp_url:
        raise BackupFailure("MinIO root or AMQP Secret contains an empty credential")
    env = os.environ.copy()
    env.update({
        "AWS_ACCESS_KEY_ID": user,
        "AWS_SECRET_ACCESS_KEY": password,
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_PAGER": "",
        "MINIO_ROOT_USER": user,
        "MINIO_ROOT_PASSWORD": password,
        # An isolated server must register the same AMQP target or MinIO
        # suppresses its saved QueueConfiguration from the S3 API response.
        # These values mirror the versioned StatefulSet; only the URI is
        # secret. No scratch upload is performed, so it sends no AMQP event.
        "MINIO_NOTIFY_AMQP_ENABLE_INTAKE": "on",
        "MINIO_NOTIFY_AMQP_URL_INTAKE": amqp_url,
        "MINIO_NOTIFY_AMQP_EXCHANGE_INTAKE": "clouddsp.source-events",
        "MINIO_NOTIFY_AMQP_EXCHANGE_TYPE_INTAKE": "direct",
        "MINIO_NOTIFY_AMQP_ROUTING_KEY_INTAKE": "source.upload.created",
        "MINIO_NOTIFY_AMQP_MANDATORY_INTAKE": "on",
        "MINIO_NOTIFY_AMQP_DURABLE_INTAKE": "on",
        "MINIO_NOTIFY_AMQP_NO_WAIT_INTAKE": "off",
        "MINIO_NOTIFY_AMQP_INTERNAL_INTAKE": "off",
        "MINIO_NOTIFY_AMQP_AUTO_DELETED_INTAKE": "off",
        "MINIO_NOTIFY_AMQP_DELIVERY_MODE_INTAKE": "2",
        "MINIO_NOTIFY_AMQP_PUBLISHING_CONFIRMS_INTAKE": "on",
        "MINIO_NOTIFY_AMQP_QUEUE_DIR_INTAKE": "/data/.minio-events/amqp-intake",
        "MINIO_NOTIFY_AMQP_QUEUE_LIMIT_INTAKE": "10000",
    })
    return env


def s3_json(label, endpoint, env, *args, missing_policy_ok=False):
    command = ["aws", "--no-cli-pager", "--endpoint-url", endpoint, "s3api", *args, "--output", "json"]
    result = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        if missing_policy_ok and b"NoSuchBucketPolicy" in result.stderr:
            return None
        raise BackupFailure(f"{label} failed")
    # AWS CLI emits zero bytes for successful empty notification/versioning
    # responses, while a configured response is JSON. Treat both as {}.
    if not result.stdout.strip():
        return {}
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise BackupFailure(f"{label} returned invalid JSON") from error


def inventory(endpoint, env):
    """Compare object versions and S3-visible bucket rules without printing keys."""
    names = sorted(bucket["Name"] for bucket in s3_json("MinIO bucket listing", endpoint, env, "list-buckets")["Buckets"])
    result = {}
    for name in names:
        current = s3_json("MinIO object listing", endpoint, env, "list-objects-v2", "--bucket", name)
        versions = s3_json("MinIO version listing", endpoint, env, "list-object-versions", "--bucket", name)
        notification = s3_json("MinIO notification lookup", endpoint, env,
                               "get-bucket-notification-configuration", "--bucket", name)
        versioning = s3_json("MinIO versioning lookup", endpoint, env, "get-bucket-versioning", "--bucket", name)
        policy = s3_json("MinIO bucket policy lookup", endpoint, env,
                         "get-bucket-policy", "--bucket", name, missing_policy_ok=True)
        result[name] = {
            "current": sorted((obj["Key"], obj["ETag"], obj["Size"]) for obj in current.get("Contents", [])),
            "versions": sorted((obj["Key"], obj["VersionId"], obj.get("ETag"), obj.get("Size"))
                               for obj in versions.get("Versions", [])),
            "deleteMarkers": sorted((obj["Key"], obj["VersionId"])
                                    for obj in versions.get("DeleteMarkers", [])),
            "notification": notification,
            "versioning": versioning,
            "policy": json.loads(policy["Policy"]) if policy else None,
        }
    return result


def wait_for_live():
    kube("MinIO StatefulSet rollout", "rollout", "status", f"statefulset/{STATEFULSET}", "--timeout=180s")


def start_live():
    kube("MinIO scale-up", "scale", f"statefulset/{STATEFULSET}", "--replicas=1")
    wait_for_live()


def snapshot(node, path, archive):
    """Stream tar directly into an owner-only file; never buffer object data."""
    descriptor = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            execute("MinIO stopped-volume archive", "docker", "exec", node,
                    "tar", "-C", path, "-czf", "-", ".", output=output)
    except Exception:
        archive.unlink(missing_ok=True)
        raise
    if archive.stat().st_size == 0:
        archive.unlink()
        raise BackupFailure("MinIO stopped-volume archive is empty")


def wait_for_scratch(endpoint):
    for _ in range(90):
        try:
            with urlopen(f"{endpoint}/minio/health/ready", timeout=2) as response:
                if response.status == 200:
                    return
        except (URLError, OSError, TimeoutError):
            pass
        time.sleep(1)
    raise BackupFailure("isolated MinIO restore did not become Ready")


def object_round_trip(original, restored, env, scratch):
    """Read one current object's bytes from both servers and compare hashes."""
    pair = next(((bucket, objects["current"][0][0]) for bucket, objects in original.items()
                 if objects["current"]), None)
    if pair is None:
        return False
    for endpoint, filename in ((LIVE_ENDPOINT, "live-object"), (restored, "restored-object")):
        target = scratch / filename
        execute("MinIO object read", "aws", "--no-cli-pager", "--endpoint-url", endpoint,
                "s3api", "get-object", "--bucket", pair[0], "--key", pair[1], str(target), env=env)
    def digest(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").digest()
    return digest(scratch / "live-object") == digest(scratch / "restored-object")


def main():
    if len(sys.argv) != 1:
        raise BackupFailure("usage: minio-backup-and-restore-test.py")
    execute("Docker readiness check", "docker", "info", "--format", "{{.ServerVersion}}")
    storage = check_storage()
    env = root_environment()
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    BACKUP_ROOT.chmod(0o700)
    backup = BACKUP_ROOT / f"minio-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{os.getpid()}"
    backup.mkdir(mode=0o700)
    archive = backup / "volume.tar.gz"
    scratch = backup / ".restore-test"
    container = f"clouddsp-minio-restore-{os.getpid()}"
    scaled_down = False
    container_started = False
    error = None
    try:
        print("MinIO backup: recording private S3 inventory", flush=True)
        before = inventory(LIVE_ENDPOINT, env)
        print("MinIO backup: stopping the single server for a consistent PVC archive", flush=True)
        kube("MinIO scale-down", "scale", f"statefulset/{STATEFULSET}", "--replicas=0")
        scaled_down = True
        kube("MinIO Pod termination", "wait", f"pod/{POD}", "--for=delete", "--timeout=180s")
        snapshot(storage["node"], storage["path"], archive)
        print("MinIO backup: restarting the original StatefulSet", flush=True)
        start_live()
        scaled_down = False
        after_storage = check_storage()
        if storage["statefulSetUid"] != after_storage["statefulSetUid"] or storage["pvcUid"] != after_storage["pvcUid"] or storage["pvName"] != after_storage["pvName"]:
            raise BackupFailure("original StatefulSet or PVC/PV identity changed during backup")
        if inventory(LIVE_ENDPOINT, env) != before:
            raise BackupFailure("live S3 inventory changed during backup; snapshot is not a current baseline")
        scratch.mkdir(mode=0o700)
        execute("MinIO archive extraction", "tar", "-xzf", str(archive), "-C", str(scratch))
        # The restored server sees only this disposable extracted copy. It
        # receives the live root identity through Docker's environment-name
        # forwarding, with no credential in command arguments or logs.
        print("MinIO backup: starting an isolated server from the archive", flush=True)
        amqp_names = sorted(name for name in env if name.startswith("MINIO_NOTIFY_AMQP_"))
        execute("isolated MinIO start", "docker", "run", "--rm", "--detach",
                "--name", container, "--user", f"{os.getuid()}:{os.getgid()}",
                "--publish", "127.0.0.1::9000", "--volume", f"{scratch}:/data",
                "--env", "MINIO_ROOT_USER", "--env", "MINIO_ROOT_PASSWORD",
                *(part for name in amqp_names for part in ("--env", name)),
                SERVER_IMAGE, "server", "/data", "--console-address", ":9001", env=env)
        container_started = True
        port = execute("isolated MinIO port lookup", "docker", "port", container, "9000/tcp").decode().strip()
        if not port.startswith("127.0.0.1:"):
            raise BackupFailure("isolated MinIO was not bound only to loopback")
        restored_endpoint = f"http://{port}"
        wait_for_scratch(restored_endpoint)
        restored = inventory(restored_endpoint, env)
        if restored != before:
            raise BackupFailure("isolated restore differs in object versions or bucket configuration")
        if not object_round_trip(before, restored_endpoint, env, scratch):
            raise BackupFailure("isolated restore could not verify a matching object download")
        with archive.open("rb") as stream:
            archive_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        manifest = {
            "statefulSetUid": storage["statefulSetUid"],
            "pvcUid": storage["pvcUid"],
            "pvName": storage["pvName"],
            "archiveSha256": archive_sha256,
            "archiveBytes": archive.stat().st_size,
            "buckets": len(before),
            "currentObjects": sum(len(bucket["current"]) for bucket in before.values()),
            "verifiedAtUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        (backup / "verified.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (backup / "verified.json").chmod(0o600)
        print(f"MinIO backup and isolated restore passed: {manifest['buckets']} buckets, {manifest['currentObjects']} current objects; original PVC retained.")
        print(f"Private snapshot retained at {archive} ({manifest['archiveBytes']} bytes, mode 600).")
    except Exception as exc:
        error = exc if isinstance(exc, BackupFailure) else BackupFailure("unexpected error while testing the MinIO backup")
    finally:
        if container_started:
            subprocess.run(["docker", "rm", "-f", container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if scaled_down:
            try:
                start_live()
            except BackupFailure:
                error = BackupFailure("MinIO restore failed to restart the original StatefulSet")
        if scratch.exists():
            shutil.rmtree(scratch, ignore_errors=True)
    if error:
        raise error


if __name__ == "__main__":
    try:
        main()
    except BackupFailure as failure:
        print(f"MinIO backup stopped: {failure}", file=sys.stderr)
        raise SystemExit(1) from None
