"""Declared argv execution and evidence parsing. This is not an OS sandbox."""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

from .core import (KitError, require, object_fields, strings, number, confined,
                   digest_bytes, redact, read_bytes, relative_name, canonical, decode_json, atomic_write, file_hash)

BASE_ENV = {"PATH", "HOME", "USER", "LOGNAME", "USERNAME", "USERPROFILE", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR",
            "LANG", "LC_ALL", "TERM", "COMSPEC", "PATHEXT", "APPDATA", "LOCALAPPDATA",
            "XDG_CONFIG_HOME", "CODEX_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR",
            "NODE_EXTRA_CA_CERTS"}
BYPASS = {"--dangerously-bypass-approvals-and-sandbox", "--dangerously-bypass-hook-trust",
          "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions",
          "--ignore-rules", "--yolo"}
OUTPUT_EXCERPT_BYTES = 16 * 1024


def _retained_output(root, value, stream):
    """Keep readable state small while retaining the complete sanitized process text."""
    data = value.encode("utf-8")
    if len(data) <= OUTPUT_EXCERPT_BYTES:
        return value, None
    fingerprint = digest_bytes(data)
    path = f".agentkit/state/outputs/{fingerprint}-{stream}.txt"
    atomic_write(root, path, data)
    half = OUTPUT_EXCERPT_BYTES // 2
    excerpt = (data[:half].decode("utf-8", "ignore") + "\n[Full sanitized output: " + path + "]\n"
               + data[-half:].decode("utf-8", "ignore"))
    return excerpt, {"path": path, "sha256": fingerprint, "bytes": len(data)}


def complete_output(root, result, stream="stdout"):
    """Load full retained text only when a protocol parser needs it."""
    artifact = result.get(stream + "_artifact")
    if artifact is None:
        return result.get(stream, "")
    data = read_bytes(root, artifact["path"], limit=64 * 1024 * 1024)
    require(len(data) == artifact["bytes"] and digest_bytes(data) == artifact["sha256"],
            "Retained process output changed", "INVALID_EVIDENCE")
    return data.decode("utf-8")


def validate_command(command, where="command"):
    object_fields(command, ["argv"], ["timeout", "max_output_bytes", "pass_env", "cwd", "stdin"], where)
    argv = command["argv"]
    require(isinstance(argv, list) and 1 <= len(argv) <= 128, f"{where}: argv must be a nonempty array")
    require(all(isinstance(a, str) and "\x00" not in a and len(a) <= 32768 for a in argv),
            f"{where}: invalid argv element")
    require(bool(argv[0].strip()), f"{where}: empty executable")
    require(not any(a in BYPASS or a.startswith(tuple(x + "=" for x in BYPASS)) for a in argv),
            "Approval/sandbox bypass flags are refused", "POLICY_DENIED")
    number(command.get("timeout", 60), "command timeout", .05, 86400)
    number(command.get("max_output_bytes", 1024 * 1024), "output limit", 128, 16 * 1024 * 1024, integer=True)
    strings(command.get("pass_env", []), "pass_env")
    require(all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) for k in command.get("pass_env", [])),
            "Invalid environment variable name")
    if "cwd" in command:
        relative_name(command["cwd"])
    require(isinstance(command.get("stdin", ""), str), "stdin must be text")
    return command


def argv_for(command, root, values=None):
    validate_command(command)
    substitutions = {"python": sys.executable, "project": str(Path(root).resolve()),
                     "exe_suffix": ".exe" if os.name == "nt" else ""}
    substitutions.update(values or {})
    def replace(value):
        def sub(match):
            key = match[1]
            require(key in substitutions, f"Unknown command placeholder: {key}")
            return str(substitutions[key])
        return re.sub(r"\{([a-z_]+)\}", sub, value)
    return [replace(x) for x in command["argv"]]


