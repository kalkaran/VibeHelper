"""Measure the same diagnostic/repair workload without claiming token savings."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_diagnostic_repair_measurement(tmp_path):
    tool = tmp_path / "check.py"
    tool.write_text(
        "import json,sys\n"
        "from pathlib import Path\n"
        "p=Path(sys.argv[1]); p.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
        "bad=sys.argv[2]=='bad'\n"
        "print(json.dumps({'comments': [{'file':'app.sh','line':7,'column':2,"
        "'level':'error','code':1009,'message':'Expected closing delimiter'}] if bad else []}))\n"
        "sys.exit(1 if bad else 0)\n"
    )
    wrappers = {"current": ROOT / "templates/vibe_scripts/ai-quality-wrapper.py"}
    snapshot = ROOT / ".cache/effectiveness-baseline/ai-quality-wrapper.py"
    baseline = os.environ.get("VIBEHELPER_BASELINE_WRAPPER") or (
        str(snapshot) if snapshot.is_file() else None
    )
    if baseline:
        wrappers["before"] = Path(baseline).resolve()
    measurements = {}
    for version, wrapper in wrappers.items():
        counter = tmp_path / f"{version}.count"
        logs = tmp_path / version
        results = []
        for state in ("bad", "good", "bad", "good"):
            started = time.monotonic()
            completed = subprocess.run(
                [
                    sys.executable,
                    str(wrapper),
                    "--label",
                    "measurement",
                    "--adapter",
                    "native:shellcheck-json1",
                    "--log-dir",
                    str(logs),
                    "--max-lines",
                    "10",
                    "--",
                    sys.executable,
                    str(tool),
                    str(counter),
                    state,
                ],
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            results.append(
                {
                    "state": state,
                    "seconds": time.monotonic() - started,
                    "emitted_bytes": len(
                        (completed.stdout + completed.stderr).encode()
                    ),
                    "returncode": completed.returncode,
                }
            )
            assert completed.returncode == (1 if state == "bad" else 0)
            if state == "bad":
                assert "app.sh:7:2" in completed.stdout
                assert "SC1009" in completed.stdout
                assert "Expected closing delimiter" in completed.stdout
        assert counter.read_text() == "xxxx", "Each requested check must execute once"
        assert len(list(logs.glob("*.log"))) == 4
        measurements[version] = {
            "runs": results,
            "command_count": len(counter.read_text()),
            "seconds": sum(item["seconds"] for item in results),
            "emitted_bytes": sum(item["emitted_bytes"] for item in results),
            "simulated_repairs_resolved": 2,
            "simulated_repairs_attempted": 2,
            "model_repair_rate": None,
            "measured_tokens": None,
            "delegation_tokens": None,
        }
    report = ROOT / ".cache/quality-measurement.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(measurements, indent=2) + "\n")
