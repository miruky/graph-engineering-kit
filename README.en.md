# Graph Engineering Kit

Coordinate a dependency workflow with parallel checks, explicit joins, retries and input-bound operator approval.

[日本語](README.ja.md) · [Workflow and commands](docs/USAGE.md) · [Design and boundaries](docs/ARCHITECTURE.md) · [Codex / Claude Code](docs/INTEGRATIONS.md)

## Run after cloning

Prerequisites: Git and **Python 3.10+**. No pip/npm install, API key or model subscription is needed for the local example. Your application can use any language, framework or test runner.

```sh
git clone <repository-url> graph-engineering-kit
cd graph-engineering-kit
python3 kit.py doctor
python3 kit.py demo
python3 kit.py check
```

On Windows use `py -3 kit.py ...`, `dev.cmd ...`, or `./dev.ps1 ...`. On macOS/Linux, `./dev ...` is a short form. Set `AGENTKIT_PYTHON` only if you need a particular interpreter.

`demo` executes a complete, model-free example in an isolated temporary project and checks expected rejections. `check` tests the toolkit itself. Neither command claims that your own product has been verified. See the documented project commands to verify your own outputs with independent checks.

## Use your own project

Create a working starter with `python3 kit.py new ../my-workspace`, or inspect a non-overwriting installation plan with `python3 kit.py install --target ../existing-project`. Add `--apply` to install the runtime and an example configuration. Existing instructions, active configuration and hooks are preserved. Adapt the file paths and verification argv to your project before a real run.

## What the names mean

Context engineering selects and maintains information available to the model. A harness supplies the runtime, tools, permissions and checks that carry out and constrain work. Writing “do not git push” belongs to instructions/context; enforcing a restriction in a runner, credential policy or repository rule is part of the execution system. These concerns overlap.

This kit is one practical implementation, not an official standard or a guarantee of correctness. Review the documented [trust boundaries](docs/ARCHITECTURE.md). In particular, local files/hooks editable by the same account are not an unbreakable security boundary.

## Included

- Working project, explicit JSON configuration and deterministic demos.
- Portable argv adapters with no language-specific build-system detection.
- Persistent local evidence and meaningful negative-path tests.
- Pinned CI for Linux, macOS and Windows.
- Sources, licenses and reproducible validation instructions.

MIT licensed. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
