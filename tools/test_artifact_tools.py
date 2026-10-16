#!/usr/bin/env python3
"""Regression checks for evidence validation and split-checkout assembly."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from prepare_workspace import prepare
from reproduce_results import ROOT, completion_ms, control_activity, validate_fio, verify_manifest
import fio_summarize_latency_mixed as fio_parser


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = fio_parser.load_fio_json(next((ROOT / "results/raw/fio").glob("fio_mixed_*.json")))

    def test_dry_run_manifest_cannot_count_as_a_run(self):
        with self.assertRaisesRegex(ValueError, "No fio jobs"):
            validate_fio({"dry_run": True, "expected_read_bytes": 5368709120})

    def test_zero_exit_does_not_hide_truncated_io(self):
        data = copy.deepcopy(self.data)
        job = next(j for j in data["jobs"] if j["jobname"] == "measure_reads")
        job["read"]["io_bytes"] //= 2
        job["error"] = 0
        with self.assertRaisesRegex(ValueError, "Incomplete measure_read"):
            validate_fio(data)

    def test_nonzero_job_error_invalidates_run(self):
        data = copy.deepcopy(self.data)
        data["jobs"][0]["error"] = 5
        with self.assertRaisesRegex(ValueError, "job error"):
            validate_fio(data)

    def test_missing_percentile_is_not_zero(self):
        self.assertIsNone(completion_ms({"clat_ns": {}}, "99.000000"))

    def test_feature_flag_does_not_prove_execution(self):
        self.assertEqual(control_activity({"compile_latency_v2_enabled": 1}), "unknown_missing_counters")
        self.assertEqual(control_activity({"read_priority_yields": 0, "read_priority_delayed_requeues": 0, "read_priority_forced_progress_runs": 0}), "no_control_activity_recorded")

    def test_input_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "results/raw/fio/sample.json"
            raw.parent.mkdir(parents=True)
            raw.write_text("changed")
            (root / "results/manifest.json").write_text(json.dumps({"files": [{"path": "results/raw/fio/sample.json", "bytes": 7, "sha256": "0" * 64}]}))
            with self.assertRaisesRegex(ValueError, "checksum"):
                verify_manifest(root)

    def test_path_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "results").mkdir()
            (root / "results/manifest.json").write_text(json.dumps({"files": [{"path": "../secret", "bytes": 0, "sha256": "0" * 64}]}))
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                verify_manifest(root)

    def test_workspace_has_module_flags_and_workload_source_paths(self):
        original_makefile = (ROOT / "nvmevirt_DA/Makefile").read_bytes()
        original_ssd = (ROOT / "ssd.c").read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "staged"
            prepare(ROOT, workspace)
            module, evaluation = workspace / "nvmevirt_DA", workspace / "evaluation"
            self.assertIn("ccflags-y += $(NVMEV_EXTRA_CFLAGS)", (module / "Makefile").read_text())
            for name in ("pci.c", "dma.c", "nvme.h", "build_die.sh", "conv_ftl_latency3_qlc_all_norp_superblock.c", "pqueue/pqueue.c"):
                self.assertTrue((module / name).is_file(), name)
            for name in ("commonvariables.sh", "fio_fragment_die_latency_mixed.sh", "sqlite/sqlite_append_die_affinity_singlefile_pageflow_fileparallel.c", "sqlite/sqlite_append_die_affinity_tablefile_pageflow_fileparallel.c", "filebench/fileserver_append_hotcold.f"):
                self.assertTrue((evaluation / name).is_file(), name)
            self.assertEqual((module / "ssd_die.c").read_bytes(), (ROOT / "ssd_die.c").read_bytes())
            with self.assertRaisesRegex(ValueError, "already exists"):
                prepare(ROOT, workspace)
        self.assertEqual((ROOT / "nvmevirt_DA/Makefile").read_bytes(), original_makefile)
        self.assertEqual((ROOT / "ssd.c").read_bytes(), original_ssd)

    def test_workspace_cannot_recursively_copy_into_source(self):
        with self.assertRaisesRegex(ValueError, "inside a copied source"):
            prepare(ROOT, ROOT / "nvmevirt_DA/never-created-test-output")


if __name__ == "__main__":
    unittest.main()
