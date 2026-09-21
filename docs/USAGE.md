# Graph workflow

## Execute the example and inspect the pause

```sh
python3 kit.py inspect
python3 kit.py run
python3 kit.py status
```

The run performs preparation, work, two parallel checks, and then waits for operator acceptance. `run` returns status `waiting` and exit code 3 at that point; this is intentional, not a completed run.

Copy the actual run ID from the output, review the two check results, then:

```sh
python3 kit.py approve accept --run-id <actual-run-id> --reason "Reviewed the functional result and independent contract check."
python3 kit.py resume
python3 kit.py view --serve
```

An approval is bound to that run, node, configuration, upstream receipts and current declared inputs. Changing the product, configuration or checked outputs invalidates it. Keep approval capability outside a worker's allowlist. An editable local record is an operator gate, not proof of human identity or a security boundary against its own owner.

## Workflow model

`.agentkit/graph.json` defines `protected_inputs`, a total execution-time budget, parallelism, required `success_nodes`, and nodes. Each node declares `id`, `kind`, `needs`, `when`, `inputs`, `outputs`, `retryable` and `max_attempts`.

- `command`: a trusted argv command.
- `agent`: a worker plus independent verifier. A successful provider call alone is not a successful node.
- `approval`: an explicit operator decision; it cannot contain a command or output declaration.

Readiness conditions are `all_succeeded`, `any_failed`, and `always`. Dependencies must first reach terminal states. Ineligible nodes become `skipped`. Completion means every configured `success_node` succeeded; a deliberately handled failure may remain visible in another branch. Choose final success nodes whose dependencies actually express your acceptance contract.

This implementation supports a **DAG with bounded per-node retries**. Arbitrary graph cycles and dynamic topology changes are rejected. A finite retry is not permission to replay an irreversible side effect: `retryable` defaults to false in examples. Conflicting declared file access between unordered stages is rejected conservatively; provide an explicit dependency or independent output locations.

## Provider nodes

```sh
python3 kit.py configure-agent claude --node implement
python3 kit.py run --allow-agent --new
```

Use a real goal, declared outputs, fixed verifier and reviewed protected files. Independent check stages may use any project language or test runner. Retry feedback is bounded and taken from the previous verifier result.

## Recovery

State and attempt histories persist under `.agentkit/state/graph`. Resuming a completed/waiting run checks current configuration and completed-node input/output hashes. Interrupted running stages require both a retryable declaration and `resume --retry-interrupted`, after inspecting possible side effects. Work already marked succeeded is not repeated when its inputs and outputs remain current. A new workflow definition needs `run --new`.

The workbench is an offline/read-only snapshot backed by Cytoscape.js and accessible text tables. It does not approve a stage or execute a command. Regenerate it to reflect changed state.
