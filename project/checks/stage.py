"""Independent sample stages with explicitly declared inputs and outputs."""
import json
from pathlib import Path
import sys
import time

stage = sys.argv[1]
folder = Path("project/build")
folder.mkdir(parents=True, exist_ok=True)
if stage == "prepare":
    result = {"contract": "Preserve the title and complete the example"}
elif stage == "unit":
    data = json.loads(Path("project/app/result.json").read_text())
    assert data["complete"] is True
    time.sleep(.05)
    result = {"functional_check": "passed"}
elif stage == "review":
    data = json.loads(Path("project/app/result.json").read_text())
    assert data["title"] == "Portable example"
    assert set(data) == {"title", "complete"}
    time.sleep(.05)
    result = {"contract_check": "passed"}
else:
    raise SystemExit("Unknown sample stage")
(folder / (stage + ".json")).write_text(json.dumps(result) + "\n")
print(json.dumps(result))
