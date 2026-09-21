# Graph Engineering Kit instructions

The target project's programming language is unrestricted. Python is the toolkit runtime, not a required application language.

1. Read README.md (or README.ja.md), the project contract, and .agentkit/graph.json.
2. Run `python3 kit.py doctor` and `python3 kit.py demo` to establish the local example.
3. For real work, define the intended outputs and independent verification command before changing implementation.
4. Do not edit protected verification inputs, state records or policy merely to obtain a successful result.
5. Do not push, publish, delete external resources or change credentials without explicit user authorization. A written rule is not an execution boundary.
6. Follow the documented workflow. Report actual tests and results; a model completion statement is not evidence.
7. Run `python3 kit.py check` after changing toolkit code. Keep credentials, private paths and generated state out of commits.

Local/provider hooks require their own installation and trust review. Do not bypass that review or weaken existing permissions.
