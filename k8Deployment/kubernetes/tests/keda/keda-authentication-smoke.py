#!/usr/bin/env python3
"""Query all five live KEDA worker metrics without reading Secret values.

The external metrics API calls the existing RabbitMQ/PostgreSQL scalers using
their TriggerAuthentication mappings. No message, database row, Job, credential,
or scale setting is created. Optional --expect-idle also requires zero metrics
and zero worker replicas; omit that option when normal processing is active.
"""
import argparse
import datetime
import decimal
import json
import subprocess
import urllib.parse

CONTEXT = "k3d-clouddsp-local"
NAMESPACE = "clouddsp-app"
WORKERS = {"adtof": 1, "basic-pitch": 2, "demucs": 2}


def read_json(label, *arguments):
    result = subprocess.run(
        ["kubectl", "--context", CONTEXT, "--request-timeout=20s", *arguments],
        capture_output=True, text=True, timeout=25, check=False,
    )
    # Backend errors can contain connection details. Return only the fixed
    # contract label; do not echo command stderr or scaler error messages.
    if result.returncode:
        raise RuntimeError(f"{label} failed")
    return json.loads(result.stdout)


def check_metric(response, metric, scaler, expect_idle):
    items = response.get("items", [])
    if response.get("kind") != "ExternalMetricValueList" or len(items) != 1:
        raise RuntimeError("expected exactly one external metric response")
    item = items[0]
    # KEDA 2.20.2 returns metricLabels: null for these native scalers. The
    # request's mandatory ScaledObject selector chooses the source server-side;
    # validate the returned metric name and any label that is actually present.
    labels = item.get("metricLabels") or {}
    if item.get("metricName") != metric or (labels and labels.get("scaledobject.keda.sh/name") != scaler):
        raise RuntimeError("external metric identity differs from the requested scaler")
    # Kubernetes quantities may represent zero as 0 or 0m. Both forms have
    # the same strict idle expectation, and negative measurements are invalid.
    value = item["value"]
    numeric = decimal.Decimal(value[:-1]) / 1000 if value.endswith("m") else decimal.Decimal(value)
    if not numeric.is_finite() or numeric < 0 or (expect_idle and numeric != 0):
        raise RuntimeError("external metric is invalid or expected idle work is active")
    observed = datetime.datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
    age = (datetime.datetime.now(datetime.timezone.utc) - observed).total_seconds()
    if not -5 <= age <= 120:
        raise RuntimeError("external metric response timestamp is stale")
    return {"scaler": scaler, "metric": metric, "value": value, "timestamp": item["timestamp"]}


def run(expect_idle=False):
    results = []
    for worker, count in WORKERS.items():
        scaler = f"clouddsp-{worker}-rabbitmq-scaler"
        obj = read_json("worker scaler lookup", "-n", NAMESPACE, "get", f"scaledobject/{scaler}", "-o", "json")
        conditions = {c["type"]: c["status"] for c in obj.get("status", {}).get("conditions", [])}
        if conditions.get("Ready") != "True" or conditions.get("Paused") != "False" or conditions.get("Fallback") != "False":
            raise RuntimeError("worker scaler is not Ready, is paused, or has an active fallback")
        if any(t.get("useCachedMetrics") for t in obj["spec"]["triggers"]):
            raise RuntimeError("credential smoke requires uncached backend metrics")
        names = obj.get("status", {}).get("externalMetricNames", [])
        expected = [f"s0-rabbitmq-clouddsp-{worker}-requests"] + (["s1-postgresql"] if count == 2 else [])
        if names != expected:
            raise RuntimeError("worker external metric names differ from the reviewed triggers")
        for metric in names:
            selector = urllib.parse.urlencode({"labelSelector": f"scaledobject.keda.sh/name={scaler}"})
            path = f"/apis/external.metrics.k8s.io/v1beta1/namespaces/{NAMESPACE}/{metric}?{selector}"
            response = read_json("KEDA authenticated backend metric query", "get", "--raw", path)
            results.append(check_metric(response, metric, scaler, expect_idle))
        if expect_idle:
            deployment = read_json("idle worker lookup", "-n", NAMESPACE, "get", f"deployment/clouddsp-{worker}", "-o", "json")
            if deployment["spec"]["replicas"] != 0 or deployment.get("status", {}).get("replicas", 0) != 0:
                raise RuntimeError("expected idle worker has replicas")
    print(json.dumps({"authenticatedMetrics": results, "expectIdle": expect_idle}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-idle", action="store_true")
    try:
        run(parser.parse_args().expect_idle)
    except (RuntimeError, ValueError, KeyError, TypeError, AttributeError, decimal.InvalidOperation, subprocess.TimeoutExpired) as error:
        # Only RuntimeError messages above are ours; other exceptions can
        # contain response fragments and receive a fixed diagnostic instead.
        raise SystemExit(f"KEDA authentication smoke stopped: {error if isinstance(error, RuntimeError) else 'invalid or incomplete metric response'}")
