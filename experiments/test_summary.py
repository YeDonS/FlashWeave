"""Analysis gates: complete pairing, missingness, and percentile semantics."""
import copy
import unittest
from summarize import extract_metrics, paired_tables, require_matrix, SEEDS, CELLS
from protocol import kernel_fault_lines


class AnalysisTests(unittest.TestCase):
    def test_real_kernel_faults_block_analysis_without_matching_summary_labels(self):
        self.assertEqual(kernel_fault_lines(["hung_task=0 warning_count=0", "drain_timeout=600"]), [])
        self.assertEqual(len(kernel_fault_lines(["[1] BUG: unable to handle page fault", "[2] INFO: task x blocked for more than 120 seconds"])), 2)

    def rows(self):
        return [{"seed": seed, "cell": cell, "read_p99_9_ms": value}
                for seed in SEEDS for cell, value in zip(CELLS, (4, 2, 2, 1))]

    def test_incomplete_matrix_and_duplicates_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "complete formal"):
            require_matrix(self.rows()[:-1])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            require_matrix(self.rows() + [self.rows()[0]])

    def test_pairing_and_interaction_preserve_seed_unit(self):
        paired, summary = paired_tables(self.rows())
        d_a = [row for row in paired if row["metric"] == "read_p99_9_ms" and row["comparison"] == "D/A"]
        self.assertEqual(len(d_a), 5)
        self.assertTrue(all(row["ratio"] == .25 for row in d_a))
        effect = [row for row in summary if row["metric"] == "read_p99_9_ms" and row["comparison"] == "(D/C)/(B/A)"][0]
        self.assertEqual(effect["paired_seed_count"], 5)
        self.assertAlmostEqual(effect["geometric_mean_ratio"], 1)

    def test_missing_percentile_is_NA_not_zero_or_imputed(self):
        rows = self.rows()
        rows[0]["read_p99_9_ms"] = None
        paired, summary = paired_tables(rows)
        row = [row for row in paired if row["metric"] == "read_p99_9_ms" and row["comparison"] == "B/A" and row["seed"] == 101][0]
        self.assertIsNone(row["ratio"])
        row = [row for row in summary if row["metric"] == "read_p99_9_ms" and row["comparison"] == "B/A"][0]
        self.assertEqual(row["paired_seed_count"], 4)

    def test_metrics_use_completion_ns_and_float_percentile_labels(self):
        data = {"jobs": [
            {"jobname": "measure_reads", "read": {"clat_ns": {"mean": 2000000, "percentile": {"99.900000": 7000000}},
                                                   "iops": 10, "runtime": 100, "total_ios": 1}},
            {"jobname": "measure_writes", "write": {"clat_ns": {"mean": 3000000}, "iops": 1}}]}
        row = extract_metrics(data)
        self.assertEqual(row["read_p99_9_ms"], 7)
        self.assertEqual(row["read_mean_ms"], 2)
        self.assertIsNone(row["read_p99_99_ms"])
        bad = copy.deepcopy(data)
        bad["jobs"][0]["read"]["clat_ns"]["mean"] = float("nan")
        with self.assertRaisesRegex(ValueError, "non-finite"):
            extract_metrics(bad)


if __name__ == "__main__":
    unittest.main()

