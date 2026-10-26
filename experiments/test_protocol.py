"""Portable static/synthetic tests. No fio, module, sysfs writes or device I/O."""
import contextlib
import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import fio_coordinator as coordinator
import protocol
import provenance

REPO = Path(__file__).resolve().parents[1]


class IdentityTests(unittest.TestCase):
    def test_dynamic_head_and_dirty_preview_only(self):
        head = "a" * 40
        def clean(_repo, *args):
            return head if args == ("rev-parse", "HEAD") else ""
        with patch.object(provenance, "git", side_effect=clean):
            result = provenance.freeze_source(REPO)
            self.assertEqual(result["source_commit"], head)
            self.assertTrue(result["allow_execute"])
        def dirty(_repo, *args):
            return head if args == ("rev-parse", "HEAD") else "ssd_die.c"
        with patch.object(provenance, "git", side_effect=dirty):
            with self.assertRaises(RuntimeError): provenance.freeze_source(REPO)
            result = provenance.freeze_source(REPO, True)
            self.assertEqual(result["base_commit"], head)
            self.assertIsNone(result["source_commit"])
            self.assertFalse(result["allow_execute"])

    def test_machine_arguments_and_no_mac_lease(self):
        def args(c="0,1", h="2-3", a="0x40000000"):
            return SimpleNamespace(cpus=c, host_cpus=h, memmap_start=a)
        self.assertEqual(protocol.machine_arguments(args(), "0-7")["memmap_start_bytes"], 1024**3)
        for a in [args(c="0,0"), args(c="0;1"), args(h="1-3"), args(h="2-9"),
                  args(h="3-2"), args(h="2,2"), args(a="256G;id"), args(a="0")]:
            with self.subTest(a=a), self.assertRaises(SystemExit):
                protocol.machine_arguments(a, "0-7")
        with patch.object(protocol.platform, "system", return_value="Darwin"), patch.object(protocol, "execution_lease") as lease:
            with self.assertRaises(SystemExit): protocol.run(SimpleNamespace(execute=True))
            lease.assert_not_called()

    def test_build_manifest_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            ws = Path(temporary); (ws / "protocol_inputs.json").write_text("{}")
            module = ws / "example.ko"; module.write_bytes(b"synthetic-not-a-module")
            info = {"build_flags": "flags", "source_commit": "b" * 40}
            record = {**info, "protocol_inputs_sha256": protocol.digest(ws / "protocol_inputs.json"),
                      "kernel_release": "synthetic-kernel", "modules": {"A": {"sha256": protocol.digest(module)}}}
            (ws / "protocol_build.json").write_text(json.dumps(record))
            with patch.object(protocol.platform, "release", return_value="synthetic-kernel"):
                protocol.verify_build(ws, info, "A", module)
                module.write_bytes(b"changed")
                with self.assertRaises(SystemExit): protocol.verify_build(ws, info, "A", module)

    def test_executor_change_rejected(self):
        with self.assertRaises(SystemExit):
            protocol.verify_executors({"executor_hashes": {"protocol.py": "wrong"}})

    def test_large_artifact_hash_uses_streaming_reads(self):
        payload = b"synthetic trace line\n" * 120000
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "trace.txt"
            artifact.write_bytes(payload)
            with patch.object(Path, "read_bytes", side_effect=AssertionError("whole-file read forbidden")):
                self.assertEqual(protocol.digest(artifact), hashlib.sha256(payload).hexdigest())
                self.assertEqual(provenance.digest(artifact), hashlib.sha256(payload).hexdigest())

    def test_trace_stop_wait_and_atomic_finalization(self):
        self.assertGreaterEqual(protocol.TRACE_STOP_WAIT_SECONDS, 300)
        self.assertGreaterEqual(protocol.TRACE_STOP_CLIENT_TIMEOUT_SECONDS,
                                protocol.TRACE_STOP_WAIT_SECONDS + 10)
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary); trace = run / "trace"; trace.mkdir()
            (trace / "ready.json").write_text('{"status":"ready"}')
            (trace / "trace.txt").write_text("partial stream while daemon hashes\n")
            self.assertFalse(protocol.trace_finalization(run)["finalized"])
            result = trace / "result.json"
            result.write_text('{"status":"captured","incomplete":false}')
            self.assertFalse(protocol.trace_finalization(run)["finalized"])
            result.write_text(json.dumps({"status": "capture_failed", "incomplete": True,
                "finished_ns": 123, "problems": ["cap reached"]}))
            observed = protocol.trace_finalization(run)
            self.assertTrue(observed["finalized"])
            self.assertEqual(observed["result_status"], "capture_failed")
            self.assertTrue(observed["incomplete"])
            result.write_text(json.dumps({"status": "captured", "incomplete": False,
                "finished_ns": 124, "problems": []}))
            self.assertEqual(protocol.trace_finalization(run)["result_status"], "captured")

    @unittest.skipUnless(os.name == "posix", "fcntl lease is POSIX-only")
    def test_nonblocking_global_lease_without_run_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / "local-test.lock"
            with patch.object(protocol, "Path", return_value=lock):
                with protocol.execution_lease():
                    with self.assertRaises(SystemExit):
                        with protocol.execution_lease(): pass
                # The previous failure released only its own descriptor.
                with protocol.execution_lease(): pass


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="flashweave_protocol_")
        cls.root = Path(cls.temporary.name)
        cls.workspace = cls.root / "workspace"
        args = SimpleNamespace(repo=str(REPO), workspace=str(cls.workspace),
                               device_name="nvme99n1", allow_dirty_preview=True)
        with contextlib.redirect_stdout(io.StringIO()): protocol.prepare(args)
        config = cls.root / "host_config.sh"
        config.write_text("FW_WORKSPACE=" + str(cls.workspace) + "\n")
        cls.batch = cls.root / "preview"
        result = subprocess.run(["bash", str(REPO / "experiments/run_matrix.sh"), "preview",
                                 str(config), str(cls.batch)], cwd=REPO, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()

    def test_twenty_previews_and_explicit_streams(self):
        with (REPO / "experiments/run_matrix.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 20)
        self.assertEqual(len({(row["seed"], row["cell"]) for row in rows}), 20)
        for row in rows:
            case = self.batch / ("seed_%s_pos_%s_%s" % (row["seed"], row["position"], row["cell"]))
            manifest = json.loads((case / "manifest.json").read_text())
            self.assertFalse(manifest["execute"])
            self.assertEqual(manifest["status"], "static_preview_ok")
            self.assertEqual(manifest["trace_mode"], "off")
            plan = json.loads(next(case.rglob("*.stages/plan.json")).read_text())
            self.assertEqual(len(plan["stages"]), 82)
            jobs = [job for stage in plan["stages"] for job in stage["jobs"]]
            self.assertEqual(len(jobs), 83)
            self.assertEqual([job["randseed"] for job in jobs], list(range(int(row["seed"]), int(row["seed"]) + 83)))
            self.assertEqual(plan["stages"][-1]["concurrent_jobs"], ["measure_reads", "measure_writes"])
            self.assertEqual(plan["stages"][-2]["concurrent_jobs"], ["enter_test_phase"])
            for stage in plan["stages"]:
                content = (Path(stage["directory"]) / "job.fio").read_text()
                self.assertIsNone(re.search(r"(?m)^\s*exec_", content))

    def test_preview_cannot_certify_or_execute(self):
        first = next(self.batch.glob("seed_*"))
        with self.assertRaises(SystemExit): protocol.check(SimpleNamespace(run=str(first)))
        args = SimpleNamespace(workspace=str(self.workspace), out=str(self.root / "forbidden"),
            cell="A", seed=1, profile="formal", trace_mode="off", execute=True,
            memmap_start="1G", cpus="0,1", host_cpus="2")
        # Direct inner call proves the dirty-preview gate precedes any device checks.
        with self.assertRaises(SystemExit): protocol.run_locked(args)
        self.assertFalse((self.root / "forbidden").exists())

    def test_staged_helpers_and_kernel_trace_include(self):
        module = self.workspace / "nvmevirt_DA"
        self.assertIn("ccflags-y += -I$(src)", (module / "Makefile").read_text())
        self.assertIn("ccflags-y += $(NVMEV_EXTRA_CFLAGS)", (module / "Makefile").read_text())
        for helper in ["protocol_hook.py", "fio_coordinator.py", "provenance.py"]:
            self.assertTrue((self.workspace / "evaluation" / helper).is_file())
        if (REPO / "flashweave_trace.h").is_file():
            self.assertEqual((module / "flashweave_trace.h").read_bytes(), (REPO / "flashweave_trace.h").read_bytes())

    def synthetic_scenario(self, mode):
        with tempfile.TemporaryDirectory(prefix="flashweave_mock_") as temporary:
            root = Path(temporary)
            source = next(self.batch.rglob("fio_mixed_*.fio")).read_text()
            global_options, jobs = coordinator.parse_job(next(self.batch.rglob("fio_mixed_*.fio")))
            old_log = jobs[-2]["options"]["write_lat_log"][:-5]
            job = root / "input.fio"; job.write_text(source.replace(old_log, str(root / "logs")))
            output = root / "combined.json"
            fake = root / "fake-fio"; fake.write_text("not executable; all subprocess calls are mocked")
            for name in ["protocol_hook.py", "trace_capture.py"]: (root / name).write_text("# mocked\n")
            calls = []; baseline_calls = []
            def fake_run(cmd, stdout=None, stderr=None, **kwargs):
                if cmd[0] == str(fake):
                    options, generated = coordinator.parse_job(cmd[1]); index = len(calls)
                    calls.append([j["name"] for j in generated])
                    if mode == "fio_failure" and index == 2: return subprocess.CompletedProcess(cmd, 7)
                    results = []
                    for generated_job in generated:
                        opts = {**options, **generated_job["options"]}
                        direction = "read" if "read" in opts["rw"] else "write"
                        count = coordinator.expected_bytes(opts["io_size"])
                        if mode == "short_io" and index == 0: count = 0
                        result = {"jobname": generated_job["name"], "error": 0, "job options": opts}
                        for d in ["read", "write"]:
                            n = count // 4096 if d == direction else 0
                            result[d] = {"io_bytes": count if d == direction else 0,
                                "total_ios": n, "clat_ns": {"N": n, "bins": {"1000": n} if n else {}}}
                        results.append(result)
                    target = Path(next(arg.split("=", 1)[1] for arg in cmd if arg.startswith("--output=")))
                    target.write_text(json.dumps({"fio version": "fio-3.16", "jobs": results}))
                    return subprocess.CompletedProcess(cmd, 0)
                if Path(cmd[1]).name == "trace_capture.py":
                    self.assertEqual(cmd[2], "start")
                    return subprocess.CompletedProcess(cmd, 11)
                baseline_calls.append(cmd)
                if mode == "baseline_failure": return subprocess.CompletedProcess(cmd, 9)
                target = Path(cmd[3]); target.mkdir()
                (target / "snapshot.json").write_text(json.dumps({"event": "baseline", "status": "ok"}))
                return subprocess.CompletedProcess(cmd, 0)
            def fake_control(control):
                if mode == "control_failure": raise RuntimeError("synthetic control failure")
                return {**control, "before": "0", "after": str(control["value"])}
            env = {"FW_PROTOCOL_FIO": str(fake), "FW_PROTOCOL_VARIANT": "die_latency1_norp_sb",
                   "FW_TRACE_MODE": "diag" if mode == "trace_failure" else "off",
                   "FW_PROTOCOL_CASE_DIR": str(root)}
            with patch.object(coordinator, "__file__", str(root / "fio_coordinator.py")), \
                 patch.object(coordinator.platform, "system", return_value="Linux"), \
                 patch.object(coordinator.os, "geteuid", return_value=0), \
                 patch.object(coordinator.subprocess, "run", side_effect=fake_run), \
                 patch.object(coordinator, "apply_control", side_effect=fake_control), \
                 patch.dict(os.environ, env), \
                 patch.object(sys, "argv", ["coordinator", str(job), "--output", str(output)]), \
                 contextlib.redirect_stdout(io.StringIO()):
                if mode == "success": coordinator.main()
                else:
                    with self.assertRaises(RuntimeError): coordinator.main()
            status = json.loads((root / "combined.stages/status.json").read_text())
            if mode == "success":
                self.assertEqual(len(calls), 82)
                self.assertEqual(calls[-1], ["measure_reads", "measure_writes"])
                self.assertEqual(len(baseline_calls), 1)
                rows = json.loads(output.read_text())["jobs"]
                self.assertEqual(len(rows), 83)
                spec = importlib.util.spec_from_file_location("volume_validator", self.workspace / "evaluation/fio_validate_latency_mixed_json.py")
                validator = importlib.util.module_from_spec(spec); spec.loader.exec_module(validator)
                self.assertEqual(validator.actual_bytes(rows), {"init_write": 8*1024**3,
                    "init_prewarm": 40*1024**3, "measure_read": 5*1024**3, "measure_write": 512*1024**2})
            else:
                self.assertEqual(status["status"], "failed")
                self.assertNotIn(["measure_reads", "measure_writes"], calls)
                self.assertFalse(output.exists())

    def test_synthetic_success_and_fail_closed(self):
        for mode in ["success", "fio_failure", "baseline_failure", "control_failure", "short_io", "trace_failure"]:
            with self.subTest(mode=mode): self.synthetic_scenario(mode)

    def test_hook_whitelist(self):
        for command in ["echo 1 > /tmp/test_phase", "echo 1 > " + coordinator.PHASE + "; true"]:
            with self.assertRaises(RuntimeError): coordinator.parse_hook(command)
        with patch.object(coordinator, "Path") as path:
            value = path.return_value; value.is_file.return_value = True
            value.read_text.side_effect = ["7", "8"]
            self.assertEqual(coordinator.apply_control({"path": coordinator.HEAT, "value": 1,
                "semantics": "increment"})["after"], "8")


if __name__ == "__main__": unittest.main()
