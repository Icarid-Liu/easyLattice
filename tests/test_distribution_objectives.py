import unittest

from app.distribution_objectives import (
    MIN_STDDEV_OBJECTIVE,
    distribution_objective_key,
    minimum_stddev_target,
    select_distribution_objectives,
)


def candidate(index, sigma, bits, security):
    return {
        "ring": {"family_id": "power2", "n": 512},
        "modulus": {"q": 257},
        "distribution": {
            "secret": {
                "name": f"Xs{index}",
                "family": "sparse_ternary",
                "stddev": sigma,
                "sampling_bits": bits,
            },
            "error": {
                "name": f"Xe{index}",
                "family": "sparse_ternary",
                "stddev": sigma,
                "sampling_bits": bits,
            },
        },
        "security": {"bits": security},
    }


class DistributionObjectiveTests(unittest.TestCase):
    def test_minimum_stddev_uses_binary_boundary_for_monotone_rows(self):
        rows = [candidate(index, sigma, bits, security) for index, (sigma, bits, security) in enumerate(
            ((0.2, 10, 110), (0.4, 8, 119), (0.6, 12, 128), (0.8, 4, 140))
        )]
        selected, metadata = minimum_stddev_target(
            rows,
            meets_target=lambda row: row["security"]["bits"] >= 128,
        )
        self.assertIs(selected, rows[2])
        self.assertEqual(metadata["method"], "binary_search_monotone_stddev")
        self.assertEqual(metadata["boundary_index"], 2)

    def test_nonmonotone_rows_fall_back_to_exact_scan(self):
        rows = [candidate(index, sigma, bits, security) for index, (sigma, bits, security) in enumerate(
            ((0.2, 10, 128), (0.4, 8, 110), (0.6, 12, 140))
        )]
        selected, metadata = minimum_stddev_target(
            rows,
            meets_target=lambda row: row["security"]["bits"] >= 128,
        )
        self.assertIs(selected, rows[0])
        self.assertEqual(metadata["method"], "linear_fallback_nonmonotone")

    def test_sampling_candidate_is_restricted_to_width_threshold(self):
        rows = [candidate(index, sigma, bits, security) for index, (sigma, bits, security) in enumerate(
            ((0.2, 2, 110), (0.4, 10, 128), (0.6, 4, 140), (0.8, 8, 150))
        )]
        primary, secondary, metadata = select_distribution_objectives(
            rows,
            meets_target=lambda row: row["security"]["bits"] >= 128,
        )
        self.assertIs(primary, rows[2])
        self.assertIs(secondary, rows[1])
        self.assertAlmostEqual(metadata["stddev_threshold"], 0.4 * 2**0.5)
        self.assertLessEqual(
            distribution_objective_key(primary)[0],
            distribution_objective_key(rows[3])[0],
        )


if __name__ == "__main__":
    unittest.main()
