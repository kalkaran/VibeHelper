#!/usr/bin/env python3
"""Record successful completion of the entire managed test target."""

import argparse
import fcntl
import importlib.util
import json
import os
import subprocess
import time
import uuid
from pathlib import Path


def hook_module():
    spec = importlib.util.spec_from_file_location(
        "verification_hook", Path(__file__).with_name("quality-hook.py")
    )
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    return hook


def record_tests(root, provider):
    hook = hook_module()
    hook.ensure_helpers()
    makefile = root / "vibe_scripts" / provider / "Makefile"
    receipt = hook.test_receipt(root, makefile)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with receipt.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(
                "[ai-quality] Full-suite verification is already running; wait for it."
            )
            return 2
        token = uuid.uuid4().hex
        with hook.QUALITY.receipt_guard(receipt):
            receipt.write_text(json.dumps({"status": "running", "token": token}))
        # Make adds transport flags to recursive invocations.
        # Semantic flags/overrides can skip commands or mask failures: no receipt.
        flags = (
            os.environ.get("MAKEFLAGS", "") + " " + os.environ.get("MFLAGS", "")
        ).split()
        eligible = (
            not os.environ.get("MAKEOVERRIDES")
            and not os.environ.get("PYTEST_ADDOPTS")
            and all(flag in {"w", "-w", "--no-print-directory"} for flag in flags)
        )
        before = None
        try:
            if eligible:
                before = hook.test_signature(root, makefile)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print(f"[ai-quality] Verification reuse unavailable: {error}")
        log = receipt.parent / f"{provider}-{time.time_ns()}.log"
        with log.open("w") as output:
            completed = subprocess.run(
                ["make", "--no-print-directory", "-f", str(makefile), "test-run-ai"],
                cwd=root,
                env={**os.environ, "VIBEHELPER_TEST_RUN": token},
                stdout=output,
                stderr=subprocess.STDOUT,
                check=False,
            )
        output = log.read_text()
        diagnostics = hook.QUALITY.parse_text_diagnostics(
            output, completed.returncode, 1
        )
        value = hook.QUALITY.result(
            "passed" if completed.returncode == 0 else "findings",
            "full test target",
            diagnostics,
            returncode=completed.returncode,
            log=str(log),
            duration_seconds=round(time.monotonic() - started, 2),
        )
        hook.QUALITY.save_summary(value, root=root)
        print(hook.QUALITY.render_result(value))
        if completed.returncode == 0 and before is not None:
            try:
                after = hook.test_signature(root, makefile)
                with hook.QUALITY.receipt_guard(receipt):
                    current = json.loads(receipt.read_text())
                    if current.get("token") == token and before == after:
                        temporary = receipt.with_suffix(".tmp")
                        temporary.write_text(
                            json.dumps(
                                {
                                    "status": "passed",
                                    "signature": before,
                                    "log": str(log),
                                }
                            )
                            + "\n"
                        )
                        temporary.replace(receipt)
                    else:
                        print(
                            "[ai-quality] Inputs or verification changed during tests; no matching full-suite result was recorded."
                        )
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                print(f"[ai-quality] Verification reuse unavailable: {error}")
        elif not eligible:
            print(
                "[ai-quality] Make overrides or test-selection environment prevent full-suite reuse."
            )
        print(
            f"[ai-quality] Full test target: exit {completed.returncode}, {time.monotonic() - started:.2f}s; log: {log}"
        )
        return completed.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("codex", "claude"), required=True)
    args = parser.parse_args()
    return record_tests(Path.cwd().resolve(), args.provider)


if __name__ == "__main__":
    raise SystemExit(main())
