$ErrorActionPreference = "Stop"
if ($env:AGENTKIT_PYTHON) { & $env:AGENTKIT_PYTHON "$PSScriptRoot/kit.py" @args } else { & py -3 "$PSScriptRoot/kit.py" @args }
exit $LASTEXITCODE
