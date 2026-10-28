"""Model-facing reports preserve evidence without replaying console output."""

import json
import subprocess
import sys

from test_quality_delivery import TEMPLATES, WRAPPER, command


def test_pytest_failure_extracts_diagnosis_and_keeps_complete_log(tmp_path):
    output = "===== FAILURES =====\n___ test_browser ___\nprogress noise\nE AssertionError: expected visible, got hidden\ntests/browser.py:42: AssertionError\nFAILED tests/browser.py::test_browser - AssertionError\n1 failed, 4 passed\n"
    value = command(tmp_path, f"print({output!r}); raise SystemExit(1)")
    shown = WRAPPER.render_result(value)
    assert "expected visible, got hidden" in shown
    assert "tests/browser.py:42" in shown and "log:5:" in shown
    assert "=====" not in shown and "progress noise" not in shown
    assert value["returncode"] == 1
    from pathlib import Path

    assert output in Path(value["log"]).read_text()


def test_unknown_and_excessive_output_are_flagged_not_truncated(tmp_path):
    value = command(tmp_path, "print('unrecognized chatter'); raise SystemExit(1)")
    assert "UNPARSED FAILURE" in WRAPPER.render_result(value)
    assert "unrecognized chatter" not in WRAPPER.render_result(value)
    text = "\n".join(f"file.py:{n}: error: issue {n}" for n in range(100))
    records = WRAPPER.parse_text_diagnostics(text, 1)
    assert len(records) == 100
    shown = WRAPPER.render_result(WRAPPER.result("findings", "large", records))
    assert "REVIEW NEEDED" in shown and "issue 99" not in shown
    assert "chars omitted" not in shown and "output lines omitted" not in shown


def test_success_notice_has_counts_without_chatter(tmp_path):
    value = command(tmp_path, "print('banner chatter'); print('12 passed in 1.2s')")
    shown = WRAPPER.render_result(value)
    assert "passed" in shown and "12 passed" in shown
    assert "banner chatter" not in shown
    assert WRAPPER.parse_text_diagnostics("All checks passed!", 0) == ["PASS"]
    assert "UNPARSED" not in str(
        WRAPPER.parse_text_diagnostics("3 files left unchanged", 0)
    )


def test_rtk_success_uses_original_counts_when_filter_format_is_unknown(monkeypatch):
    calls = []
    monkeypatch.setattr(WRAPPER.shutil, "which", lambda _: "/usr/bin/rtk")

    def filtered(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "unfamiliar RTK banner", "")

    monkeypatch.setattr(WRAPPER.subprocess, "run", filtered)
    lines, error = WRAPPER.adapter_summary(
        "rtk:ruff-check", "All checks passed!", 0, 20
    )
    assert calls == [["/usr/bin/rtk", "pipe", "--filter", "ruff-check"]]
    assert lines == ["PASS"] and error is None


