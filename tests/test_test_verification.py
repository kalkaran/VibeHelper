"""Full controlled targets, including multiple commands, are reusable only on success."""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "templates/vibe_scripts"


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def project(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(".cache/\nvibe_scripts/\n")
    (tmp_path / "source.py").write_text("original\n")
    (tmp_path / "deleted.py").write_text("deleted\n")
    helper = tmp_path / "vibe_scripts/codex"
    helper.mkdir(parents=True)
    for name in (
        "quality-hook.py",
        "test-verification.py",
        "ai-quality-wrapper.py",
        "agent-check-edited.py",
    ):
        shutil.copy2(TEMPLATES / name, helper / name)
    makefile = helper / "Makefile"
    makefile.write_text(
        "export PATH := $(HOME)/.local/bin:$(PATH)\n"
        "test-ai:\n\t@python3 vibe_scripts/codex/ai-quality-wrapper.py --label test-first --shell -- 'python3 -c \"print(123)\"'\n"
        "\t@python3 vibe_scripts/codex/ai-quality-wrapper.py --label test-second --shell -- 'test ! -f .cache/fail'\n"
    )
    load(TEMPLATES / "managed-install.py").configure_test_verification(
        tmp_path, "codex"
    )
    hook = load(helper / "quality-hook.py")
    hook.ensure_helpers()
    monkeypatch.setattr(
        hook.CHECKER,
        "check_files",
        lambda *a, **k: hook.QUALITY.result("passed", "edited", []),
    )
    payload = {"session_id": "full-tests"}
    hook.handle(tmp_path, "codex", "SessionStart", payload)
    (tmp_path / "deleted.py").unlink()
    return tmp_path, hook, payload, makefile


def run(project):
    root, _, _, makefile = project
    result = subprocess.run(
        ["make", "-f", str(makefile), "test-ai"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    return result


def receipt_matches(project):
    root, hook, _, makefile = project
    path = hook.test_receipt(root, makefile)
    if not path.exists():
        return False
    receipt = json.loads(path.read_text())
    return receipt.get("status") == "passed" and receipt.get(
        "signature"
    ) == hook.test_signature(root, makefile)


def test_complete_target_records_success_without_hook_dependency(project):
    root, hook, payload, _ = project
    assert hook.handle(root, "codex", "Stop", payload) == {}
    assert not receipt_matches(project)
    result = run(project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert receipt_matches(project)
    assert hook.handle(root, "codex", "Stop", payload) == {}


def test_second_command_failure_invalidates_previous_success(project):
    root, hook, _, makefile = project
    assert run(project).returncode == 0
    assert receipt_matches(project)
    (root / ".cache/fail").touch()
    assert run(project).returncode != 0
    assert not receipt_matches(project)
    assert (
        json.loads(hook.test_receipt(root, makefile).read_text())["status"] != "passed"
    )


@pytest.mark.parametrize(
    "relative",
    [
        "source.py",
        "tests/new_test.py",
        "node_modules/example/index.js",
        "backend/source.py",
    ],
)
def test_changed_inputs_including_nested_repo_and_dependencies(project, relative):
    root, _, _, _ = project
    nested = root / "backend"
    nested.mkdir()
    subprocess.run(["git", "init", "-q", str(nested)], check=True)
    (nested / "source.py").write_text("before\n")
    assert run(project).returncode == 0
    assert receipt_matches(project)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("changed\n")
    assert not receipt_matches(project)


def test_targeted_run_invalidates_but_cannot_publish_full_success(project):
    root, hook, _, makefile = project
    assert run(project).returncode == 0
    result = subprocess.run(
        [
            sys.executable,
            str(makefile.parent / "ai-quality-wrapper.py"),
            "--label",
            "test-pytest-targeted",
            "--",
            sys.executable,
            "-c",
            "print('targeted')",
        ],
        cwd=root,
        check=False,
        capture_output=True,
    )
    assert result.returncode == 0
    assert not hook.test_receipt(root, makefile).exists()
    assert not receipt_matches(project)


def test_inputs_changed_while_running_do_not_publish(project):
    root, hook, _, makefile = project
    with makefile.open("a") as output:
        output.write("\t@echo changed >> source.py\n")
    assert run(project).returncode == 0
    assert (
        json.loads(hook.test_receipt(root, makefile).read_text())["status"] != "passed"
    )
    assert not receipt_matches(project)


def test_make_override_cannot_publish_success(project):
    root, hook, _, makefile = project
    result = subprocess.run(
        ["make", "-f", str(makefile), "test-ai", "TESTS=one"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    assert result.returncode == 0
    assert (
        json.loads(hook.test_receipt(root, makefile).read_text())["status"] != "passed"
    )


def test_environment_change_invalidates_success(project, monkeypatch):
    assert run(project).returncode == 0
    monkeypatch.setenv("TEST_DATABASE_URL", "changed")
    assert not receipt_matches(project)


def test_shell_and_agent_transport_do_not_invalidate_success(project, monkeypatch):
    assert run(project).returncode == 0
    for name in (
        "CODEX_THREAD_ID",
        "CODEX_TOOL_CALL_ID",
        "TERM_SESSION_ID",
        "MANPATH",
        "SHLVL",
    ):
        monkeypatch.setenv(name, "different transport")
    assert receipt_matches(project)


def test_migration_is_idempotent_and_preserves_custom_target(project):
    root, _, _, makefile = project
    managed = load(TEMPLATES / "managed-install.py")
    before = makefile.read_bytes()
    managed.configure_test_verification(root, "codex")
    assert makefile.read_bytes() == before
    custom = "test-ai:\n\t@echo custom tests\n"
    makefile.write_text(custom)
    managed.configure_test_verification(root, "codex")
    assert makefile.read_text() == custom


def test_invalidation_during_final_fingerprint_prevents_publication(
    project, monkeypatch
):
    root, hook, _, makefile = project
    driver = load(makefile.parent / "test-verification.py")
    monkeypatch.setattr(driver, "hook_module", lambda: hook)
    original = hook.test_signature
    calls = []

    def fingerprint(*args):
        signature = original(*args)
        calls.append(signature)
        if len(calls) == 2:
            receipt = hook.test_receipt(root, makefile)
            with hook.QUALITY.receipt_guard(receipt):
                receipt.unlink()
        return signature

    monkeypatch.setattr(hook, "test_signature", fingerprint)
    assert driver.record_tests(root, "codex") == 0
    assert len(calls) == 2
    assert not hook.test_receipt(root, makefile).exists()


def test_linked_dependency_directory_is_not_silently_omitted(project):
    root, hook, _, makefile = project
    dependencies = root / "node_modules"
    dependencies.mkdir()
    (dependencies / "linked").symlink_to(root, target_is_directory=True)
    with pytest.raises(OSError, match="symlinks"):
        hook.test_signature(root, makefile)


def test_unreadable_dependency_walk_is_not_silently_omitted(project, monkeypatch):
    root, hook, _, makefile = project
    (root / "node_modules").mkdir()

    def unreadable(*args, onerror, **kwargs):
        onerror(PermissionError("unreadable dependency"))
        return iter(())

    monkeypatch.setattr(hook.os, "walk", unreadable)
    with pytest.raises(PermissionError, match="unreadable"):
        hook.test_signature(root, makefile)