def _terminate(process):
    """Stop the process group we own; never use a broad process-name match."""
    if os.name == "posix":
        def signal_group(sig):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            except PermissionError as exc:
                # A rapidly exited/reaped leader may no longer have an accessible group
                # on macOS. Never retry a foreign/inaccessible group by PID or name.
                try:
                    process.wait(timeout=.2)
                except subprocess.TimeoutExpired:
                    # Reap a still-owned direct child where permitted, but do not claim
                    # successful process-tree control when the group signal was refused.
                    with contextlib.suppress(OSError):
                        process.kill()
                    raise KitError("Cannot terminate the owned process group", "PROCESS_CONTROL_FAILED") from exc
        signal_group(signal.SIGTERM)
        try:
            process.wait(timeout=.5)
        except subprocess.TimeoutExpired:
            pass
        signal_group(signal.SIGKILL)
    elif process.poll() is None:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        with contextlib.suppress(OSError):
            process.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=5)


def execute(root, command, *, values=None, input_text=None, remaining=None, extra_env=None, cancel=None):
    root = Path(root).resolve()
    argv = argv_for(command, root, values)
    executable = shutil.which(argv[0])
    if executable is None and Path(argv[0]).is_absolute() and Path(argv[0]).is_file():
        executable = argv[0]
    require(executable is not None, f"Executable not found: {Path(argv[0]).name}", "MISSING_EXECUTABLE")
    argv[0] = executable
    # Windows .cmd/.bat programs implicitly use cmd.exe. Refuse metacharacters rather than
    # pretending shell=False makes an arbitrary batch-file argument safe.
    if os.name == "nt" and Path(executable).suffix.lower() in (".cmd", ".bat"):
        require(not any(re.search(r"[&|<>^%!\r\n]", a) for a in argv),
                "Batch adapter argument contains a shell metacharacter", "POLICY_DENIED")
    cwd = confined(root, command["cwd"], exists=True) if command.get("cwd") else root
    require(cwd.is_dir(), "Command cwd must be a directory")
    duration = command.get("timeout", 60)
    if remaining is not None:
        require(remaining > 0, "Run time budget exhausted", "TIME_LIMIT")
        duration = min(duration, remaining)
    output_limit = command.get("max_output_bytes", 1024 * 1024)
    stdin = command.get("stdin", "") if input_text is None else input_text
    require(isinstance(stdin, str) and len(stdin.encode()) <= 4 * 1024 * 1024, "stdin exceeds limit")
    env = {k: v for k, v in os.environ.items() if k in BASE_ENV or k in command.get("pass_env", [])}
    env.update(extra_env or {})
    env["PYTHONUTF8"] = "1"
    secrets = [v for k, v in env.items() if re.search(r"KEY|TOKEN|PASSWORD|SECRET|AUTH", k, re.I)]
    start = time.monotonic()
    reason = None
    with tempfile.TemporaryDirectory(prefix="agentkit-process-") as tmp:
        folder = Path(tmp)
        (folder / "stdin").write_bytes(stdin.encode())
        process = None
        try:
            with (folder / "stdin").open("rb") as source, (folder / "stdout").open("w+b") as out, \
                    (folder / "stderr").open("w+b") as err:
                try:
                    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=source, stdout=out, stderr=err,
                                               shell=False, start_new_session=os.name == "posix")
                except OSError as exc:
                    raise KitError(f"Cannot start {Path(argv[0]).name}: {type(exc).__name__}", "START_FAILED") from exc
                while process.poll() is None:
                    if cancel is not None and cancel.is_set():
                        reason = "cancelled"
                        break
                    if time.monotonic() - start > duration:
                        reason = "timeout"
                        break
                    if os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > output_limit:
                        reason = "output_limit"
                        break
                    time.sleep(.02)
                if reason:
                    _terminate(process)
                returncode = process.wait()
                if os.name == "posix":
                    _terminate(process)  # Long-lived detached children are outside this command contract.
                out.seek(0)
                err.seek(0)
                stdout, stderr = out.read(output_limit + 1), err.read(output_limit + 1)
                if len(stdout) + len(stderr) > output_limit:
                    reason = reason or "output_limit"
                stdout = stdout[:output_limit]
                stderr = stderr[:max(0, output_limit - len(stdout))]
        finally:
            if process is not None and process.poll() is None:
                _terminate(process)
    safe_stdout, stdout_artifact = _retained_output(root, redact(stdout.decode("utf-8", "replace"), root, secrets), "stdout")
    safe_stderr, stderr_artifact = _retained_output(root, redact(stderr.decode("utf-8", "replace"), root, secrets), "stderr")
    return {"status": reason or ("passed" if returncode == 0 else "failed"),
            "returncode": returncode, "elapsed_seconds": round(time.monotonic() - start, 4),
            "stdout": safe_stdout, "stderr": safe_stderr,
            "stdout_artifact": stdout_artifact, "stderr_artifact": stderr_artifact,
            "stdout_sha256": digest_bytes(stdout), "stderr_sha256": digest_bytes(stderr)}


