from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock

from agentkit import KIND
from agentkit.core import (KitError, decode_json, confined, text, atomic_write, load_json, write_json,
    expand, snapshot, ProjectLock, unlock, process_alive, contains_secret, redact, number, digest)
from agentkit.runtime import execute, verify, junit, validate_command, worker_result, _terminate, complete_output
from agentkit.integrations import new_project, install_runtime, provider_command

REPO = Path(__file__).resolve().parents[1]


class ProjectCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="kit-test-")
        self.root = Path(self.tmp.name) / "spaces 日本語"
        shutil.copytree(REPO / "examples/project", self.root)
    def tearDown(self):
        self.tmp.cleanup()
    def command(self, code, **kw):
        return {"argv": ["{python}", "-c", code], **kw}
    def assertCode(self, code, fn):
        with self.assertRaises(KitError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)


class FileContracts(ProjectCase):
    def test_duplicate_json_keys_and_nonfinite_are_rejected(self):
        for value in ('{"a":1,"a":2}', '{"x":NaN}', '{"x":Infinity}'):
            with self.subTest(value=value), self.assertRaises(KitError):
                decode_json(value)
    def test_bool_is_not_an_integer_limit(self):
        with self.assertRaises(KitError):
            number(True, "limit", integer=True)
    def test_path_traversal_and_windows_devices(self):
        for path in ("../secret", "/etc/passwd", "C:/secret", "a\\b", "a/./b", "a//b", "NUL.txt", "x./a"):
            with self.subTest(path=path), self.assertRaises(KitError):
                confined(self.root, path)
    def test_symlink_and_parent_link_are_rejected(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "data.txt").write_text("private")
        try:
            (self.root / "alias").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("This runner cannot create symlinks; exercised on other CI platforms")
        self.assertCode("UNSAFE_PATH", lambda: text(self.root, "alias/data.txt"))
        self.assertCode("UNSAFE_PATH", lambda: atomic_write(self.root, "alias/new", b"bad"))
        self.assertFalse((outside / "new").exists())
    def test_binary_and_oversized_context_reads_fail(self):
        atomic_write(self.root, "binary-content.bin", b"a\x00b")
        with self.assertRaises(KitError):
            text(self.root, "binary-content.bin")
        self.assertCode("SIZE_LIMIT", lambda: text(self.root, "project/docs/policy.md", limit=10))
    def test_atomic_exclusive_write_preserves_existing(self):
        atomic_write(self.root, "new.txt", b"first", exclusive=True)
        self.assertCode("ALREADY_EXISTS", lambda: atomic_write(self.root, "new.txt", b"second", exclusive=True))
        self.assertEqual(text(self.root, "new.txt"), "first")
    def test_oversized_json_cannot_replace_a_readable_record(self):
        write_json(self.root, "state.json", {"status": "running"})
        with patch("agentkit.core.MAX_JSON_BYTES", 64):
            self.assertCode("SIZE_LIMIT", lambda: write_json(self.root, "state.json", {"large": "x" * 100}))
        self.assertEqual(load_json(self.root, "state.json"), {"status": "running"})
    def test_bracket_paths_and_unicode_are_literal(self):
        atomic_write(self.root, "routes/[id]/説明.txt", "hello".encode())
        self.assertEqual(expand(self.root, ["routes/[id]/*.txt"]), ["routes/[id]/説明.txt"])
    def test_glob_zero_or_many_directories(self):
        atomic_write(self.root, "a/root.txt", b"r")
        atomic_write(self.root, "a/nested/deep.txt", b"d")
        self.assertEqual(expand(self.root, ["a/**/*.txt"]), ["a/nested/deep.txt", "a/root.txt"])
    def test_required_pattern_cannot_silently_match_nothing(self):
        self.assertCode("MISSING_FILE", lambda: expand(self.root, ["missing/**/*.txt"]))
        self.assertEqual(expand(self.root, ["missing/**/*.txt"], required=False), [])
    def test_source_inventory_is_content_based(self):
        first = snapshot(self.root, ["project/app/**"])
        p = self.root / "project/app/result.json"
        info = p.stat()
        p.write_bytes(p.read_bytes().replace(b"false", b"true "))
        os.utime(p, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertNotEqual(first, snapshot(self.root, ["project/app/**"]))
    def test_live_lock_cannot_be_stolen(self):
        with ProjectLock(self.root, KIND) as lock:
            self.assertCode("LOCKED", lambda: ProjectLock(self.root, KIND).__enter__())
            self.assertCode("LOCKED", lambda: unlock(self.root, KIND, lock.token))
            self.assertTrue(process_alive(os.getpid()))
        with ProjectLock(self.root, KIND):
            pass
    def test_secret_detector_and_redactor(self):
        token = "sk-" + "x" * 30
        self.assertTrue(contains_secret(token))
        self.assertNotIn(token, redact("before " + token + " after"))
        self.assertNotIn(str(self.root), redact(str(self.root) + "/project/app", self.root))


class ProcessContracts(ProjectCase):
    def test_argument_metacharacters_are_not_shell_commands(self):
        marker = "literal; echo injected"
        result = execute(self.root, {"argv": ["{python}", "-c", "import sys; print(sys.argv[1])", marker]})
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["stdout"].strip(), marker)
    def test_timeout_does_not_pass(self):
        start = time.monotonic()
        result = execute(self.root, self.command("import time; time.sleep(20)", timeout=.1))
        self.assertEqual(result["status"], "timeout")
        self.assertLess(time.monotonic() - start, 10)
    def test_output_limit_does_not_pass(self):
        result = execute(self.root, self.command("print('x'*100000)", max_output_bytes=1024))
        self.assertEqual(result["status"], "output_limit")
        self.assertLessEqual(len(result["stdout"]), 1024)
    def test_output_limit_is_shared_between_stdout_and_stderr(self):
        result = execute(self.root, self.command("import sys;print('x'*900);print('y'*900,file=sys.stderr)", max_output_bytes=1024))
        self.assertEqual(result["status"], "output_limit")
        self.assertLessEqual(len(result["stdout"].encode()) + len(result["stderr"].encode()), 1024)
    def test_large_output_is_retained_sanitized_without_bloating_state(self):
        result = execute(self.root, self.command("print('start-'+'x'*50000+'-end')"))
        self.assertEqual(result["status"], "passed")
        self.assertLess(len(result["stdout"].encode()), 18000)
        self.assertEqual(complete_output(self.root, result), 'start-' + 'x'*50000 + '-end\n')
        atomic_write(self.root, result["stdout_artifact"]["path"], b"replaced")
        self.assertCode("INVALID_EVIDENCE", lambda: complete_output(self.root, result))
    def test_provider_protocol_uses_complete_retained_output(self):
        code = "import json;print(json.dumps(dict(type='result',is_error=False,subtype='success',result='x'*50000)))"
        result = worker_result(self.root, {"provider":"claude","command":self.command(code)}, {}, 10)
        self.assertEqual(result["status"], "passed")
        self.assertIsNotNone(result["stdout_artifact"])
    def test_codex_failure_hidden_from_excerpt_is_still_rejected(self):
        code = ("import json;events=[dict(type='item.completed',text='x'*20000),"
                "dict(type='error',message='middle_failure'),dict(type='item.completed',text='y'*20000),"
                "dict(type='turn.completed')];print('\\n'.join(json.dumps(e) for e in events))")
        result = worker_result(self.root, {"provider":"codex","command":self.command(code)}, {}, 10)
        self.assertEqual(result["status"], "provider_error")
        self.assertNotIn("middle_failure", result["stdout"])
        self.assertIn("middle_failure", complete_output(self.root, result))
    def test_retained_output_does_not_restore_redacted_secrets(self):
        result = execute(self.root, self.command("print('x'*50000+' sk-'+'z'*36)"))
        self.assertIsNotNone(result["stdout_artifact"])
        self.assertNotIn('sk-' + 'z'*36, complete_output(self.root, result))
    @unittest.skipUnless(os.name == "posix", "POSIX process-group contract")
    def test_exited_group_permission_race_is_not_a_false_failure(self):
        process = Mock(pid=123, poll=Mock(return_value=0), wait=Mock(return_value=0))
        with patch("agentkit.runtime.os.killpg", side_effect=PermissionError):
            _terminate(process)
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired("owned-process", .2)
        with patch("agentkit.runtime.os.killpg", side_effect=PermissionError):
            self.assertCode("PROCESS_CONTROL_FAILED", lambda: _terminate(process))
    def test_missing_executable_is_an_error(self):
        self.assertCode("MISSING_EXECUTABLE", lambda: execute(self.root, {"argv": ["agentkit-executable-that-does-not-exist"]}))
    def test_stdin_and_no_accidental_env_leak(self):
        with patch.dict(os.environ, {"AGENTKIT_PRIVATE_TEST": "not-forwarded"}):
            result = execute(self.root, self.command("import sys,os; print(sys.stdin.read()); print(os.getenv('AGENTKIT_PRIVATE_TEST','absent'))"), input_text="specific task")
        self.assertEqual(result["stdout"].splitlines(), ["specific task", "absent"])
    def test_os_identity_variables_needed_by_credential_stores_are_preserved(self):
        with patch.dict(os.environ, {"USER": "kit-test-user", "LOGNAME": "kit-test-user", "USERNAME": "kit-test-user"}):
            result = execute(self.root, self.command("import os;print(all(os.getenv(k)=='kit-test-user' for k in ('USER','LOGNAME','USERNAME')))"))
        self.assertEqual(result["stdout"].strip(), "True")
    def test_cancellation_stops_running_command(self):
        event = threading.Event()
        timer = threading.Timer(.1, event.set)
        timer.start()
        try:
            result = execute(self.root, self.command("import time; time.sleep(20)"), cancel=event)
        finally:
            timer.cancel()
        self.assertEqual(result["status"], "cancelled")
    def test_no_approval_bypass_flags(self):
        for flag in ("--yolo", "--dangerously-skip-permissions", "--dangerously-bypass-hook-trust"):
            self.assertCode("POLICY_DENIED", lambda: validate_command({"argv": ["tool", flag]}))
    def test_provider_exit_zero_is_not_enough(self):
        spec = {"provider": "claude", "command": self.command("print('{\"type\":\"result\",\"is_error\":true,\"subtype\":\"error\"}')")}
        result = worker_result(self.root, spec, {"goal": "test"}, 10)
        self.assertEqual(result["status"], "provider_error")
    def test_provider_success_is_checked_by_wire_contract(self):
        spec = {"provider": "claude", "command": self.command("print('{\"type\":\"result\",\"is_error\":false,\"subtype\":\"success\"}')")}
        self.assertEqual(worker_result(self.root, spec, {"goal": "test"}, 10)["status"], "passed")
        codex = {"provider": "codex", "command": self.command("print('{\"type\":\"turn.completed\",\"usage\":{}}')")}
        self.assertEqual(worker_result(self.root, codex, {}, 10)["status"], "passed")
    def test_presets_never_disable_sandbox_or_permission_checks(self):
        for name in ("codex", "claude"):
            command = provider_command(name)["command"]
            validate_command(command)
            self.assertNotIn("--dangerously-skip-permissions", command["argv"])
        self.assertIn("--restricted", provider_command("claude")["command"]["argv"])


