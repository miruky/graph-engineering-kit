"""Complete model-free demonstrations; every expected failure is actually exercised."""
from pathlib import Path
import json
import shutil
import tempfile

from . import KIND, engine
from .core import KitError, require, write_json, load_json, confined


def demo(repository):
    events = []
    graph = None
    def expected_failure(name, action, code):
        try:
            action()
        except KitError as exc:
            require(exc.code == code, f"Unexpected failure for {name}: {exc.code}")
            events.append({"check": name, "status": "passed", "observed": code})
        else:
            raise KitError("Expected rejection did not occur: " + name, "DEMO_FAILED")
    with tempfile.TemporaryDirectory(prefix="agentkit-demo-") as temp:
        root = Path(temp) / "project with spaces 日本語"
        shutil.copytree(Path(repository) / "examples/project", root)
        if KIND == "context":
            packed = engine.pack(root, "Complete the example while preserving the agreed title.")
            require(engine.verify_packet(root, packed["packet"])["fresh"], "Packet freshness failed")
            events.append({"check": "bounded packet and fresh hashes", "status": "passed", "bytes": packed["bytes"]})
            p = root / "project/docs/requirements.md"
            p.write_text(p.read_text() + "\nA changed requirement.\n")
            expected_failure("changed source invalidates packet", lambda: engine.verify_packet(root, packed["packet"]), "STALE_INPUTS")
        elif KIND == "harness":
            require(not engine.inspect(root)["ok"], "Unreviewed example must not pass")
            events.append({"check": "initial review gate", "status": "passed"})
            red = engine.tdd(root, "red", "CHG-DEMO")
            events.append({"check": "real failing assertions", "status": "passed", "counts": red["counts"]})
            value = load_json(root, "project/app/result.json")
            value["complete"] = True
            write_json(root, "project/app/result.json", value)
            green = engine.tdd(root, "green", "CHG-DEMO")
            events.append({"check": "same tests now pass", "status": "passed", "counts": green["counts"]})
            for key in engine.structure(root)["nodes"]:
                engine.acknowledge(root, key, "CHG-DEMO",
                    "Checked the sample contract: complete is true, title is unchanged, and the same two assertions passed.")
            require(engine.inspect(root)["ok"], "Acknowledged example must be current")
            expected_failure("unregistered action", lambda: engine.run_action(root, "unknown"), "POLICY_DENIED")
            expected_failure("publish action denied", lambda: engine.run_action(root, "push"), "POLICY_DENIED")
            baseline = (root / "project/app/result.json").read_bytes()
            value["title"] = "changed"
            write_json(root, "project/app/result.json", value)
            require(not engine.inspect(root)["ok"], "Changed product must invalidate reviews")
            expected_failure("stale Green rejected", lambda: engine.current_green(root, "CHG-DEMO", engine.structure(root)), "STALE_INPUTS")
            (root / "project/app/result.json").write_bytes(baseline)
            graph = engine.inspect(root)["graph"]
        elif KIND == "loop":
            state = engine.run(root)
            require(state["status"] == "completed" and len(state["attempts"]) == 2, "Two-pass repair loop failed")
            require(not state["attempts"][0]["verification"]["ok"] and state["attempts"][1]["verification"]["ok"],
                    "No failure-to-success evidence")
            events.append({"check": "worker, failed verification, repair, successful verification", "status": "passed", "attempts": 2})
            require(engine.run(root, resume=True)["id"] == state["id"], "Completed run should resume without rerunning")
            events.append({"check": "completed resume performs no extra attempt", "status": "passed"})
            value = load_json(root, "project/app/result.json")
            value["complete"] = False
            write_json(root, "project/app/result.json", value)
            expected_failure("changed completed result", lambda: engine.run(root, resume=True), "STALE_INPUTS")
            cfg = engine.configuration(root)
            cfg["worker"]["command"] = {"argv": ["{python}", "-c", "print('no observable progress')"]}
            write_json(root, engine.CONFIG, cfg)
            stalled = engine.run(root, new=True)
            require(stalled["status"] == "blocked" and stalled["reason"] == "repeated_failure", "Stagnation did not stop")
            events.append({"check": "repeated failure stops", "status": "passed", "attempts": len(stalled["attempts"])})
        else:
            state = engine.run(root)
            require(state["status"] == "waiting" and state["nodes"]["accept"]["status"] == "waiting", "Approval gate did not pause")
            require(state["nodes"]["unit"]["status"] == state["nodes"]["review"]["status"] == "succeeded", "Joined checks did not pass")
            events.append({"check": "dependencies, parallel checks and approval pause", "status": "passed"})
            expected_failure("approval for wrong run", lambda: engine.approve(root, "wrong", "accept", "Checked independent results"), "STALE_APPROVAL")
            engine.approve(root, state["id"], "accept", "Sample operator verified both independent stage results.")
            finished = engine.run(root, resume=True)
            require(finished["status"] == "completed", "Approved workflow did not finish")
            events.append({"check": "input-bound approval and resume", "status": "passed"})
            graph = engine.graph(root)
        if graph:
            from .viewer import export
            exported = export(repository, graph, f"{KIND.title()} Engineering Demo")
        else:
            exported = None
    result = {"ok": True, "kit": KIND, "mode": "deterministic local example, no model/API call", "checks": events,
              "workbench": exported, "temporary_project_removed": not root.exists()}
    write_json(repository, f".agentkit/output/demo/{KIND}-report.json", result)
    return result
