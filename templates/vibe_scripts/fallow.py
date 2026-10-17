#!/usr/bin/env python3
"""Default Fallow installation and scoped, advisory lint reports."""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

VERSION = "3.33.1"
SOURCE_EXTENSIONS = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}


def sibling(name):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cache(root):
    path = root / ".cache/vibehelper-tools/fallow" / VERSION
    if not path.resolve().is_relative_to(root):
        raise ValueError("Fallow tool cache leaves the repository")
    return path


def binary(root):
    for path in (
        root / "node_modules/.bin/fallow",
        cache(root) / "node_modules/.bin/fallow",
    ):
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def sources(root):
    return [
        p
        for p in sibling("agent-check-edited").all_project_files(root)
        if Path(p).suffix in SOURCE_EXTENSIONS
    ]


def prepare_runtime(root, provider, dry_run):
    """Refresh unchanged receipt-owned companions; preserve custom files."""
    receipt = root / f".cache/vibehelper-install/{provider}.json"
    owned = json.loads(receipt.read_text()) if receipt.is_file() else {}
    replacements = []
    for name in ("fallow.py", "agent-verify.sh", "ai-quality-wrapper.py"):
        source = Path(__file__).with_name(name)
        destination = root / f"vibe_scripts/{provider}/{name}"
        if any(
            p.is_symlink()
            for p in (destination, destination.parent, destination.parent.parent)
        ):
            raise ValueError(f"preserved symlinked runtime: {destination}")
        if destination.is_file():
            current = destination.read_bytes()
            if current == source.read_bytes():
                continue
            if (
                not isinstance(owned, dict)
                or owned.get(destination.relative_to(root).as_posix())
                != hashlib.sha256(current).hexdigest()
            ):
                raise ValueError(
                    f"preserved custom/unowned runtime: {destination}; review before updating"
                )
        replacements.append((source, destination))
    if not dry_run:
        for source, destination in replacements:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                shutil.copy2(destination, str(destination) + f".bak.{time.time_ns()}")
            shutil.copy2(source, destination)
            destination.chmod(0o755)


def install(root, allow_install, dry_run, provider=None):
    if not sources(root):
        print("[fallow] skipped: no JavaScript/TypeScript source files found.")
        return 0
    if provider:
        prepare_runtime(root, provider, dry_run)
    if binary(root):
        print(
            "[fallow] available: preserving existing local installation; analysis has not run."
        )
        return 0
    if not allow_install:
        print(
            "[fallow] not installed: package changes disabled; report command requires a local Fallow installation."
        )
        return 0
    if dry_run:
        print(
            f"[fallow] Would install fallow@{VERSION} in {cache(root).relative_to(root)} (Node >=22 and npm required)."
        )
        return 0
    if not shutil.which("npm") or not shutil.which("node"):
        raise ValueError("Fallow installation requires Node >=22 and npm on PATH")
    version = subprocess.run(
        ["node", "--version"], capture_output=True, text=True, check=True
    ).stdout
    if not re.match(r"v?(\d+)", version) or int(re.match(r"v?(\d+)", version)[1]) < 22:
        raise ValueError(
            "Fallow 3.33.1 requires Node >=22; existing Node installation preserved"
        )
    result = subprocess.run(
        [
            "npm",
            "install",
            "--prefix",
            str(cache(root)),
            "--no-audit",
            "--no-fund",
            "--save-exact",
            f"fallow@{VERSION}",
        ],
        cwd=root,
        check=False,
    )
    if result.returncode or not binary(root):
        raise ValueError(
            f"Fallow installation failed (npm exit {result.returncode}); rerun after repairing the diagnostic"
        )
    print(f"[fallow] installed: {VERSION}; analysis has not run.")
    return 0


def scope_paths(root, paths):
    selected = []
    for value in paths:
        path = (root / value).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"Scope leaves repository: {value}")
        selected.append(path.relative_to(root).as_posix())
    return selected


def matches(path, scope):
    return any(
        item == "." or path == item or path.startswith(item.rstrip("/") + "/")
        for item in scope
    )


def finding_paths(item, root):
    """Find source references in native findings, including clone partners."""
    if isinstance(item, dict):
        for key, value in item.items():
            if key in {
                "path",
                "file",
                "file_path",
                "files",
                "locations",
                "cycle",
                "used_in",
                "dir_a",
                "dir_b",
            }:
                for path in value if isinstance(value, list) else [value]:
                    if isinstance(path, str):
                        path = re.sub(r":\d+(?::\d+)?$", "", path)
                        resolved = (root / path).resolve()
                        if resolved.is_relative_to(root):
                            yield resolved.relative_to(root).as_posix()
                    elif isinstance(path, (dict, list)):
                        yield from finding_paths(path, root)
            elif isinstance(value, (dict, list)):
                yield from finding_paths(value, root)
    elif isinstance(item, list):
        for value in item:
            yield from finding_paths(value, root)


def scoped_findings(document, root, scope):
    for category, items in document.items():
        if not isinstance(items, list) or category in {
            "workspace_diagnostics",
            "warnings",
            "next_steps",
            "clone_families",
        }:
            continue
        for item in items:
            if isinstance(item, dict) and (
                scope is None
                or any(matches(p, scope) for p in finding_paths(item, root))
            ):
                yield category, item


def brief(item):
    if isinstance(item, dict):
        return {
            key: brief(value)
            for key, value in item.items()
            if key not in {"actions", "fragment", "suggestions", "next_steps"}
        }
    if isinstance(item, list):
        return [brief(value) for value in item]
    return item


