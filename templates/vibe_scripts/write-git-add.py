"""Save reviewed pathspecs from stdin, retaining timestamped recommendations."""

import argparse
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path


def write_manifest(root, content):
    root = root.resolve()
    current = root / "git-add.txt"
    history = root / "notes/git-add-history"
    if current.is_symlink() or not history.resolve().is_relative_to(root):
        raise ValueError("Manifest and history must remain inside the project")
    history.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S-%fZ")
    if current.exists():
        with (history / f"{stamp}-previous.txt").open("xb") as stream:
            stream.write(current.read_bytes())
    destination = history / f"{stamp}.txt"
    with destination.open("xb") as stream:
        stream.write(content)
    descriptor, temporary = tempfile.mkstemp(prefix=".git-add-", dir=root)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.replace(temporary, current)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        history = write_manifest(args.root, sys.stdin.buffer.read())
    except (OSError, ValueError) as error:
        print(f"Manifest history failed: {error}", file=sys.stderr)
        return 1
    print(f"Updated git-add.txt; history: {history}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