class EvidenceContracts(ProjectCase):
    def test_zero_skipped_error_and_duplicates_are_rejected(self):
        cases = [b'<testsuite tests="0"/>', b'<testsuite><testcase name="a"><skipped/></testcase></testsuite>',
                 b'<testsuite><testcase name="a"><error/></testcase></testsuite>',
                 b'<testsuite><testcase name="a"/><testcase name="a"/></testsuite>']
        for data in cases:
            with self.subTest(data=data), self.assertRaises(KitError):
                junit(data)
    def test_dtd_and_totals_are_rejected(self):
        for data in (b'<!DOCTYPE a [<!ENTITY e "x">]><testsuite/>',
                     b'<testsuite tests="8"><testcase name="one"/></testsuite>',
                     b'<testsuites tests="2"><testsuite tests="1"><testcase name="one"/></testsuite></testsuites>'):
            with self.subTest(data=data), self.assertRaises(KitError):
                junit(data)
    def test_assertion_and_startup_failure_are_distinguished(self):
        assertion = junit(b'<testsuite><testcase name="a"><failure type="AssertionError">wrong value</failure></testcase></testsuite>')
        startup = junit(b'<testsuite><testcase name="a"><failure type="SyntaxError">bad syntax</failure></testcase></testsuite>')
        self.assertEqual(assertion["assertion_failures"], 1)
        self.assertEqual(startup["assertion_failures"], 0)
    def test_successful_exit_without_fresh_junit_fails(self):
        result = verify(self.root, {"format": "junit", "command": self.command("print('done')")})
        self.assertFalse(result["ok"])
        self.assertIn("evidence_error", result)
    def test_real_verifier_fails_then_passes_when_available(self):
        specification = {"format": "junit", "command": {"argv": ["{python}", "project/checks/verify.py", "{result}"]}}
        first = verify(self.root, specification)
        self.assertFalse(first["ok"])
        data = load_json(self.root, "project/app/result.json")
        data["complete"] = True
        write_json(self.root, "project/app/result.json", data)
        second = verify(self.root, specification)
        self.assertTrue(second["ok"])
        self.assertEqual(first["junit"]["test_ids"], second["junit"]["test_ids"])
        self.assertTrue((self.root / second["junit"]["report_path"]).is_file())