def report_count(document, analysis):
    if not isinstance(document, dict):
        return None
    diagnostics = document.get("workspace_diagnostics", [])
    if not isinstance(diagnostics, list) or any(
        not isinstance(item, dict) for item in diagnostics
    ):
        return None
    if analysis == "dead-code":
        count = document.get("total_issues")
    else:
        stats = document.get("stats", {})
        groups = document.get("clone_groups", [])
        if (
            not isinstance(stats, dict)
            or not isinstance(groups, list)
            or any(not isinstance(item, dict) for item in groups)
        ):
            return None
        count = stats.get("clone_groups", document.get("total_clones"))
    return count if type(count) is int and count >= 0 else None


def report(root, paths, all_files):
    scope = None if all_files else scope_paths(root, paths)
    project_sources = sources(root)
    if not project_sources or (
        scope is not None and not any(matches(p, scope) for p in project_sources)
    ):
        print(
            "[fallow] not_checked: no JavaScript/TypeScript sources in the requested scope."
        )
        return 2
    executable = binary(root)
    if not executable:
        print(
            "[fallow] not_checked: Fallow is not installed; rerun the standard installer."
        )
        return 2
    quality = sibling("ai-quality-wrapper")
    failed = False
    print(
        "[fallow] Advisory report; findings do not authorize repairs or replace required quality checks."
    )
    print(
        "[fallow] Full project analysis; displayed scope: "
        + (", ".join(scope) if scope is not None else "all files")
    )
    for analysis in ("dead-code", "dupes"):
        result = quality.run_quality(
            [executable, analysis, "--format", "json", "--quiet"],
            label="fallow-" + analysis,
            cwd=root,
            timeout=120,
        )
        log = Path(result["log"]) if result.get("log") else None
        log_text = log.read_text(errors="replace") if log else ""
        documents, _ = quality.json_documents(log_text)
        document = next(
            (d for d in documents if isinstance(d, dict) and d.get("kind") == analysis),
            None,
        )
        count = report_count(document, analysis)
        if (
            result.get("execution_error") is not None
            or ("execution_error" not in result and result["status"] == "tool_failure")
            or result["returncode"] not in (0, 1)
            or not log_text.endswith(f"\nexit_code={result['returncode']}\n")
            or document is None
            or count is None
        ):
            result["status"] = "tool_failure"
            print(quality.render_result(result))
            failed = True
            continue
        findings = list(scoped_findings(document, root, scope))
        print(
            f"[fallow] {analysis}: reported {len(findings)} scoped items; {count} total native findings."
        )
        for category, item in findings[:20]:
            print(f"  {category}: {json.dumps(brief(item), ensure_ascii=False)[:700]}")
        if len(findings) > 20:
            print(
                f"  ... {len(findings) - 20} scoped items omitted; inspect the full log."
            )
        diagnostics = document.get("workspace_diagnostics", [])
        if diagnostics:
            print(
                "  Coverage diagnostics (details in log; configuration changes are outside this report):"
            )
            for item in diagnostics[:10]:
                print(f"    {item.get('kind', 'unknown')}: {item.get('path', '')}")
        for key in ("warnings", "gate_outcomes"):
            if document.get(key):
                print(
                    f"  {key}: {json.dumps(document[key], ensure_ascii=False)[:1400]}"
                )
        print(f"[fallow] full report log: {log}")
    return 2 if failed else 0


def lint(root):
    project_sources = sources(root)
    if not project_sources:
        print("[fallow] not_applicable: no JavaScript/TypeScript source files.")
        return 0
    provider = Path(__file__).parent.name
    variables = (
        ("CODEX_THREAD_ID", "CODEX_SESSION_ID")
        if provider == "codex"
        else ("CLAUDE_SESSION_ID",)
    )
    paths = []
    try:
        for variable in variables:
            session = os.environ.get(variable)
            if not session:
                continue
            key = provider + "-" + hashlib.sha256(session.encode()).hexdigest()[:24]
            state_path = root / ".cache/quality-sessions" / (key + ".json")
            if not state_path.is_file():
                continue
            state = json.loads(state_path.read_text())
            paths = state.get("paths") if isinstance(state, dict) else None
            if not isinstance(paths, list) or not all(
                isinstance(p, str) for p in paths
            ):
                raise ValueError("invalid session paths")
            paths = sibling("agent-check-edited").project_paths(
                root, scope_paths(root, paths)
            )
            break
    except (OSError, ValueError) as error:
        print(f"[fallow] not_checked: session scope unavailable ({error}).")
        return 0
    if not paths:
        print(
            f"[fallow] not_checked: no active session edit scope. For this task, use ./vibe_scripts/{provider}/agent-verify.sh fallow --files PATH."
        )
        return 0
    if not any(matches(p, paths) for p in project_sources):
        print(
            "[fallow] not_checked: no JavaScript/TypeScript sources in session edits."
        )
        return 0
    if not binary(root):
        print(
            "[fallow] unavailable: rerun the standard installer; advisory analysis has not run."
        )
        return 0
    return report(root, paths, False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("install")
    setup.add_argument("--allow-install", type=int, choices=(0, 1), default=0)
    setup.add_argument("--dry-run", action="store_true")
    setup.add_argument("--provider", choices=("codex", "claude"))
    commands.add_parser("lint")
    reports = commands.add_parser("report")
    scope = reports.add_mutually_exclusive_group(required=True)
    scope.add_argument("--files", nargs="+")
    scope.add_argument("--all", action="store_true")
    args = parser.parse_args()
    root = Path.cwd().resolve()
    try:
        if args.command == "install":
            return install(root, args.allow_install, args.dry_run, args.provider)
        if args.command == "lint":
            return lint(root)
        return report(root, args.files or [], args.all)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        if args.command == "install":
            print(f"[fallow] unavailable: {error}; advisory analysis has not run.")
            return 0
        print(f"[fallow] tool_failure: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
