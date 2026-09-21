import time
from unittest.mock import patch
from agentkit import engine
from agentkit.core import KitError, load_json, write_json, state_path, atomic_write
from test_core import ProjectCase


class GraphWorkflow(ProjectCase):
    def config(self, function):
        cfg = engine.configuration(self.root)
        function(cfg)
        write_json(self.root, engine.CONFIG, cfg)
    def test_parallel_checks_join_before_approval(self):
        state = engine.run(self.root)
        self.assertEqual(state["status"], "waiting")
        for key in ("prepare", "implement", "unit", "review"):
            self.assertEqual(state["nodes"][key]["status"], "succeeded")
        left, right = state["nodes"]["unit"]["history"][0], state["nodes"]["review"]["history"][0]
        self.assertLess(left["started_at"], right["finished_at"])
        self.assertLess(right["started_at"], left["finished_at"])
        engine.approve(self.root, state["id"], "accept", "Confirmed both independent checks.")
        done = engine.run(self.root, resume=True)
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["nodes"]["implement"]["attempts"], 1)
    def test_wrong_run_and_stale_product_cannot_be_approved(self):
        state = engine.run(self.root)
        self.assertCode("STALE_APPROVAL", lambda: engine.approve(self.root, "wrong", "accept", "Reviewed the expected output."))
        p = self.root / "project/app/result.json"
        p.write_text(p.read_text() + " ")
        self.assertCode("STALE_INPUTS", lambda: engine.approve(self.root, state["id"], "accept", "Reviewed the expected output."))
    def test_approval_is_invalid_after_configuration_change(self):
        state = engine.run(self.root)
        engine.approve(self.root, state["id"], "accept", "Reviewed both test outputs.")
        self.config(lambda c: c.update(max_seconds=999))
        self.assertCode("STALE_INPUTS", lambda: engine.run(self.root, resume=True))
    def test_completed_approval_rechecks_its_additional_inputs(self):
        atomic_write(self.root, "acceptance-note.txt", b"approved contract")
        self.config(lambda c: c["nodes"][-1]["inputs"].append("acceptance-note.txt"))
        state = engine.run(self.root)
        engine.approve(self.root, state["id"], "accept", "Reviewed both checks and the acceptance note.")
        self.assertEqual(engine.run(self.root, resume=True)["status"], "completed")
        atomic_write(self.root, "acceptance-note.txt", b"changed contract")
        self.assertCode("STALE_INPUTS", lambda: engine.run(self.root, resume=True))
    def test_cycles_and_dangling_dependencies_fail_before_execution(self):
        original = engine.configuration(self.root)
        self.config(lambda c: c["nodes"][0].update(needs=["accept"]))
        self.assertCode("CYCLE", lambda: engine.run(self.root))
        write_json(self.root, engine.CONFIG, original)
        self.config(lambda c: c["nodes"][0].update(needs=["unknown"]))
        self.assertCode("DANGLING_REFERENCE", lambda: engine.run(self.root))
    def test_undeclared_read_write_dependency_is_rejected(self):
        self.config(lambda c: c["nodes"][2].update(needs=[]))
        self.assertCode("UNORDERED_FILE_ACCESS", lambda: engine.configuration(self.root))
    def test_overlapping_wildcard_outputs_are_rejected(self):
        self.config(lambda c: (c["nodes"][2].update(outputs=["project/build/check*.json"]),
                               c["nodes"][3].update(outputs=["project/build/check-final.json"])))
        self.assertCode("UNORDERED_FILE_ACCESS", lambda: engine.configuration(self.root))
    def minimal(self, nodes, success):
        cfg = {"schema_version": 1, "max_seconds": 20, "parallelism": 2,
               "protected_inputs": ["project/docs/**"], "success_nodes": success, "nodes": nodes}
        write_json(self.root, engine.CONFIG, cfg)
    def n(self, key, code, needs=(), when="all_succeeded", **kwargs):
        return {"id": key, "kind": "command", "needs": list(needs), "when": when, "inputs": [],
                "outputs": [], "retryable": False, "max_attempts": 1, "command": self.command(code), **kwargs}
    def test_failure_branch_runs_and_success_branch_is_skipped(self):
        self.minimal([self.n("first", "raise SystemExit(1)"),
                      self.n("success", "print('success')", ["first"]),
                      self.n("recovery", "print('recovered')", ["first"], "any_failed")], ["recovery"])
        state = engine.run(self.root)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["nodes"]["success"]["status"], "skipped")
        self.assertEqual(state["nodes"]["first"]["status"], "failed")
    def test_retry_count_is_bounded_and_records_failure_then_success(self):
        code = "from pathlib import Path;p=Path('counter.txt');n=int(p.read_text())+1 if p.exists() else 1;p.write_text(str(n));raise SystemExit(0 if n==2 else 1)"
        self.minimal([self.n("retry", code, outputs=["counter.txt"], retryable=True, max_attempts=2)], ["retry"])
        state = engine.run(self.root)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["nodes"]["retry"]["attempts"], 2)
        self.assertFalse(state["nodes"]["retry"]["history"][0]["ok"])
        self.assertTrue(state["nodes"]["retry"]["history"][1]["ok"])
    def test_non_retryable_side_effect_is_not_repeated(self):
        self.minimal([self.n("one", "raise SystemExit(1)")], ["one"])
        state = engine.run(self.root)
        self.assertEqual(state["nodes"]["one"]["attempts"], 1)
        self.assertEqual(state["status"], "failed")
    def test_missing_declared_output_does_not_pass(self):
        self.minimal([self.n("one", "print('claimed success')", outputs=["missing.txt"])], ["one"])
        self.assertEqual(engine.run(self.root)["status"], "failed")
    def test_new_protected_file_during_command_invalidates_node(self):
        self.minimal([self.n("mutate", "from pathlib import Path;Path('project/docs/new.md').write_text('changed rules')")], ["mutate"])
        state = engine.run(self.root)
        self.assertEqual(state["status"], "failed")
        self.assertIn("STALE_INPUTS", state["nodes"]["mutate"]["error"])
    def test_provider_node_needs_opt_in(self):
        self.config(lambda c: c["nodes"][1]["worker"].update(provider="claude"))
        self.assertCode("AGENT_OPT_IN", lambda: engine.run(self.root))
    def test_interrupt_cancels_active_worker_without_waiting_for_its_full_timeout(self):
        self.minimal([self.n("slow", "import time;time.sleep(15)", retryable=True, max_attempts=2)], ["slow"])
        start = time.monotonic()
        with patch("agentkit.engine.wait", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                engine.run(self.root)
        self.assertLess(time.monotonic() - start, 5)
        self.assertEqual(engine.status(self.root)["status"], "interrupted")
        self.assertCode("RETRY_CONFIRMATION", lambda: engine.run(self.root, resume=True))
    def test_changed_completed_output_is_not_silently_reused(self):
        self.minimal([self.n("one", "from pathlib import Path;Path('out.txt').write_text('correct')", outputs=["out.txt"])], ["one"])
        engine.run(self.root)
        atomic_write(self.root, "out.txt", b"changed")
        self.assertCode("STALE_INPUTS", lambda: engine.run(self.root, resume=True))
