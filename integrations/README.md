# Integration data

These files are example worker declarations. They are not automatically loaded or executed. Use `agent-command` to print the current preset, or the controller's `configure-agent` command to select it. Provider authentication and permissions must already be appropriate for the chosen project.

Project-local provider hook configuration is generated on demand only by the harness kit's `integration` command. Inspect/merge it and perform the provider's own trust procedure. Do not overwrite an existing configuration file with redirected output.
