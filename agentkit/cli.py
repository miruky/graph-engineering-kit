"""The stable command-line interface; all project paths are explicit and scoped."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from . import KIND
from . import engine
from .core import (KitError, require, decode_json, load_json, write_json, confined, unlock,
                   ProjectLock, digest, snapshot, check_fingerprint, VERSION)
from .integrations import provider_command, hooks_configuration, new_project, install_runtime, install_git_hooks

REPOSITORY = Path(__file__).resolve().parent.parent


def parser():
    p = argparse.ArgumentParser(description=f"{KIND.title()} Engineering Kit — project-language independent")
    p.add_argument("--root", default=".", help="Project root (default: current directory)")
    p.add_argument("--version", action="version", version=VERSION)
    subs = p.add_subparsers(dest="action", required=True)
    def command(name, help):
        c = subs.add_parser(name, help=help)
        c.add_argument("--root", default=argparse.SUPPRESS)
        return c
    command("doctor", "Check runtime and optional agent availability without calling a model")
    command("check", "Run this kit's own regression tests")
    command("demo", "Run the complete included workflow in a temporary project")
    c = command("new", "Create a complete working starter in an empty directory")
    c.add_argument("directory")
    c = command("install", "Plan a non-overwriting toolkit installation into an existing project")
    c.add_argument("--target", required=True)
    c.add_argument("--apply", action="store_true")
    c = command("agent-command", "Print a reviewed provider argv preset; does not execute it")
    c.add_argument("provider", choices=["claude", "codex"])
    c = command("unlock", "Remove a dead local writer lock using its exact token")
    c.add_argument("--token", required=True)
    if KIND == "context":
        c = command("pack", "Build a deterministic, byte-bounded context packet")
        group = c.add_mutually_exclusive_group(required=True)
        group.add_argument("--task")
        group.add_argument("--task-file")
        c.add_argument("--profile", default="default")
        c = command("verify", "Verify packet text, configuration and source freshness")
        c.add_argument("packet")
        command("inspect", "Validate the context configuration and selected sources")
    elif KIND == "harness":
        command("inspect", "Check specification structure and review freshness")
        command("impact", "Show the changed document/artifact inputs")
        c = command("review", "Acknowledge one document after inspecting its changes")
        c.add_argument("id")
        c.add_argument("--change", required=True)
        c.add_argument("--reason", required=True)
        c = command("retire", "Record removal of a document whose references are already resolved")
        c.add_argument("id")
        c.add_argument("--reason", required=True)
        c = command("tdd", "Record strict Red or Green JUnit evidence")
        c.add_argument("phase", choices=["red", "green"])
        c.add_argument("--change", required=True)
        c = command("run", "Run one registered, permitted action")
        c.add_argument("name")
        c = command("hook", "Read one provider hook event from stdin")
        c.add_argument("--event", required=True, choices=["SessionStart", "PreToolUse", "Stop"])
        c = command("integration", "Print local hook JSON for review; never enables or trusts hooks")
        c.add_argument("provider", choices=["codex", "claude"])
        command("install-git-hooks", "Install new project-local Git hooks without replacing existing hooks")
        c = command("git-gate", "Internal pre-commit/pre-push gate")
        c.add_argument("--event", required=True, choices=["pre-commit", "pre-push"])
    else:
        for name in ("run", "resume"):
            c = command(name, "Start a bounded run" if name == "run" else "Resume a persisted run after input checks")
            c.add_argument("--allow-agent", action="store_true")
            if name == "run":
                c.add_argument("--new", action="store_true")
            else:
                c.add_argument("--retry-interrupted", action="store_true")
        command("status", "Read current execution state")
        command("inspect", "Validate controller configuration")
        c = command("configure-agent", "Write a provider preset into project configuration; does not run it")
        c.add_argument("provider", choices=["codex", "claude"])
        if KIND == "graph":
            c.add_argument("--node", required=True)
            c = command("approve", "Record operator approval for current inputs and a specific run")
            c.add_argument("node")
            c.add_argument("--run-id", required=True)
            c.add_argument("--reason", required=True)
    if KIND in ("harness", "graph"):
        c = command("view", "Export an offline, read-only Cytoscape workbench")
        c.add_argument("--serve", action="store_true")
        c.add_argument("--port", type=int, default=0)
    return p


def git_gate(root, event):
    from .runtime import verify
    def git(*args):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8")
        require(result.returncode == 0, "Git check failed: " + " ".join(args), "GIT_GATE")
        return result.stdout.strip()
    cfg = engine.configuration(root)
    if event == "pre-push":
        require(not cfg["policy"]["forbid_git_push"], "git push is forbidden by this project's execution policy", "POLICY_DENIED")
        head = git("rev-parse", "HEAD")
        lines = sys.stdin.read(1024 * 1024).splitlines()
        require(lines, "Push reference inventory is required", "GIT_GATE")
        require(all(len(line.split()) == 4 and line.split()[1] == head for line in lines),
                "Only the verified HEAD may be pushed; deletion and other refs are refused", "GIT_GATE")
    with ProjectLock(root, "harness"):
        git("diff", "--quiet")
        require(not git("ls-files", "--others", "--exclude-standard"), "Untracked project files remain", "GIT_GATE")
        tree = git("write-tree")
        report = engine.inspect(root)
        require(report["ok"], "Specification reviews are not current", "STALE_REVIEW")
        before = engine._code_snapshot(root, report)
        result = verify(root, cfg["verifier"])
        require(result["ok"], "Project verification failed", "VERIFICATION_FAILED")
        check_fingerprint(before, engine._code_snapshot(root, engine.structure(root)), "verified artifacts")
        check_fingerprint(tree, git("write-tree"), "Git index during verification")
        git("diff", "--quiet")
    return {"ok": True, "event": event, "verified_index": tree}


def main(argv=None):
    args = parser().parse_args(argv)
    root = Path(args.root).resolve()
    try:
        action = args.action
        if action == "doctor":
            result = {"ok": sys.version_info >= (3, 10), "kit": KIND, "version": VERSION,
                      "python": ".".join(map(str, sys.version_info[:3])), "target_language": "unrestricted",
                      "dependencies_to_install": [], "optional_agents": {n: bool(shutil.which(n)) for n in ("codex", "claude")},
                      "note": "Installed agent availability does not establish authentication or live model access."}
        elif action == "check":
            require((REPOSITORY / "tests").is_dir(), "Run kit regression tests from the standalone checkout")
            import unittest
            suite = unittest.TestLoader().discover(str(REPOSITORY / "tests"))
            require(suite.countTestCases() > 0, "No regression tests discovered", "ZERO_TESTS")
            return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
        elif action == "demo":
            from .sample import demo
            result = demo(REPOSITORY)
        elif action == "new":
            result = new_project(REPOSITORY, args.directory)
        elif action == "install":
            result = install_runtime(REPOSITORY, args.target, KIND, args.apply)
        elif action == "agent-command":
            result = provider_command(args.provider)
        elif action == "unlock":
            result = unlock(root, KIND, args.token)
        elif action == "integration":
            result = hooks_configuration(root, REPOSITORY / "kit.py", args.provider)
        elif action == "install-git-hooks":
            result = install_git_hooks(root, REPOSITORY / "kit.py")
        elif action == "git-gate":
            result = git_gate(root, args.event)
        elif action == "hook":
            try:
                raw = sys.stdin.buffer.read(1024 * 1024 + 1)
                require(len(raw) <= 1024 * 1024, "Hook payload too large")
                payload = decode_json(raw)
                require(isinstance(payload, dict), "Hook event must be an object")
            except KitError:
                if args.event == "PreToolUse":
                    payload = {}
                else:
                    raise
            result = engine.hook(root, args.event, payload)
        elif action == "view":
            from .viewer import export, serve
            graph = engine.inspect(root)["graph"] if KIND == "harness" else engine.graph(root)
            result = export(root, graph, f"{KIND.title()} Engineering Workbench")
            if args.serve:
                require(0 <= args.port <= 65535, "Invalid port")
                server = serve(root, result["directory"], args.port)
                print(json.dumps({**result, "url": f"http://127.0.0.1:{server.server_port}/"}), flush=True)
                try:
                    server.serve_forever()
                finally:
                    server.server_close()
                return 0
        elif KIND == "context":
            if action == "pack":
                if args.task_file:
                    from .core import text
                    task = text(root, args.task_file)
                else:
                    task = args.task
                result = engine.pack(root, task, args.profile)
            elif action == "verify":
                result = engine.verify_packet(root, args.packet)
            else:
                cfg = engine.configuration(root)
                result = {"ok": True, "profiles": {name: len(engine.candidates(root, p)) for name, p in cfg["profiles"].items()}}
        elif KIND == "harness":
            if action in ("inspect", "impact"):
                report = engine.inspect(root)
                result = {"ok": report["ok"], "documents": len(report["nodes"]), "artifacts": len(report["artifacts"]),
                          "findings": report["findings"], "nodes": list(report["nodes"].values())}
            elif action == "review":
                result = engine.acknowledge(root, args.id, args.change, args.reason)
            elif action == "retire":
                result = engine.retire(root, args.id, args.reason)
            elif action == "tdd":
                result = engine.tdd(root, args.phase, args.change)
            else:
                result = engine.run_action(root, args.name)
        else:
            if action == "configure-agent":
                cfg = engine.configuration(root)
                if KIND == "loop":
                    cfg["worker"] = provider_command(args.provider)
                else:
                    node = next((n for n in cfg["nodes"] if n["id"] == args.node), None)
                    require(node and node["kind"] == "agent", "Select an existing agent node")
                    node["worker"] = {**provider_command(args.provider), "goal": node["worker"]["goal"]}
                with ProjectLock(root, KIND):
                    write_json(root, engine.CONFIG, cfg)
                result = {"ok": True, "provider": args.provider, "executed": False,
                          "next": "Review the provider command, project scope and verification criteria, then run with --allow-agent."}
            elif action in ("run", "resume"):
                result = engine.run(root, resume=action == "resume", new=getattr(args, "new", False),
                    retry_interrupted=getattr(args, "retry_interrupted", False), allow_agent=args.allow_agent)
            elif action == "status":
                result = engine.status(root)
            elif action == "approve":
                result = engine.approve(root, args.run_id, args.node, args.reason)
            else:
                engine.configuration(root)
                result = {"ok": True, "configuration_valid": True, "executed": False}
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        if args.action in ("hook", "status", "integration", "agent-command"):
            return 0
        if "ok" in result:
            return 0 if result["ok"] else 3
        return 0 if result.get("status") == "completed" else 3
    except KitError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "code": "INTERRUPTED", "error": "Operation interrupted"}), file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "code": "INVALID_STATE", "error": type(exc).__name__ +
                          ": inspect configuration and local state; no success is assumed"}), file=sys.stderr)
        return 2
