#!/usr/bin/env python3
"""Select an already-installed supported Python; never download or change global settings."""
import os
import shutil
import subprocess
import sys

if sys.version_info < (3, 10):
    candidates = [os.environ.get("AGENTKIT_PYTHON", ""), "python3.14", "python3.13",
                  "python3.12", "python3.11", "python3.10"]
    for candidate in candidates:
        executable = shutil.which(candidate) if candidate else None
        if not executable:
            continue
        try:
            probe = subprocess.run([executable, "-c", "import sys;sys.exit(0 if sys.version_info >= (3,10) else 1)"],
                                   capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0:
            os.execv(executable, [executable, os.path.abspath(__file__)] + sys.argv[1:])
    raise SystemExit("Python 3.10+ must be installed. Set AGENTKIT_PYTHON to its executable if it is not on PATH. No pip install is needed.")

from agentkit.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
