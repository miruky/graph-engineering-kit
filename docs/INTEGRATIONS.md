# Codex, Claude Code and other workers

## Instructions versus execution

`AGENTS.md` and `CLAUDE.md` describe a workflow. They do not install hooks, change tool permissions, revoke credentials, or prove compliance. Keep the existing project instructions when adopting the toolkit.

The core interface is a command plus declared files. Your project can be written in any language. A verifier may be a test runner, compiler, CLI acceptance script, document validator, or another check whose result is meaningful for your goal.

## Provider presets

```sh
python3 kit.py agent-command claude
python3 kit.py agent-command codex
```

These commands print configuration; they do not invoke a model or save credentials. Review the preset and selected project before enabling it.

The Claude preset uses restricted file tools (`Read`, `Edit`, `Write`, `Glob`, `Grep`), no shell tools, an empty explicit MCP configuration, and noninteractive refusal of requests that still need permission. Its per-invocation cost limit is `--max-budget-usd 2`; controller attempt limits are separate. Existing authorized Claude login or explicitly passed provider credentials are required. The preset's flags correspond to Claude Code 2.1.278; `--restricted`, `--permission-prompts`, and JSON result support must be available in the installed CLI.

The Codex preset uses `codex exec --ephemeral --sandbox workspace-write --json -`. Its flags correspond to Codex CLI 0.154.0. The selected project must satisfy Codex's own repository/trust requirements. Existing sandbox, execution-policy and approval restrictions still apply. No bypass, unrestricted sandbox or hook-trust bypass flag is used.

CLI authentication, access plans, quotas and model selection belong to the provider. `doctor` reports executable availability, not authentication. Review customizations in a project before a provider run. Do not run an unfamiliar repository merely because its instructions suggest it.

## Generic workers

Use `provider: "command"` and a trusted argv array. Workers receive a JSON request on stdin containing their goal and, where applicable, feedback and protected-file names. The controller independently invokes the verifier. An exit code or success sentence from a worker never substitutes for that verifier.

The generic route has no provider-specific model requirement. The command's own permissions and cost controls remain the operator's responsibility.

## Configuration transport

Supported substitutions are `{python}`, `{project}`, and `{exe_suffix}`. A JUnit verifier also receives `{result}` and `AGENTKIT_RESULT`, both pointing to a fresh result file. Each substituted value remains one argv element. Unknown placeholders fail instead of silently producing a different command.

Use `pass_env` only for environment variable names needed by that command; never put credential values into JSON, prompts or source files. Feedback excerpts sent to a worker are bounded independently from the full local output record.