def test_requested_log_context_is_only_eleven_lines(tmp_path):
    log = tmp_path / "failure.log"
    log.write_text("\n".join(f"entry {n}" for n in range(1, 101)))
    result = subprocess.run(
        [
            sys.executable,
            str(TEMPLATES / "ai-quality-wrapper.py"),
            "--label",
            "context",
            "--report-log",
            str(log),
            "--context-line",
            "50",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == [f"{n}: entry {n}" for n in range(45, 56)]


def test_native_diagnostics_are_not_silently_dropped():
    rows = [f"source.py:{n}: [error] failure" for n in range(100)]
    result = WRAPPER.render_native("100 errors", rows, [], 10)
    assert len(result) == 101 and rows[-1] in result


def test_indented_unknown_output_is_flagged():
    assert (
        "UNPARSED OUTPUT"
        in WRAPPER.parse_text_diagnostics("  unexpected tool response", 0)[0]
    )


def test_passed_child_notices_survive_aggregation(tmp_path, monkeypatch):
    from test_quality_delivery import CHECKER

    (tmp_path / "demo.py").write_text("pass\n")
    monkeypatch.setattr(
        CHECKER,
        "plan_checks",
        lambda *_args: (
            [(tmp_path, ("fixture", ["unused"], False, 20, "generic"))],
            [],
            {"demo.py": "required checks planned"},
        ),
    )
    monkeypatch.setattr(
        CHECKER.QUALITY,
        "run_quality",
        lambda *_args, **_kwargs: WRAPPER.result(
            "passed",
            "fixture",
            ["UNPARSED OUTPUT: inspect log:2", "2 warnings"],
        ),
    )
    value = CHECKER.check_files(tmp_path, ["demo.py"])
    shown = WRAPPER.render_result(value)
    assert value["status"] == "passed"
    assert "UNPARSED OUTPUT" in shown and "2 warnings" in shown


def test_native_failure_identifies_log_document_start(tmp_path):
    report = {
        "comments": [
            {
                "file": "app.sh",
                "line": 7,
                "column": 2,
                "level": "error",
                "code": 2086,
                "message": "Quote variable",
            }
        ]
    }
    value = command(
        tmp_path,
        f"print({json.dumps(report)!r}); raise SystemExit(1)",
        adapter="native:shellcheck-json1",
    )
    assert any(
        "log:2 (JSON output starts): app.sh:7:2" in row for row in value["summary"]
    )


def test_unknown_residue_counts_and_plain_errors_are_preserved():
    result = WRAPPER.parse_text_diagnostics(
        "Error: boom\n=== 1 failed in 0.2s ===\nunfamiliar reason", 1
    )
    assert any("Error: boom" in row for row in result)
    assert any("1 failed" in row for row in result)
    assert any("UNPARSED FAILURE" in row and "log:4" in row for row in result)
    assert (
        "UNPARSED OUTPUT" in WRAPPER.parse_text_diagnostics("unfamiliar output", 0)[0]
    )


def test_complete_parsed_sidecar_survives_display_budget(tmp_path):
    from pathlib import Path

    output = "\n".join(f"source.py:{n}: error: issue {n}" for n in range(100))
    value = command(tmp_path, f"print({output!r}); raise SystemExit(1)")
    assert "REVIEW NEEDED" in WRAPPER.render_result(value)
    saved = json.loads(Path(value["evidence"]).read_text())
    assert any("issue 99" in row for row in saved["summary"])


def test_huge_context_record_is_flagged_without_a_partial_dump(tmp_path):
    log = tmp_path / "large.log"
    log.write_text("x" * 10000)
    result = subprocess.run(
        [
            sys.executable,
            str(TEMPLATES / "ai-quality-wrapper.py"),
            "--label",
            "context",
            "--report-log",
            str(log),
            "--context-line",
            "1",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "REVIEW NEEDED" in result.stdout and "xxxx" not in result.stdout


def test_reporting_migration_is_scoped_and_backed_up(tmp_path):
    import shutil

    from test_project_memory import MANAGED

    scripts = tmp_path / "vibe_scripts/codex"
    scripts.mkdir(parents=True)
    names = (
        "ai-quality-wrapper.py",
        "agent-check-edited.py",
        "agent-verify.sh",
        "test-verification.py",
        "project-memory.py",
    )
    owned = {}
    for name in names:
        target = scripts / name
        shutil.copy2(TEMPLATES / name, target)
        owned[str(target.relative_to(tmp_path))] = MANAGED.digest(target)
    receipt = tmp_path / ".cache/vibehelper-install/codex.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps(owned))
    untouched = scripts / "Makefile"
    untouched.write_text("custom recipes\n")
    MANAGED.install_usage_tracker(tmp_path, "codex", reporting=True)
    backups = list(receipt.parent.glob("reporting-backup-*/RESTORE.txt"))
    assert len(backups) == 1
    assert (backups[0].parent / "files/vibe_scripts/codex/project-memory.py").is_file()
    assert untouched.read_text() == "custom recipes\n"


def test_memory_cli_keeps_inventory_on_disk(tmp_path, monkeypatch, capsys):
    from test_project_memory import MEMORY

    value = {
        "classification": {"code": ["a.py"] * 1000},
        "freshness": {
            "crg": {
                "status": "current",
                "paths": [],
                "coverage": {"indexed": ["a.py"] * 1000},
            }
        },
    }
    monkeypatch.setattr(sys, "argv", ["memory", "status", "--root", str(tmp_path)])
    monkeypatch.setattr(MEMORY, "handle", lambda *_args: value)
    monkeypatch.setattr(MEMORY, "run", lambda *_args: str(tmp_path))
    assert MEMORY.main() == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["classification"]["code"] == 1000
    assert shown["freshness"]["crg"]["coverage"]["indexed"] == 1000
    assert len(json.dumps(shown)) < 1000
    assert (
        json.loads((tmp_path / ".cache/project-memory/status-report.json").read_text())
        == value
    )
