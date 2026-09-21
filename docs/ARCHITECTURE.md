# Design and trust boundaries

## The four concerns

| Concern | Contract |
| --- | --- |
| Context | Which instructions, references and observations enter a model interaction |
| Harness | Runtime, tool access, permissions, state and checks around an agent |
| Loop | Feedback, repeated work, verification and bounded termination |
| Graph | Dependencies, branches, coordination, shared execution state and joins |

These concerns overlap. A written `git push` prohibition is information supplied to the model. Enforcing it in an execution gateway, restricted credentials or a repository policy is an execution control. Merely putting a prohibition into an instructions file does not revoke a capability.

## What this implementation trusts

Project configuration and declared executables are reviewed operator inputs. Commands use argv arrays with `shell=False`; configuration is not a language for arbitrary shell interpolation. Trusted scripts can still start other programs or use the network. This runner is **not an OS sandbox** and does not turn same-user editable files into a hostile-agent security boundary.

For an untrusted or unattended worker, use a separate identity, restrictive provider permissions, an isolated worktree/container/VM, and credentials that lack unwanted privileges. Do not grant the worker access to controller policy or approval commands. A local operator approval record expresses an authorized decision; it does not cryptographically prove a human made it.

## Files and state

Paths are project-relative with forward slashes. Traversal, device names, symlinks and Windows reparse points are refused. Globs support `*`, `**`, and `?`; square brackets are literal, including route names such as `routes/[id]/page.html`. Inventories are bounded at 20,000 scanned files; use focused paths/exclusions for large trees. File hashing can trace binary artifacts; context sources must be UTF-8 text.

Mutable state lives under `.agentkit/state/`; derived output under `.agentkit/output/`. Both are ignored by Git. Atomic writes prevent a half-written JSON record. JSON configuration/state records are limited to 8 MiB; an oversized write fails before replacing the previous readable record. An exclusive local writer lock prevents overlapping controllers in one project. This is a single-machine tool, not a distributed scheduler or an NFS locking protocol.

SHA-256 identifies the exact declared content and detects stale evidence. It is not authentication: an account able to rewrite the controller and its records can forge a new record. Hashes cannot decide whether prose is correct or a test is sufficient.

## Process limits and privacy

Commands have a deadline and an output-byte ceiling. POSIX runs use a process group and terminate remaining children on completion or cancellation. Windows timeout/cancellation uses `taskkill /T` for the active process tree; commands that detach children or leave daemons behind are outside the supported command contract. Use a job/OS supervisor when detached workloads are needed.

Only a small operating-system environment allowlist and explicitly named `pass_env` entries reach a child. Known secret-shaped strings and configured credential values are redacted from captured output. Detection is heuristic, not a guarantee that arbitrary sensitive content will be found. Review data before sending a context packet or task to a provider. Local traces are not intended for public commits.

Large sanitized stdout/stderr is retained under `.agentkit/state/outputs/` with a content hash. State records contain at most 16 KiB of text from each stream plus a reference to the full sanitized file. Provider protocol checks read and hash-check the full retained output, so a truncated excerpt cannot hide a failed event. This keeps large command logs from making an otherwise valid run record unreadable. Large metadata inventories can still reach the explicit JSON record limit and stop the operation.

The framework does not download a model, create an account, upload a repository, change global Git settings, or weaken provider approval/sandbox controls. Provider calls occur only when requested. Local examples have no provider dependency.
