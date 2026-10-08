#!/usr/bin/env python3
"""Remove the exact disposable identity from a failed score smoke run.

Use the suffix of its Kubernetes Pod name, never a real account name. This
utility reads the local administrator Secret in memory and prints no token,
credential or account data. It does not remove score rows or objects.
"""

import argparse
import base64
import json
import re
import subprocess
from urllib.parse import urlencode
from urllib.request import Request, urlopen

parser = argparse.ArgumentParser()
parser.add_argument("--pod-suffix", required=True)
args = parser.parse_args()
if not re.fullmatch(r"[a-z0-9]{5}", args.pod_suffix):
    parser.error("Expected the five-character disposable smoke Pod suffix.")

result = subprocess.run(["kubectl", "--context", "k3d-clouddsp-local", "-n", "clouddsp-data",
                         "get", "secret", "clouddsp-keycloak-bootstrap-admin", "-o", "json"],
                        capture_output=True, check=True)
data = json.loads(result.stdout)["data"]
creds = {key: base64.b64decode(value).decode() for key, value in data.items()}
origin = "http://keycloak.localhost:8080"
form = urlencode({"grant_type": "password", "client_id": "admin-cli",
                  "username": creds["KC_BOOTSTRAP_ADMIN_USERNAME"],
                  "password": creds["KC_BOOTSTRAP_ADMIN_PASSWORD"]}).encode()
with urlopen(Request(origin + "/realms/master/protocol/openid-connect/token", data=form,
                     headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=10) as response:
    token = json.load(response)["access_token"]
headers = {"Authorization": "Bearer " + token}
realm = origin + "/admin/realms/clouddsp"
for resource, field, name in (
    ("users", "username", "score-omr-smoke-" + args.pod_suffix),
    ("clients", "clientId", "score-omr-smoke-client-" + args.pod_suffix),
):
    query = urlencode({field: name, "exact": "true"})
    with urlopen(Request(f"{realm}/{resource}?{query}", headers=headers), timeout=10) as response:
        matches = [row for row in json.load(response) if row.get(field) == name]
    if len(matches) > 1:
        raise RuntimeError("Ambiguous disposable identity; no deletion performed.")
    for row in matches:
        with urlopen(Request(f"{realm}/{resource}/{row['id']}", headers=headers, method="DELETE"), timeout=10):
            pass
print("Exact disposable score-smoke identities removed.")
