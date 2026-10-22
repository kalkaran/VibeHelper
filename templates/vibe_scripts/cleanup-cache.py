"""Background Stop maintenance: retain recent diagnostics and newest backups."""

import argparse
import fcntl
import gzip
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

AGE = 3 * 24 * 60 * 60
BACKUP_DIR = re.compile(r"\d{8}T\d{6}Z(?:-[a-f0-9]+)?\Z")
BACKUP_FILE = re.compile(
    r"(?:\d{8}T\d{6}Z\.(?:dump|deletions\.jsonl)|(?:deployment-media|merged-media|release)\.tar\.gz|(?:merged-media|server)\.sha256|release)\Z"
)


def completed(path, root):
    directory = path.parent
    if path.name == "release.tar.gz":
        marker = root / "deployments" / ("react-app-" + directory.name + ".json")
        return (
            safe_file(marker, root)
            and marker.stat().st_mtime_ns >= path.stat().st_mtime_ns
        )
    if path.name in {"merged-media.tar.gz", "merged-media.sha256"}:
        marker = directory / "merged-media.sha256"
        archive = directory / "merged-media.tar.gz"
        return (
            safe_file(marker, root)
            and safe_file(archive, root)
            and marker.stat().st_mtime_ns >= archive.stat().st_mtime_ns
        )
    if path.name == "deployment-media.tar.gz":
        # Legacy exports have no marker: verify the newest stream before pruning.
        try:
            before = path.stat()
            with gzip.open(path, "rb") as stream:
                while stream.read(1024 * 1024):
                    pass
            after = path.stat()
            return (before.st_mtime_ns, before.st_size) == (
                after.st_mtime_ns,
                after.st_size,
            )
        except (OSError, EOFError):
            return False
    if path.name.startswith("questions-"):
        try:
            json.loads(path.read_text())
            return True
        except (ValueError, OSError):
            return False
    return all(
        safe_file(directory / name, root)
        for name in (
            "release",
            "server.sha256",
            directory.name + ".dump",
            directory.name + ".deletions.jsonl",
        )
    )


def safe_file(path, root):
    return (
        not any(
            p.is_symlink()
            for p in (path, *path.parents)
            if p != root and root in p.parents
        )
        and path.is_file()
    )


def candidates(root, now=None):
    """Never infer that arbitrary files under .cache are disposable."""
    now = time.time() if now is None else now
    cache = root / ".cache"
    if cache.is_symlink():
        return []
    selected = []
    for folder, pattern in (("ai-quality", "*.log"), ("hook-cleanup", "*.json")):
        files = [p for p in (cache / folder).glob(pattern) if safe_file(p, root)]
        if files:
            newest = max(files, key=lambda p: (p.stat().st_mtime_ns, p.name))
            selected.extend(
                p for p in files if p != newest and p.stat().st_mtime < now - AGE
            )
    # Backup directories can contain partial releases; retain newest per artifact,
    # not just the newest directory (which may contain no media or database).
    groups = {}
    for directory in (cache / "between-backups").glob("*"):
        if (
            not BACKUP_DIR.fullmatch(directory.name)
            or directory.is_symlink()
            or not directory.is_dir()
        ):
            continue
        for path in directory.iterdir():
            if safe_file(path, root) and BACKUP_FILE.fullmatch(path.name):
                key = re.sub(r"^\d{8}T\d{6}Z\.", "", path.name)
                groups.setdefault(key, []).append(path)
    for path in (cache / "curation-backups").glob("questions-*.json"):
        if safe_file(path, root) and re.fullmatch(
            r"questions-\d{8}-\d{6}-\d+\.json", path.name
        ):
            groups.setdefault("curation-questions", []).append(path)
    for files in groups.values():
        newest = max(
            files,
            key=lambda p: (
                p.parent.name if BACKUP_DIR.fullmatch(p.parent.name) else p.name,
                p.name,
            ),
        )
        if len(files) > 1 and completed(newest, root):
            selected.extend(
                p
                for p in files
                if p != newest
                and (
                    p.name.endswith(".tar.gz")
                    or p.name == "merged-media.sha256"
                    or p.stat().st_mtime < now - AGE
                )
            )
    return sorted(selected)


def clean(root):
    cache = root / ".cache"
    if cache.is_symlink():
        return
    cache.mkdir(exist_ok=True)
    lock_path = cache / "cleanup-cache.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        started = time.monotonic()
        removed, errors, total = [], [], 0
        for path in candidates(root):
            try:
                if not safe_file(path, root):
                    continue
                size = path.stat().st_size
                path.unlink()
                removed.append(str(path.relative_to(root)))
                total += size
            except OSError as error:
                errors.append(f"{path.relative_to(root)}: {error}")
        report = cache / "cleanup-cache-last.json"
        if not report.is_symlink():
            report.write_text(
                json.dumps(
                    {
                        "removed": removed,
                        "bytes": total,
                        "errors": errors,
                        "duration_seconds": round(time.monotonic() - started, 3),
                    },
                    indent=2,
                )
                + "\n"
            )


def launch(root):
    subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--root",
            str(root),
            "--worker",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if args.worker:
        clean(args.root.resolve())
    else:
        launch(args.root.resolve())
