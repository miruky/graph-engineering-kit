"""A real, deterministic sample verifier using JUnit's failure/error distinction."""
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

root = Path.cwd()
destination = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ["AGENTKIT_RESULT"])
cases = []


def test(name, function):
    case = ET.Element("testcase", {"classname": "sample.contract", "name": name})
    try:
        function()
    except AssertionError as exc:
        ET.SubElement(case, "failure", {"type": "AssertionError"}).text = str(exc)
    except Exception as exc:
        ET.SubElement(case, "error", {"type": type(exc).__name__}).text = "Verifier could not evaluate the product"
    cases.append(case)


def ready():
    data = json.loads((root / "project/app/result.json").read_text())
    assert data.get("complete") is True, "The product must set complete to true"


def title():
    data = json.loads((root / "project/app/result.json").read_text())
    assert data.get("title") == "Portable example", "The agreed title must be preserved"


test("complete", ready)
test("title", title)
failures = sum(x.find("failure") is not None for x in cases)
errors = sum(x.find("error") is not None for x in cases)
suite = ET.Element("testsuite", {"name": "sample.contract", "tests": str(len(cases)),
                   "failures": str(failures), "errors": str(errors), "skipped": "0"})
suite.extend(cases)
destination.parent.mkdir(parents=True, exist_ok=True)
ET.ElementTree(suite).write(destination, encoding="utf-8", xml_declaration=True)
print(json.dumps({"tests": len(cases), "failures": failures, "errors": errors}))
raise SystemExit(1 if failures or errors else 0)
