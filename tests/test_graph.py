import time
from unittest.mock import patch
from agentkit import engine
from agentkit.core import KitError, load_json, write_json, state_path, atomic_write
from test_core import ProjectCase


class GraphWorkflow(ProjectCase):
    def pending_retry_fixture(self, *, with_reader):
        atomic_write(self.root, "decision.txt", b"bad")
        code = ("from pathlib import Path\n"
                "p=Path('attempt.txt');n=int(p.read_text())+1 if p.exists() else 1;p.write_text(str(n))\n"
                "Path('decision.txt').write_text('partial' if n==1 else 'fixed')\n"
                "raise SystemExit(1 if n==1 else 0)\n")
        first = self.n("check", "raise SystemExit(1)", inputs=["decision.txt"])
        repair = self.n("repair", code, ["check"] if with_reader else [], "any_failed" if with_reader else "all_succeeded",
                        outputs=["decision.txt", "attempt.txt"], retryable=True, max_attempts=2)
        self.minimal(([first] if with_reader else []) + [repair], ["repair"])
        original_save = engine._save
        def stop_between_attempts(root, state):
            original_save(root, state)
            record = state["nodes"]["repair"]
            if state["status"] == "running" and record["status"] == "pending" and record["history"]:
                raise KeyboardInterrupt
        with patch("agentkit.engine._save", side_effect=stop_between_attempts):
            with self.assertRaises(KeyboardInterrupt):
                engine.run(self.root)
        saved = engine.status(self.root)
        self.assertEqual(saved["nodes"]["repair"]["status"], "pending")
        self.assertEqual(saved["nodes"]["repair"]["attempts"], 1)
    def test_completed_failed_attempt_pending_retry_is_a_recorded_file_version(self):
        self.pending_retry_fixture(with_reader=True)
        done = engine.run(self.root, resume=True)
        self.assertEqual(done["status"], "completed")
        self.assertEqual((self.root / "attempt.txt").read_text(), "2")
        self.assertEqual(engine.run(self.root, resume=True), done)
    def test_external_pending_retry_output_edit_is_rejected_before_another_attempt(self):
        self.pending_retry_fixture(with_reader=False)
        atomic_write(self.root, "decision.txt", b"external")
        self.assertCode("STALE_INPUTS", lambda: engine.run(self.root, resume=True, retry_interrupted=True))
        self.assertEqual((self.root / "attempt.txt").read_text(), "1")
    def test_recorded_partial_write_can_be_handled_without_erasing_failure_history(self):
        atomic_write(self.root, "decision.txt", b"bad")
        first = self.n("check", "raise SystemExit(1)", inputs=["decision.txt"])
        partial = self.n("partial", "from pathlib import Path;Path('decision.txt').write_text('partial');raise SystemExit(1)", ["check"], "any_failed", outputs=["decision.txt"])
        handled = self.n("handled", "print('partial result retained for inspection')", ["partial"], "any_failed")
        self.minimal([first, partial, handled], ["handled"])
        done = engine.run(self.root)
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["nodes"]["partial"]["status"], "failed")
        self.assertEqual(engine.run(self.root, resume=True), done)
        atomic_write(self.root, "decision.txt", b"external")
        self.assertCode("STALE_INPUTS", lambda: engine.run(self.root, resume=True))
    def test_declared_failed_launch_cannot_authorize_external_input_changes(self):
        atomic_write(self.root, "decision.txt", b"bad")
        first = self.n("check", "raise SystemExit(1)", inputs=["decision.txt"])
        missing = self.n("missing", "", ["check"], "any_failed", outputs=["decision.txt"])
        missing["command"] = {"argv":["agentkit-no-such-program"]}
        handled = self.n("handled", "print('launch failure handled')", ["missing"], "any_failed")
        self.minimal([first, missing, handled], ["handled"])
        done = engine.run(self.root)
        self.assertEqual(done["status"], "completed")
        atomic_write(self.root, "decision.txt", b"external")
        self.assertCode("STALE_INPUTS", lambda: engine.run(self.root, resume=True))
    def test_declared_recovery_write_advances_failed_input_version(self):
        atomic_write(self.root, "decision.txt", b"bad")
        first = self.n("check", "from pathlib import Path;raise SystemExit(0 if Path('decision.txt').read_text()=='good' else 1)", inputs=["decision.txt"])
        repair = self.n("repair", "from pathlib import Path;Path('decision.txt').write_text('good')", ["check"], "any_failed", outputs=["decision.txt"])
        self.minimal([first, repair], ["repair"])
        done = engine.run(self.root)
        self.assertEqual(done["status"], "completed")
        self.assertNotEqual(done["nodes"]["check"]["inputs"], done["nodes"]["repair"]["outputs"])
        self.assertEqual(engine.run(self.root, resume=True), done)
        atomic_write(self.root, "decision.txt", b"external edit")
        self.assertCode("STALE_INPUTS", lambda: engine.run(self.root, resume=True))
    def test_ordered_successor_versions_use_topology_not_config_list_order(self):
        atomic_write(self.root, "decision.txt", b"bad")
        first = self.n("first", "raise SystemExit(1)", inputs=["decision.txt"])
        second = self.n("second", "from pathlib import Path;Path('decision.txt').write_text('second')", ["first"], "any_failed", outputs=["decision.txt"])
        third = self.n("third", "from pathlib import Path;Path('decision.txt').write_text('third')", ["second"], outputs=["decision.txt"])
        self.minimal([third, first, second], ["third"])
        done = engine.run(self.root)
        self.assertEqual(done["status"], "completed")
        self.assertEqual(engine.run(self.root, resume=True), done)
    def test_successful_later_writer_handles_glob_additions_and_deletions(self):
        atomic_write(self.root, "data/old.txt", b"old")
        first = self.n("first", "raise SystemExit(1)", inputs=["data/*.txt"])
        second = self.n("repair", "from pathlib import Path;Path('data/old.txt').unlink();Path('data/new.txt').write_text('new')", ["first"], "any_failed", outputs=["data/*.txt"])
        self.minimal([first, second], ["repair"])
        done = engine.run(self.root)
        self.assertEqual(done["status"], "completed")
        self.assertEqual(engine.run(self.root, resume=True), done)
        atomic_write(self.root, "data/external.txt", b"extra")
        self.assertCode("STALE_INPUTS", lambda: engine.run(self.root, resume=True))
    def test_approval_keeps_reviewed_version_before_declared_downstream_write(self):
        atomic_write(self.root, "decision.txt", b"draft")
        first = self.n("check", "print('reviewable')", inputs=["decision.txt"])
        approval = {"id":"accept", "kind":"approval", "needs":["check"], "when":"all_succeeded", "inputs":["decision.txt"], "outputs":[], "retryable":False, "max_attempts":1}
        apply = self.n("apply", "from pathlib import Path;Path('decision.txt').write_text('applied')", ["accept"], outputs=["decision.txt"])
        self.minimal([first, approval, apply], ["apply"])
        waiting = engine.run(self.root)
        self.assertEqual(waiting["status"], "waiting")
        reviewed = waiting["nodes"]["accept"]["inputs"]
        engine.approve(self.root, waiting["id"], "accept", "Reviewed the draft before applying the change.")
        done = engine.run(self.root, resume=True)
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["nodes"]["accept"]["inputs"], reviewed)
        self.assertEqual(engine.run(self.root, resume=True), done)
        atomic_write(self.root, "decision.txt", b"external")
        self.assertCode("STALE_INPUTS", lambda: engine.run(self.root, resume=True))
    def test_in_place_recovery_interrupt_requires_inspection_and_rejects_unrelated_drift(self):
        atomic_write(self.root, "decision.txt", b"bad")
        atomic_write(self.root, "readonly.txt", b"fixed")
        first = self.n("check", "raise SystemExit(1)", inputs=["decision.txt", "readonly.txt"])
        code = ("from pathlib import Path;import time\n"
                "p=Path('attempt.txt');n=int(p.read_text())+1 if p.exists() else 1;p.write_text(str(n))\n"
                "Path('decision.txt').write_text('partial' if n==1 else 'fixed')\n"
                "Path('ready.txt').write_text('ready')\n"
                "if n==1:time.sleep(15)\n")
        repair = self.n("repair", code, ["check"], "any_failed", outputs=["decision.txt", "attempt.txt", "ready.txt"], retryable=True, max_attempts=2)
        self.minimal([first, repair], ["repair"])
        original_wait = engine.wait
        def interrupt_when_partial(*args, **kwargs):
            if (self.root / "ready.txt").exists():
                raise KeyboardInterrupt
            return original_wait(*args, **kwargs)
        with patch("agentkit.engine.wait", side_effect=interrupt_when_partial):
            with self.assertRaises(KeyboardInterrupt):
                engine.run(self.root)
        self.assertCode("RETRY_CONFIRMATION", lambda: engine.run(self.root, resume=True))
        atomic_write(self.root, "readonly.txt", b"external")
        self.assertCode("STALE_INPUTS", lambda: engine.run(self.root, resume=True, retry_interrupted=True))
        atomic_write(self.root, "readonly.txt", b"fixed")
        original_configuration = engine.configuration
        calls = 0
        def interrupt_before_retry_launch(root):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt
            return original_configuration(root)
        with patch("agentkit.engine.configuration", side_effect=interrupt_before_retry_launch):
            with self.assertRaises(KeyboardInterrupt):
                engine.run(self.root, resume=True, retry_interrupted=True)
        self.assertTrue(engine.status(self.root)["nodes"]["repair"]["interrupted_retry"])
        self.assertCode("RETRY_CONFIRMATION", lambda: engine.run(self.root, resume=True))
        done = engine.run(self.root, resume=True, retry_interrupted=True)
        self.assertEqual(done["status"], "completed")
        self.assertEqual((self.root / "attempt.txt").read_text(), "2")
        self.assertEqual(engine.run(self.root, resume=True), done)
    def test_same_node_readonly_input_and_mutable_output_overlap_is_rejected_early(self):
        self.minimal([self.n("one", "print('never execute')", inputs=["project/app/result.json"], outputs=["project/app/result.json"])], ["one"])
        self.assertCode("READ_WRITE_OVERLAP", lambda: engine.run(self.root))
    def test_waiting_approval_and_completed_resume_require_retained_junit(self):
        state = engine.run(self.root)
        report = self.root / state["nodes"]["implement"]["history"][-1]["result"]["verification"]["junit"]["report_path"]
        original = report.read_bytes()
        report.write_bytes(original + b"\n")
        self.assertCode("STALE_INPUTS", lambda: engine.approve(self.root, state["id"], "accept", "Reviewed the checked result."))
        report.unlink()
        self.assertCode("MISSING_FILE", lambda: engine.approve(self.root, state["id"], "accept", "Reviewed the checked result."))
        report.write_bytes(original)
        engine.approve(self.root, state["id"], "accept", "Reviewed the checked result.")
        finished = engine.run(self.root, resume=True)
        self.assertEqual(finished["status"], "completed")
        report.unlink()
        self.assertCode("MISSING_FILE", lambda: engine.run(self.root, resume=True))
    def test_agent_verifier_cannot_repair_declared_product(self):
        cfg = engine.configuration(self.root)
        agent = cfg["nodes"][1]
        agent["needs"] = []
        agent["inputs"] = ["project/docs/requirements.md"]
        agent["worker"] = {"provider":"command", "command":self.command("print('unchanged')"), "goal":"Complete the result"}
        agent["verifier"] = {"format":"exit", "command":self.command(
            "import json;from pathlib import Path;p=Path('project/app/result.json');"
            "d=json.loads(p.read_text());d['complete']=True;p.write_text(json.dumps(d))")}
        cfg["nodes"], cfg["success_nodes"] = [agent], [agent["id"]]
        write_json(self.root, engine.CONFIG, cfg)
        state = engine.run(self.root)
        self.assertEqual(state["status"], "failed")
        self.assertIn("STALE_INPUTS", state["nodes"][agent["id"]]["error"])
    def test_failed_node_input_drift_invalidates_recovery_approval(self):
        atomic_write(self.root, "decision.txt", b"bad")
        first = self.n("check", "from pathlib import Path;raise SystemExit(0 if Path('decision.txt').read_text()=='good' else 1)", inputs=["decision.txt"])
        recovery = self.n("recovery", "from pathlib import Path;Path('recovery.txt').write_text('recovered')", ["check"], "any_failed", outputs=["recovery.txt"])
        approval = {"id":"accept", "kind":"approval", "needs":["recovery"], "when":"all_succeeded", "inputs":["recovery.txt"], "outputs":[], "retryable":False, "max_attempts":1}
        self.minimal([first, recovery, approval], ["accept"])
        state = engine.run(self.root)
        self.assertEqual(state["status"], "waiting")
        atomic_write(self.root, "decision.txt", b"good")
        self.assertCode("STALE_INPUTS", lambda: engine.approve(self.root, state["id"], "accept", "Reviewed the recovery result."))
    def test_failed_launch_keeps_inputs_for_completed_recovery_validation(self):
        atomic_write(self.root, "decision.txt", b"original")
        first = self.n("missing", "", inputs=["decision.txt"])
        first["command"] = {"argv":["agentkit-no-such-program"]}
        recovery = self.n("recovery", "print('recovered')", ["missing"], "any_failed")
        self.minimal([first, recovery], ["recovery"])
        state = engine.run(self.root)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(engine.run(self.root, resume=True), state)
        atomic_write(self.root, "decision.txt", b"changed")
        self.assertCode("STALE_INPUTS", lambda: engine.run(self.root, resume=True))
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
