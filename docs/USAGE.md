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
- `agent`: a worker plus independent verifier. A successful provider call alone is not a successful node. The worker must create its declared outputs; the verifier may inspect but must not change those outputs. Put build/package generation in a command node, or use a temporary location for verifier byproducts.
- `approval`: an explicit operator decision; it cannot contain a command or output declaration.

Readiness conditions are `all_succeeded`, `any_failed`, and `always`. Dependencies must first reach terminal states. Ineligible nodes become `skipped`. Completion means every configured `success_node` succeeded; a deliberately handled failure may remain visible in another branch. Choose final success nodes whose dependencies actually express your acceptance contract.

Within one node, `inputs` are read-only dependencies; put paths that the stage may modify in `outputs` instead. A downstream stage with a completed execution record may update an earlier stage's file when its output declaration and dependency order permit it. This includes recorded partial outputs from an ordinary failed command. A failed launch or incomplete attempt does not silently become a writer. Saved historical snapshots remain unchanged; freshness checks follow recorded successor versions to the current workspace. An external edit after the last declared writer is still rejected. Approval records retain the specific version reviewed, including when a later approved stage writes a new version. Interrupted partial writes require the documented inspection/retry option before a retryable writer can continue.

This implementation supports a **DAG with bounded per-node retries**. Arbitrary graph cycles and dynamic topology changes are rejected. A finite retry is not permission to replay an irreversible side effect: `retryable` defaults to false in examples. Conflicting declared file access between unordered stages is rejected conservatively; provide an explicit dependency or independent output locations.

## Provider nodes

```sh
python3 kit.py configure-agent claude --node implement
python3 kit.py run --allow-agent --new
```

Use a real goal, declared outputs, fixed verifier and reviewed protected files. Independent check stages may use any project language or test runner. Retry feedback is bounded and taken from the previous verifier result.

## Recovery

State and attempt histories persist under `.agentkit/state/graph`. Resuming a completed/waiting run checks current configuration and terminal-node input/output hashes, including failed nodes whose results selected a recovery branch. Retained JUnit reports are re-read and checked against their recorded hashes, test identities and outcomes. Approval performs the same checks. Missing or changed evidence is rejected. Interrupted running stages require both a retryable declaration and `resume --retry-interrupted`, after inspecting possible side effects. Work already marked succeeded is not repeated when its inputs and outputs remain current. A new workflow definition needs `run --new`.

A completed failed attempt remains recorded while its next retry is pending. Those saved input/output versions and any JUnit evidence are rechecked too. Stopping between completed attempts can resume from that checked boundary; stopping during an unfinished attempt keeps the explicit inspection/retry requirement, including if another interruption happens before the retry starts.

The workbench is an offline/read-only snapshot backed by Cytoscape.js and accessible text tables. It does not approve a stage or execute a command. Regenerate it to reflect changed state.
