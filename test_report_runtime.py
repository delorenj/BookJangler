import unittest

from report_runtime import runtime_evidence


class RuntimeEvidenceTests(unittest.TestCase):
    def test_local_request_totals_reconcile(self):
        data = runtime_evidence()
        tts = data["tts"]
        self.assertEqual(tts["totals"]["requests"], 307)
        self.assertEqual(tts["totals"]["successful_calls"], 307)
        self.assertEqual(tts["totals"]["failed_calls"], 0)
        self.assertEqual(tts["totals"]["input_text_tokens"], 9906)
        self.assertEqual(tts["by_phase"]["design"]["input_text_tokens"], 432)
        self.assertEqual(tts["by_phase"]["chapter"]["input_text_tokens"], 9474)

    def test_local_tokens_not_mislabeled_llm_usage(self):
        data = runtime_evidence()
        for row in data["ledger"]:
            self.assertIsNone(row["total_tokens"])
            self.assertIsNone(row["input_tokens"])
            self.assertIsNone(row["output_tokens"])
        synthesis = [row for row in data["ledger"] if row["source"] == "Local TTS receipt"]
        self.assertEqual(sum(row["tts_text_tokens"] for row in synthesis), 9906)
        self.assertEqual(len({row["id"] for row in synthesis}), 307)

    def test_offline_asr_and_local_audio_provenance(self):
        data = runtime_evidence()
        self.assertEqual(len(data["qa"]), 2)
        self.assertTrue(all(row["model"]["offline"] for row in data["qa"]))
        self.assertTrue(all(row["model"]["device"] == "cpu" for row in data["qa"]))
        self.assertEqual(sum(row["summary"]["transcribed"] for row in data["qa"]), 35)
        self.assertAlmostEqual(data["tts"]["assembly"]["mastered_wav"]["duration_seconds"], 3020.08)


if __name__ == "__main__":
    unittest.main()
