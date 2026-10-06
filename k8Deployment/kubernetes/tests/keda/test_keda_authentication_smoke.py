"""Cover observed KEDA response shapes and false success in the live smoke."""
import copy
import datetime
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("keda_authentication_smoke", Path(__file__).with_name("keda-authentication-smoke.py"))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class KedaAuthenticationSmokeTest(unittest.TestCase):
    def setUp(self):
        self.scaler = "clouddsp-adtof-rabbitmq-scaler"
        self.metric = "s0-rabbitmq-clouddsp-adtof-requests"
        self.response = {"kind": "ExternalMetricValueList", "items": [{
            "metricName": self.metric, "metricLabels": None, "value": "0",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }]}

    def test_null_labels_from_live_keda_and_milli_zero_are_accepted(self):
        for value in ["0", "0m"]:
            self.response["items"][0]["value"] = value
            self.assertEqual(value, smoke.check_metric(self.response, self.metric, self.scaler, True)["value"])

    def test_wrong_metric_or_nonempty_wrong_selector_labels_are_rejected(self):
        for field, value in [("metricName", "s1-postgresql"), ("metricLabels", {"scaledobject.keda.sh/name": "wrong-scaler"})]:
            response = copy.deepcopy(self.response)
            response["items"][0][field] = value
            with self.assertRaisesRegex(RuntimeError, "identity differs"):
                smoke.check_metric(response, self.metric, self.scaler, True)

    def test_empty_or_duplicate_measurements_cannot_pass(self):
        for items in [[], self.response["items"] * 2]:
            with self.assertRaisesRegex(RuntimeError, "exactly one"):
                smoke.check_metric({"kind": "ExternalMetricValueList", "items": items}, self.metric, self.scaler, True)

    def test_idle_rejects_nonzero_and_invalid_measurements(self):
        for value in ["1", "500m", "-1", "NaN", "Infinity"]:
            self.response["items"][0]["value"] = value
            with self.assertRaises(RuntimeError):
                smoke.check_metric(self.response, self.metric, self.scaler, True)

    def test_active_mode_accepts_positive_metrics(self):
        self.response["items"][0]["value"] = "1500m"
        self.assertEqual("1500m", smoke.check_metric(self.response, self.metric, self.scaler, False)["value"])

    def test_old_metric_response_cannot_establish_current_credentials(self):
        self.response["items"][0]["timestamp"] = "2020-01-01T00:00:00Z"
        with self.assertRaisesRegex(RuntimeError, "timestamp is stale"):
            smoke.check_metric(self.response, self.metric, self.scaler, True)


if __name__ == "__main__":
    unittest.main()
