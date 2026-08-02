import unittest

from app.adaptive_search import adaptive_binary_validate, adaptive_validate


class AdaptiveSearchTests(unittest.TestCase):
    def test_binary_validation_finds_first_monotone_target(self):
        candidates = [{"index": index} for index in range(16)]
        calls = []
        result = adaptive_binary_validate(
            candidates,
            estimate=lambda candidate: calls.append(candidate["index"]) or {"bits": candidate["index"]},
            normalize=lambda candidate, raw: (raw, {"ok": True}),
            apply=lambda candidate, raw: candidate.update(raw),
            meets_target=lambda candidate: candidate["bits"] >= 11,
        )
        self.assertTrue(result.target_met)
        self.assertEqual(result.best_candidate["index"], 11)
        self.assertLess(result.attempted, len(candidates))
        self.assertEqual(len(calls), result.attempted)

    def test_binary_validation_falls_back_when_high_endpoint_misses(self):
        candidates = [{"index": index} for index in range(7)]
        result = adaptive_binary_validate(
            candidates,
            estimate=lambda candidate: {"bits": 128 if candidate["index"] == 3 else 90},
            normalize=lambda candidate, raw: (raw, {"ok": True}),
            apply=lambda candidate, raw: candidate.update(raw),
            meets_target=lambda candidate: candidate["bits"] >= 128,
        )
        self.assertTrue(result.target_met)
        self.assertEqual(result.best_candidate["index"], 3)
        self.assertEqual(result.best_candidate["index"], 3)
        self.assertGreaterEqual(result.attempted, 4)

    def test_continues_after_unmet_candidate_and_stops_at_first_target(self):
        candidates = [{"n": 512, "q": q} for q in (257, 769, 3329)]
        outcomes = {257: 90, 769: 127, 3329: 128}

        result = adaptive_validate(
            candidates,
            estimate=lambda candidate: {"bits": outcomes[candidate["q"]]},
            normalize=lambda candidate, raw: (raw, {"ok": True, "bits": raw["bits"]}),
            apply=lambda candidate, raw: candidate.update(raw),
            meets_target=lambda candidate: candidate["bits"] >= 128,
            order_key=lambda candidate: (candidate["n"], candidate["q"]),
        )

        self.assertEqual(result.attempted, 3)
        self.assertEqual(result.successful, 3)
        self.assertTrue(result.target_met)
        self.assertEqual(result.status, "target_met")
        self.assertEqual(result.best_candidate["q"], 3329)

    def test_exhaustion_is_distinguished_from_target_unmet_without_results(self):
        result = adaptive_validate(
            [{"n": 512, "q": 257}],
            estimate=lambda candidate: {"bits": 90},
            normalize=lambda candidate, raw: (raw, {"ok": True, "bits": raw["bits"]}),
            apply=lambda candidate, raw: candidate.update(raw),
            meets_target=lambda candidate: candidate["bits"] >= 128,
        )

        self.assertEqual(result.attempted, 1)
        self.assertTrue(result.exhausted)
        self.assertFalse(result.target_met)
        self.assertEqual(result.status, "no_feasible_candidate")

    def test_cancellation_stops_before_next_candidate(self):
        calls = []
        result = adaptive_validate(
            [{"n": 512, "q": 257}, {"n": 512, "q": 769}],
            estimate=lambda candidate: calls.append(candidate["q"]) or {"bits": 90},
            normalize=lambda candidate, raw: (raw, {"ok": True}),
            apply=lambda candidate, raw: candidate.update(raw),
            meets_target=lambda candidate: False,
            cancel=lambda: bool(calls),
        )

        self.assertEqual(calls, [257])
        self.assertEqual(result.status, "cancelled")
        self.assertFalse(result.exhausted)


if __name__ == "__main__":
    unittest.main()
