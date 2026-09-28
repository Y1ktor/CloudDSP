"""Guard the fresh sample mirror before it can write a shared public bucket."""

import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[2] / "services" / "minio" / "midi_sample_mirror.py"
SPEC = importlib.util.spec_from_file_location("midi_sample_mirror", SOURCE)
assert SPEC and SPEC.loader
mirror = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mirror)


class FreshMirrorBoundaryTest(unittest.TestCase):
    def test_committed_lock_supplies_all_sources_without_frontend_dependencies(self):
        lock = json.loads(mirror.LOCK_PATH.read_text())
        with patch.object(mirror, "catalog", side_effect=AssertionError("node package needed")):
            sources = mirror.sources_from_lock(lock)
        self.assertEqual(461, len(sources))
        self.assertEqual(
            "https://smpldsnds.github.io/drum-machines/808-mini/kick.m4a",
            sources["drums/kick.m4a"],
        )
        lock["assets"]["drums/kick.m4a"]["source"] = "https://unreviewed.example/kick.m4a"
        with self.assertRaisesRegex(RuntimeError, "metadata is invalid"):
            mirror.sources_from_lock(lock)

    def response(self, names=None, objects=None, policy_present=False, policy_error=False):
        calls = []

        def fake_aws(_env, _service, operation, *args):
            calls.append(operation)
            if operation == "list-buckets":
                names_to_return = names if names is not None else ["clouddsp-uploads", mirror.BUCKET]
                return json.dumps({"Buckets": [{"Name": name} for name in names_to_return]})
            if operation == "head-bucket":
                return ""
            if operation == "list-objects-v2":
                return json.dumps({"Contents": objects or []})
            if operation == "get-bucket-policy":
                if policy_error:
                    raise subprocess.CalledProcessError(1, ["aws"], stderr="AccessDenied")
                if policy_present:
                    return json.dumps({"Policy": "{}"})
                raise subprocess.CalledProcessError(1, ["aws"], stderr="NoSuchBucketPolicy")
            self.fail(f"unexpected S3 operation: {operation}")

        return fake_aws, calls

    def test_empty_private_sample_bucket_is_accepted(self):
        fake_aws, calls = self.response()
        with patch.object(mirror, "aws", side_effect=fake_aws):
            mirror.require_fresh_bucket({})
        self.assertEqual(
            ["list-buckets", "head-bucket", "list-objects-v2", "get-bucket-policy"], calls
        )

    def test_partial_or_unexpected_bucket_inventory_is_rejected(self):
        for names in ([mirror.BUCKET], ["clouddsp-uploads", mirror.BUCKET, "other"]):
            fake_aws, calls = self.response(names=names)
            with patch.object(mirror, "aws", side_effect=fake_aws):
                with self.assertRaisesRegex(RuntimeError, "exactly the two"):
                    mirror.require_fresh_bucket({})
            self.assertEqual(["list-buckets"], calls)

    def test_existing_object_or_policy_is_rejected(self):
        for options, message in (
            ({"objects": [{"Key": "drums/kick.m4a"}]}, "empty sample bucket"),
            ({"policy_present": True}, "pre-existing sample policy"),
            ({"policy_error": True}, "could not verify"),
        ):
            fake_aws, _calls = self.response(**options)
            with patch.object(mirror, "aws", side_effect=fake_aws):
                with self.assertRaisesRegex(RuntimeError, message):
                    mirror.require_fresh_bucket({})

    def test_public_grant_requires_exact_uploaded_inventory(self):
        expected = {"drums/kick.m4a": 5}
        for contents, truncated, succeeds in (
            ([{"Key": "drums/kick.m4a", "Size": 5}], False, True),
            ([{"Key": "drums/kick.m4a", "Size": 5}, {"Key": "extra", "Size": 1}], False, False),
            ([{"Key": "drums/kick.m4a", "Size": 5}], True, False),
            ([{"Key": "drums/kick.m4a", "Size": 4}], False, False),
        ):
            listing = json.dumps({"Contents": contents, "IsTruncated": truncated})
            with patch.object(mirror, "aws", return_value=listing):
                if succeeds:
                    mirror.verify_uploaded_inventory({}, expected)
                else:
                    with self.assertRaisesRegex(RuntimeError, "exact reviewed sample catalog"):
                        mirror.verify_uploaded_inventory({}, expected)


if __name__ == "__main__":
    unittest.main()
