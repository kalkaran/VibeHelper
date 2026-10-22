"""Install Graphify and register its skill consistently for both providers."""

import argparse
import os
import shlex
import shutil
import site
import subprocess
import sys
from pathlib import Path


def probe(*command):
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError:
        return subprocess.CompletedProcess(command, 127, "", "")


def find_graphify():
    binary = shutil.which("graphify")
    if binary:
        return binary
    bins = [Path.home() / ".local/bin", Path(site.getuserbase()) / "bin"]
    uv_bin = probe("uv", "tool", "dir", "--bin")
    if uv_bin.returncode == 0 and uv_bin.stdout.strip():
        bins.insert(0, Path(uv_bin.stdout.strip()))
    bins.extend(sorted((Path.home() / "Library/Python").glob("*/bin"), reverse=True))
    return next(
        (str(p / "graphify") for p in bins if os.access(p / "graphify", os.X_OK)),
        None,
    )


def upgrade_command(binary):
    for command, upgrade in (
        (("uv", "tool", "list"), ["uv", "tool", "upgrade", "graphifyy"]),
        (("pipx", "list", "--short"), ["pipx", "upgrade", "graphifyy"]),
    ):
        result = probe(*command)
        if result.returncode == 0 and any(
            line.split()[:1] == ["graphifyy"] for line in result.stdout.splitlines()
        ):
            return upgrade
    try:
        with Path(binary).open() as stream:
            header = stream.readline()
        interpreter = shlex.split(header[2:]) if header.startswith("#!") else []
    except (OSError, UnicodeError, ValueError):
        return None
    if len(interpreter) == 2 and interpreter[0] == "/usr/bin/env":
        interpreter = [shutil.which(interpreter[1])]
    if len(interpreter) != 1 or not interpreter[0]:
        return None
    if probe(*interpreter, "-m", "pip", "show", "graphifyy").returncode == 0:
        scope = probe(
            *interpreter,
            "-c",
            "import site; from pathlib import Path; from importlib.metadata import distribution; "
            "print(Path(distribution('graphifyy').locate_file('')).resolve() "
            "== Path(site.getusersitepackages()).resolve())",
        )
        if scope.returncode != 0 or scope.stdout.strip() not in ("True", "False"):
            return None
        return (
            [*interpreter, "-m", "pip", "install", "--upgrade"]
            + (["--user"] if scope.stdout.strip() == "True" else [])
            + ["graphifyy"]
        )
    return None


def run(command, dry_run):
    print(("Would run: " if dry_run else "Running: ") + shlex.join(command), flush=True)
    if dry_run:
        return True
    try:
        return subprocess.run(command, check=False).returncode == 0
    except OSError as error:
        print(f"Graphify command failed: {error}", file=sys.stderr)
        return False


def install(platform, refresh=False, dry_run=False, binary=None):
    binary = binary or find_graphify()
    success = True
    if binary and refresh:
        command = upgrade_command(binary)
        if command:
            success = run(command, dry_run)
            if not success:
                print("Graphify package upgrade failed; attempting skill registration.")
        else:
            print(
                "Graphify package upgrade skipped: unknown package owner; using existing executable for skill registration."
            )
    elif not binary:
        if shutil.which("uv"):
            command = ["uv", "tool", "install", "graphifyy"]
        elif shutil.which("pipx"):
            command = ["pipx", "install", "graphifyy"]
        else:
            command = [sys.executable, "-m", "pip", "install"]
            if sys.prefix == sys.base_prefix:
                command.append("--user")
            command.append("graphifyy")
        if not run(command, dry_run):
            print("Graphify package installation failed.", file=sys.stderr)
            return 1
        binary = "graphify" if dry_run else find_graphify()
    if not binary:
        print("Graphify executable missing after installation.", file=sys.stderr)
        return 1
    skill_roots = (".agents", ".codex") if platform == "codex" else (".claude",)
    if not refresh and any(
        (Path.home() / root / "skills/graphify/SKILL.md").is_file()
        for root in skill_roots
    ):
        print(f"Graphify {platform} skill already installed.")
        return 0
    registered = run([binary, "install", "--platform", platform], dry_run)
    if not registered:
        print("Graphify skill registration failed.", file=sys.stderr)
    elif success and not dry_run:
        print(f"Graphify {platform} skill ready.")
    return 0 if success and registered else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("codex", "claude"), required=True)
    parser.add_argument("--refresh", type=int, choices=(0, 1), default=0)
    parser.add_argument("--dry-run", type=int, choices=(0, 1), default=0)
    parser.add_argument("--binary")
    args = parser.parse_args()
    return install(args.platform, args.refresh, args.dry_run, args.binary)


if __name__ == "__main__":
    raise SystemExit(main())