class InstallationContracts(ProjectCase):
    def test_existing_destination_is_never_overwritten(self):
        before = (self.root / "project/app/result.json").read_bytes()
        self.assertCode("ALREADY_EXISTS", lambda: new_project(REPO, self.root))
        self.assertEqual((self.root / "project/app/result.json").read_bytes(), before)
    def test_install_plan_does_not_mutate_target(self):
        target = Path(self.tmp.name) / "existing"
        target.mkdir()
        (target / "AGENTS.md").write_text("existing instructions")
        result = install_runtime(REPO, target, KIND, apply=False)
        self.assertFalse(result["applied"])
        self.assertEqual(list(target.iterdir()), [target / "AGENTS.md"])
    def test_install_preserves_existing_instructions_and_config(self):
        target = Path(self.tmp.name) / "existing"
        target.mkdir()
        (target / "AGENTS.md").write_text("existing instructions")
        result = install_runtime(REPO, target, KIND, apply=True)
        self.assertTrue(result["applied"])
        self.assertEqual((target / "AGENTS.md").read_text(), "existing instructions")
        self.assertFalse((target / f".agentkit/{KIND}.json").exists())
        self.assertCode("ALREADY_EXISTS", lambda: install_runtime(REPO, target, KIND, apply=True))
    def test_fresh_starter_has_independent_runtime(self):
        target = Path(self.tmp.name) / "new 日本語 project"
        new_project(REPO, target)
        result = subprocess.run([sys.executable, str(target / "kit.py"), "doctor"], cwd=target, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
