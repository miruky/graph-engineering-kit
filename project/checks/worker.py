"""Model-free sample worker. Real-provider validation is recorded separately."""
import json
from pathlib import Path
import sys

request = json.load(sys.stdin)
path = Path("project/app/result.json")
value = json.loads(path.read_text())
mode = sys.argv[1] if len(sys.argv) > 1 else "graph"
value["complete"] = mode != "loop" or request["attempt"] >= 2
path.write_text(json.dumps(value, indent=2) + "\n")
print(json.dumps({"work_performed": True, "completion_is_decided_by": "independent verifier"}))
