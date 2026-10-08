"""Narrow an untrusted MinIO notification to a canonical score input key."""

import json
from urllib.parse import unquote_plus
from uuid import UUID

BUCKET = "clouddsp-uploads"
EXTENSIONS = frozenset({"pdf", "png", "jpg", "jpeg"})


def score_candidates(body: bytes) -> list[tuple[str, str]]:
    if len(body) > 65536:
        return []
    try:
        records = json.loads(body)["Records"]
    except (ValueError, KeyError, TypeError):
        return []
    if not isinstance(records, list) or len(records) > 20:
        return []
    candidates = []
    for record in records:
        try:
            if not record["eventName"].startswith("s3:ObjectCreated:"):
                continue
            if record["s3"]["bucket"]["name"] != BUCKET:
                continue
            raw = record["s3"]["object"]["key"]
            key = unquote_plus(raw, errors="strict")
            parts = key.split("/")
            if len(parts) != 3 or parts[0] != "score-inputs":
                continue
            job_id = parts[1]
            if str(UUID(job_id)) != job_id or parts[2] not in {f"source.{ext}" for ext in EXTENSIONS}:
                continue
            candidates.append((job_id, key))
        except (KeyError, TypeError, ValueError, UnicodeError):
            continue
    return candidates
