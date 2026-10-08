import unittest

from report_costs import api_equivalent, electricity_scenario, request_tier_equivalent


class CostTests(unittest.TestCase):
    def test_snapshot_api_equivalent(self):
        cost = api_equivalent({"input_tokens": 13552862, "output_tokens": 194612, "cache_read_tokens": 3530240, "cache_write_tokens": 0})
        self.assertAlmostEqual(cost["usd"], 29.404868)
        self.assertAlmostEqual(cost["breakdown_usd"]["cache_read_tokens"], 0.353024)

    def test_missing_usage_is_unknown_not_zero(self):
        self.assertIsNone(api_equivalent({"input_tokens": 0, "output_tokens": 0})["usd"])
        self.assertEqual(api_equivalent({field: 0 for field in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")})["usd"], 0)

    def test_long_context_counts_cached_input_once(self):
        long = request_tier_equivalent({"input_tokens": 200000, "cache_read_tokens": 73000, "cache_write_tokens": 0, "output_tokens": 1000})
        self.assertEqual(long["normalized_input_tokens"], 273000)
        self.assertEqual(long["tier"], "long_context")
        self.assertAlmostEqual(long["usd"], 0.8 + 0.0146 + 0.015)
        standard = request_tier_equivalent({"input_tokens": 200000, "cache_read_tokens": 72000, "cache_write_tokens": 0, "output_tokens": 1000})
        self.assertEqual(standard["tier"], "standard")
        self.assertAlmostEqual(standard["usd"], 0.4 + 0.0072 + 0.01)

    def test_energy_is_labeled_unmeasured(self):
        result = electricity_scenario(1460.7673996747471, 150, 350, 0.15)
        self.assertAlmostEqual(result["usd_low"], 0.00912979624796717)
        self.assertAlmostEqual(result["usd_high"], 0.021302857911923396)
        self.assertFalse(result["measured"])

    def test_invalid_costs_refused(self):
        with self.assertRaises(ValueError):
            api_equivalent({"input_tokens": -1})
        with self.assertRaises(ValueError):
            electricity_scenario(10, 350, 150, 0.15)


if __name__ == "__main__":
    unittest.main()
