"""Durable DAG execution with conditional stages, joins, retries and operator gates."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import PurePosixPath
import time
import uuid
import threading

from .core import (KitError, require, object_fields, strings, number, identifier, load_json,
                   digest, snapshot, confined, write_json, ProjectLock, state_path, now, check_fingerprint)
from .runtime import execute, validate_command, verify, worker_result, feedback

CONFIG = ".agentkit/graph.json"
TERMINAL = {"succeeded", "failed", "skipped"}


def _prefix(pattern):
    return pattern[:min([pattern.find(c) for c in "*?" if c in pattern] or [len(pattern)])].rstrip("/")


def _overlap(a, b):
    dynamic = any(c in a or c in b for c in "*?")
    a, b = _prefix(a), _prefix(b)
    return not a or not b or a == b or a.startswith(b + "/") or b.startswith(a + "/") or (
        dynamic and (a.startswith(b) or b.startswith(a)))


def configuration(root):
    cfg = load_json(root, CONFIG)
    object_fields(cfg, ["schema_version", "max_seconds", "parallelism", "protected_inputs", "success_nodes", "nodes"])
    require(type(cfg["schema_version"]) is int and cfg["schema_version"] == 1, "Unsupported schema version")
    number(cfg["max_seconds"], "max_seconds", .1, 86400)
    number(cfg["parallelism"], "parallelism", 1, 8, integer=True)
    strings(cfg["protected_inputs"], "protected_inputs", allow_empty=False)
    strings(cfg["success_nodes"], "success_nodes", allow_empty=False)
    require(isinstance(cfg["nodes"], list) and 1 <= len(cfg["nodes"]) <= 500, "Expected 1–500 workflow nodes")
    nodes = {}
    for node in cfg["nodes"]:
        object_fields(node, ["id", "kind", "needs", "when", "inputs", "outputs", "retryable", "max_attempts"],
                      ["command", "worker", "verifier", "label"], "workflow node")
        key = identifier(node["id"], "node id")
        require(key not in nodes, "Duplicate workflow node id", "DUPLICATE_ID")
        nodes[key] = node
        require(node["kind"] in ("command", "agent", "approval"), "Unknown node kind")
        require(node["when"] in ("all_succeeded", "any_failed", "always"), "Unknown readiness condition")
        strings(node["needs"], "needs")
        strings(node["inputs"], "inputs")
        strings(node["outputs"], "outputs")
        require(type(node["retryable"]) is bool, "retryable must be boolean")
        number(node["max_attempts"], "node max_attempts", 1, 20, integer=True)
        require(node["max_attempts"] == 1 or node["retryable"], "Multiple attempts require retryable=true")
        require(node["needs"] or node["when"] != "any_failed", "A root node cannot wait for predecessor failure")
        if node["kind"] == "command":
            require("command" in node and "worker" not in node and "verifier" not in node, "Command node shape is invalid")
            validate_command(node["command"])
        elif node["kind"] == "agent":
            require("command" not in node and "worker" in node and "verifier" in node, "Agent node needs a worker and verifier")
            object_fields(node["worker"], ["provider", "command", "goal"])
            require(node["worker"]["provider"] in ("command", "claude", "codex"), "Unknown provider")
            require(isinstance(node["worker"]["goal"], str) and node["worker"]["goal"].strip(), "Agent goal is empty")
            validate_command(node["worker"]["command"])
            object_fields(node["verifier"], ["command", "format"])
            validate_command(node["verifier"]["command"])
            require(node["verifier"]["format"] in ("exit", "junit"), "Invalid verifier format")
        else:
            require(not any(k in node for k in ("worker", "command", "verifier")) and not node["outputs"]
                    and node["max_attempts"] == 1, "Approval nodes cannot execute commands or write outputs")
    for node in nodes.values():
        require(set(node["needs"]) <= nodes.keys(), "Dangling workflow dependency", "DANGLING_REFERENCE")
    remaining, order, ancestors = set(nodes), [], {}
    while remaining:
        ready = sorted(n for n in remaining if not set(nodes[n]["needs"]) & remaining)
        require(ready, "Workflow cycles are not supported; use bounded node retries", "CYCLE")
        for key in ready:
            ancestors[key] = set(nodes[key]["needs"])
            for dep in nodes[key]["needs"]:
                ancestors[key].update(ancestors[dep])
        remaining.difference_update(ready)
        order.extend(ready)
    require(set(cfg["success_nodes"]) <= nodes.keys(), "Unknown success node")
    # Missing dependencies must not accidentally create read/write races between ready stages.
    keys = list(nodes)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if a in ancestors[b] or b in ancestors[a]:
                continue
            na, nb = nodes[a], nodes[b]
            conflict = any(_overlap(x, y) for x in na["outputs"] for y in nb["inputs"] + nb["outputs"])
            conflict |= any(_overlap(x, y) for x in nb["outputs"] for y in na["inputs"])
            require(not conflict, f"Declare an ordering dependency between {a} and {b}: file access overlaps",
                    "UNORDERED_FILE_ACCESS")
    return cfg


def status(root):
    state = load_json(root, state_path("graph"))
    require(state.get("schema_version") == 1 and isinstance(state.get("nodes"), dict), "Invalid graph state", "INVALID_STATE")
    return state


def _save(root, state):
    state["updated_at"] = now()
    write_json(root, state_path("graph"), state)


def _approval_digest(root, cfg, state, node):
    deps = {k: state["nodes"][k].get("receipt") for k in node["needs"]}
    return digest({"run_id": state["id"], "node": node["id"], "configuration": digest(cfg),
                   "dependencies": deps, "inputs": snapshot(root, node["inputs"], required=bool(node["inputs"]))})


def _validate_saved(root, cfg, state):
    check_fingerprint(state.get("configuration_sha256"), digest(cfg), "workflow configuration")
    check_fingerprint(state.get("protected_inputs"), snapshot(root, cfg["protected_inputs"]), "protected workflow inputs")
    require(set(state["nodes"]) == {n["id"] for n in cfg["nodes"]}, "Workflow state inventory differs", "INVALID_STATE")
    for node in cfg["nodes"]:
        record = state["nodes"][node["id"]]
        if record["status"] == "succeeded" and node["kind"] != "approval":
            check_fingerprint(record.get("inputs"), snapshot(root, node["inputs"], required=bool(node["inputs"])), node["id"] + " inputs")
            check_fingerprint(record.get("outputs"), snapshot(root, node["outputs"], required=bool(node["outputs"])), node["id"] + " outputs")


def approve(root, run_id, node_id, reason):
    require(isinstance(reason, str) and len(reason.strip()) >= 12, "A specific approval reason is required")
    with ProjectLock(root, "graph"):
        cfg, state = configuration(root), status(root)
        _validate_saved(root, cfg, state)
        require(state["id"] == run_id, "Approval belongs to a different run", "STALE_APPROVAL")
        node = next((n for n in cfg["nodes"] if n["id"] == node_id), None)
        require(node and node["kind"] == "approval" and state["nodes"][node_id]["status"] == "waiting",
                "This node is not waiting for approval")
        fingerprint = _approval_digest(root, cfg, state, node)
        check_fingerprint(state["nodes"][node_id].get("request_digest"), fingerprint, "approval request")
        state["nodes"][node_id]["approval"] = {"digest": fingerprint, "reason": reason.strip(), "at": now()}
        state["nodes"][node_id]["status"] = "pending"
        _save(root, state)
    return {"ok": True, "approved": node_id, "run_id": run_id, "request_digest": fingerprint}


def _run_node(root, node, protected, deadline, cancel, attempt, previous):
    inputs = snapshot(root, node["inputs"], required=bool(node["inputs"]))
    started = now()
    if node["kind"] == "command":
        result = execute(root, node["command"], remaining=deadline - time.monotonic(), cancel=cancel)
        ok = result["status"] == "passed"
    else:
        result = worker_result(root, node["worker"], {"goal": node["worker"]["goal"], "attempt": attempt,
            "feedback": feedback(previous.get("result", {}).get("verification")) if previous else None,
            "instruction": "Do not change protected inputs, controller state or permissions. "
            "Only declared outputs may change. The independent verifier decides success.",
            "protected_inputs": sorted(protected), "outputs": node["outputs"]}, deadline - time.monotonic(), cancel=cancel)
        if result["status"] == "passed":
            result["verification"] = verify(root, node["verifier"], remaining=deadline - time.monotonic(), cancel=cancel)
        ok = result["status"] == "passed" and result.get("verification", {}).get("ok") is True
    check_fingerprint(inputs, snapshot(root, node["inputs"], required=bool(node["inputs"])), node["id"] + " read-only inputs")
    check_fingerprint(protected, {p: snapshot(root, [p])[p] for p in protected}, "protected workflow inputs")
    outputs = snapshot(root, node["outputs"], required=bool(node["outputs"])) if ok else {}
    return {"ok": ok, "started_at": started, "finished_at": now(), "result": result,
            "inputs": inputs, "outputs": outputs,
            "receipt": digest({"node": node["id"], "inputs": inputs, "outputs": outputs, "ok": ok, "result": result})}


def run(root, *, resume=False, new=False, retry_interrupted=False, allow_agent=False):
    cfg = configuration(root)
    require(allow_agent or all(n["kind"] != "agent" or n["worker"]["provider"] == "command" for n in cfg["nodes"]),
            "Provider nodes require --allow-agent after command/account review", "AGENT_OPT_IN")
    with ProjectLock(root, "graph"):
        if resume:
            state = status(root)
            _validate_saved(root, cfg, state)
            if state["status"] == "completed":
                return state
            require(state["status"] in ("running", "waiting", "interrupted"), "This run cannot resume; start --new")
            for node in cfg["nodes"]:
                record = state["nodes"][node["id"]]
                if record["status"] == "running":
                    require(retry_interrupted and node["retryable"],
                            "Interrupted node may have side effects; inspection and --retry-interrupted are required", "RETRY_CONFIRMATION")
                    record["status"] = "pending" if record["attempts"] < node["max_attempts"] else "failed"
        else:
            path = state_path("graph")
            if confined(root, path).exists():
                require(new, "Workflow run already exists; use resume or --new", "STATE_EXISTS")
                old = status(root)
                write_json(root, state_path("graph", "archive-" + old["id"]), old)
            state = {"schema_version": 1, "id": uuid.uuid4().hex, "configuration_sha256": digest(cfg),
                     "protected_inputs": snapshot(root, cfg["protected_inputs"]), "status": "running",
                     "elapsed_seconds": 0.0, "created_at": now(),
                     "nodes": {n["id"]: {"status": "pending", "attempts": 0, "history": []} for n in cfg["nodes"]}}
        start, used = time.monotonic(), state["elapsed_seconds"]
        deadline = start + max(0, cfg["max_seconds"] - used)
        state["status"] = "running"
        def save():
            state["elapsed_seconds"] = round(used + time.monotonic() - start, 4)
            _save(root, state)
        futures = {}
        cancel = threading.Event()
        pool = ThreadPoolExecutor(max_workers=cfg["parallelism"])
        try:
            while True:
                check_fingerprint(digest(cfg), digest(configuration(root)), "workflow during execution")
                changed = False
                for node in cfg["nodes"]:
                    record = state["nodes"][node["id"]]
                    if record["status"] != "pending":
                        continue
                    deps = [state["nodes"][n]["status"] for n in node["needs"]]
                    if not all(d in TERMINAL for d in deps):
                        continue
                    eligible = (node["when"] == "always" or
                                node["when"] == "all_succeeded" and all(d == "succeeded" for d in deps) or
                                node["when"] == "any_failed" and any(d == "failed" for d in deps))
                    if not eligible:
                        record["status"] = "skipped"
                        changed = True
                        continue
                    if node["kind"] == "approval":
                        request = _approval_digest(root, cfg, state, node)
                        approval = record.get("approval")
                        if approval:
                            check_fingerprint(approval["digest"], request, "operator approval")
                            record["status"], record["receipt"] = "succeeded", request
                        else:
                            record["status"], record["request_digest"] = "waiting", request
                        changed = True
                        continue
                    if len(futures) >= cfg["parallelism"]:
                        continue
                    if time.monotonic() >= deadline:
                        record["status"], record["error"] = "failed", "run_time_limit"
                        changed = True
                        continue
                    record["status"] = "running"
                    record["attempts"] += 1
                    save()  # Persist the at-least-once boundary before launching side effects.
                    previous = record["history"][-1] if record["history"] else None
                    future = pool.submit(_run_node, root, node, state["protected_inputs"], deadline, cancel,
                                         record["attempts"], previous)
                    futures[future] = node
                    changed = True
                if futures:
                    done, _ = wait(futures, timeout=.1, return_when=FIRST_COMPLETED)
                    for future in done:
                        node = futures.pop(future)
                        record = state["nodes"][node["id"]]
                        try:
                            result = future.result()
                            record["history"].append(result)
                            record.update({k: result[k] for k in ("inputs", "outputs", "receipt")})
                            record["status"] = "succeeded" if result["ok"] else (
                                "pending" if node["retryable"] and record["attempts"] < node["max_attempts"] else "failed")
                        except KitError as exc:
                            record["status"], record["error"] = "failed", exc.code + ": " + str(exc)
                        except Exception as exc:
                            record["status"], record["error"] = "failed", "INTERNAL_ERROR: " + type(exc).__name__
                        changed = True
                if changed:
                    save()
                if not futures and not changed:
                    break
            if any(r["status"] == "waiting" for r in state["nodes"].values()):
                state["status"] = "waiting"
            elif all(state["nodes"][n]["status"] == "succeeded" for n in cfg["success_nodes"]):
                state["status"] = "completed"
            else:
                state["status"] = "failed"
            save()
        except KeyboardInterrupt:
            cancel.set()
            state["status"] = "interrupted"
            save()
            raise
        except KitError as exc:
            state["status"], state["error"] = "failed", exc.code + ": " + str(exc)
            save()
            raise
        finally:
            # Every command has a deadline; queued futures are cancelled on interruption.
            cancel.set()
            pool.shutdown(wait=True, cancel_futures=True)
    return state


def graph(root):
    cfg = configuration(root)
    state = status(root) if confined(root, state_path("graph")).exists() else {"nodes": {}}
    return {"nodes": [{"id": n["id"], "title": n.get("label", n["id"]), "kind": n["kind"],
                       "status": state["nodes"].get(n["id"], {}).get("status", "pending"),
                       "attempts": state["nodes"].get(n["id"], {}).get("attempts", 0)} for n in cfg["nodes"]],
            "edges": [{"id": f"{dep}:{n['id']}", "from": dep, "to": n["id"], "kind": n["when"]}
                      for n in cfg["nodes"] for dep in n["needs"]]}