def junit(data):
    """Strict, language-neutral JUnit evidence. Missing/empty/skipped/error is never a pass."""
    require(isinstance(data, bytes) and len(data) <= 8 * 1024 * 1024, "JUnit report exceeds limit")
    require(not re.search(br"<!\s*(?:DOCTYPE|ENTITY)", data, re.I), "DTD/entities are refused", "INVALID_EVIDENCE")
    try:
        tree = ET.fromstring(data)
    except (ET.ParseError, RecursionError) as exc:
        raise KitError("Malformed JUnit XML", "INVALID_EVIDENCE") from exc
    require(tree.tag in ("testsuite", "testsuites"), "Expected JUnit testsuite(s)", "INVALID_EVIDENCE")
    cases, ids = [], set()
    for case in tree.iter("testcase"):
        name = case.get("name", "").strip()
        identity = (case.get("classname", ""), name)
        require(name and identity not in ids, "Missing/duplicate JUnit test identity", "INVALID_EVIDENCE")
        ids.add(identity)
        flags = [k for k in ("failure", "error", "skipped") if case.find(k) is not None]
        require(len(flags) <= 1, "Conflicting JUnit outcomes", "INVALID_EVIDENCE")
        assertion = False
        if flags == ["failure"]:
            failure = case.find("failure")
            kind = failure.get("type", "")
            description = (failure.get("message", "") + " " + (failure.text or "")).strip()
            assertion = bool(re.search(r"assertion|assertfailed|comparisonfailure", kind, re.I)
                             or not kind and re.search(r"\bassert(?:ion)?\b", description, re.I))
        cases.append({"id": list(identity), "status": flags[0] if flags else "passed", "assertion": assertion})
    require(cases, "JUnit ran zero tests", "INVALID_EVIDENCE")
    counts = {k: sum(c["status"] == k for c in cases) for k in ("passed", "failure", "error", "skipped")}
    for suite in (n for n in tree.iter() if n.tag in ("testsuite", "testsuites")):
        local = list(suite.iter("testcase"))
        for field, expected in (("tests", len(local)),
                                ("failures", sum(c.find("failure") is not None for c in local)),
                                ("errors", sum(c.find("error") is not None for c in local)),
                                ("skipped", sum(c.find("skipped") is not None for c in local))):
            if field in suite.attrib:
                try:
                    actual = int(suite.get(field))
                except (ValueError, TypeError) as exc:
                    raise KitError("Invalid JUnit totals", "INVALID_EVIDENCE") from exc
                require(actual == expected, f"JUnit {field} total disagrees with test cases", "INVALID_EVIDENCE")
    require(not counts["error"] and not counts["skipped"], "JUnit errors/skips are not assertion evidence", "INVALID_EVIDENCE")
    return {"cases": sorted(cases, key=lambda x: x["id"]), "counts": counts,
            "assertion_failures": sum(c["status"] == "failure" and c["assertion"] for c in cases),
            "test_ids": sorted([list(x) for x in ids]), "sha256": digest_bytes(data)}


