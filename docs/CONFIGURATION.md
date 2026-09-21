# Configuration reference

The complete runnable JSON configuration is in `.agentkit/graph.json`; an independent starter copy is in `examples/project`. `docs/USAGE.md` explains each field and the controller's state/exit behavior. Unknown keys, invalid types, non-finite numeric values and duplicate JSON keys fail. Do not add a guessed parameter and assume it is active.

All project selectors use relative paths and explicit glob patterns. A command is an argv array; shell strings and command interpolation are not accepted. `{python}` selects the toolkit's actual Python interpreter, so the application itself need not be Python.

Exit code 0 means the requested operation met its documented success condition. Code 2 is an input/policy/state error. Code 3 is an incomplete or failing workflow/review. Code 130 is an interruption. Read-only `status` and hook/integration rendering commands can return 0 while describing a workflow that is not complete.