def verify(root, specification, *, remaining=None, cancel=None):
    object_fields(specification, ["command", "format"], [], "verifier")
    require(specification["format"] in ("exit", "junit"), "Unknown verifier format")
    validate_command(specification["command"], "verifier command")
    if specification["format"] == "exit":
        result = execute(root, specification["command"], remaining=remaining, cancel=cancel)
        return {**result, "ok": result["status"] == "passed", "evidence_format": "exit"}
    # A fresh result path for every invocation prevents accidental reuse of yesterday's XML.
    state = confined(root, ".agentkit/state/results")
    state.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="junit-", dir=state) as folder:
        report = Path(folder) / "results.xml"
        result = execute(root, specification["command"], values={"result": str(report)},
                         extra_env={"AGENTKIT_RESULT": str(report)}, remaining=remaining, cancel=cancel)
        if result["status"] in ("timeout", "output_limit", "cancelled"):
            return {**result, "ok": False, "evidence_format": "junit", "evidence_error": result["status"]}
        try:
            observed = read_bytes(root, report.relative_to(Path(root).resolve()).as_posix())
            original = junit(observed)
            tree = ET.fromstring(observed)
            attributes = {"testsuite": {"name", "tests", "failures", "errors", "skipped", "time"},
                          "testsuites": {"name", "tests", "failures", "errors", "skipped", "time"},
                          "testcase": {"name", "classname", "time"}, "failure": {"type", "message"},
                          "error": {"type", "message"}, "skipped": {"message"}}
            for element in tree.iter():
                for child in list(element):
                    if child.tag in ("properties", "system-out", "system-err"):
                        element.remove(child)
                element.attrib = {k: redact(v, root) for k, v in element.attrib.items()
                                  if k in attributes.get(element.tag, set())}
                if element.text:
                    element.text = redact(element.text, root)
                if element.tail:
                    element.tail = redact(element.tail, root)
            sanitized = ET.tostring(tree, encoding="utf-8", xml_declaration=True)
            parsed = junit(sanitized)
            require(parsed["counts"] == original["counts"] and parsed["assertion_failures"] == original["assertion_failures"],
                    "Sanitization changed evidence meaning", "INVALID_EVIDENCE")
            require((result["returncode"] == 0) == (parsed["counts"]["failure"] == 0),
                    "Command status contradicts JUnit report", "INVALID_EVIDENCE")
            retained = ".agentkit/state/results/report-" + parsed["sha256"] + ".xml"
            if confined(root, retained).exists():
                require(file_hash(root, retained) == parsed["sha256"], "Retained report was changed", "INVALID_EVIDENCE")
            else:
                atomic_write(root, retained, sanitized, exclusive=True)
            parsed.update({"report_path": retained, "observed_sha256": digest_bytes(observed),
                           "identity_paths_normalized": parsed["test_ids"] != original["test_ids"]})
            return {**result, "ok": result["returncode"] == 0, "evidence_format": "junit", "junit": parsed}
        except KitError as exc:
            return {**result, "ok": False, "evidence_format": "junit", "evidence_error": str(exc)}


def worker_result(root, specification, request, remaining, cancel=None):
    result = execute(root, specification["command"], input_text=canonical(request), remaining=remaining, cancel=cancel)
    provider = specification["provider"]
    if result["status"] == "passed" and provider != "command":
        try:
            protocol_output = complete_output(root, result)
            if provider == "claude":
                response = decode_json(protocol_output)
                require(isinstance(response, dict) and response.get("type") == "result"
                        and response.get("is_error") is False and response.get("subtype") == "success",
                        "Claude did not report a successful invocation", "PROVIDER_ERROR")
                result["provider_usage"] = {k: response[k] for k in ("total_cost_usd", "usage", "modelUsage") if k in response}
            else:
                events = [decode_json(line) for line in protocol_output.splitlines() if line.strip()]
                require(events and all(isinstance(x, dict) for x in events)
                        and any(x.get("type") == "turn.completed" for x in events)
                        and not any(x.get("type") in ("turn.failed", "error") for x in events),
                        "Codex did not complete its turn", "PROVIDER_ERROR")
                result["provider_usage"] = [x.get("usage") for x in events if x.get("type") == "turn.completed"]
        except KitError as exc:
            result["status"] = "provider_error"
            result["provider_error"] = str(exc)
    return result


def feedback(result):
    """Bound what is sent back to a worker independently from stored process output."""
    if not result:
        return None
    return {"ok": result.get("ok"), "status": result.get("status"), "returncode": result.get("returncode"),
            "stdout": result.get("stdout", "")[:24000], "stderr": result.get("stderr", "")[:24000],
            "output_excerpt_limit_characters": 24000, "evidence_error": result.get("evidence_error"),
            "counts": result.get("junit", {}).get("counts")}
