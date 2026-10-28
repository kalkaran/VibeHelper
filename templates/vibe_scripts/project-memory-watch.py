"""One filesystem observer per canonical project, leased by agent sessions."""

import argparse
import fcntl
import importlib.util
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path


def memory():
    spec = importlib.util.spec_from_file_location(
        "memory", Path(__file__).with_name("project-memory.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def process(pid):
    result = subprocess.run(
        ["ps", "-o", "ppid=,lstart=,comm=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    )
    parts = result.stdout.strip().split(maxsplit=6)
    if result.returncode or len(parts) < 7:
        return None
    return {
        "pid": pid,
        "parent": int(parts[0]),
        "started": " ".join(parts[1:6]),
        "command": parts[6],
    }


def owner():
    pid = os.getppid()
    while pid > 1:
        info = process(pid)
        if not info:
            break
        if any(
            name in Path(info["command"]).name.lower() for name in ("codex", "claude")
        ):
            return info
        pid = info["parent"]
    raise RuntimeError("No persistent Codex/Claude ancestor found; watcher not started")


def alive(info):
    current = process(info.get("pid", 0)) if info else None
    return bool(current and current["started"] == info.get("started"))


def checked_cache(root):
    cache = root / ".cache/project-memory"
    for name in (
        "",
        "watcher.json",
        "watcher.log",
        "watcher.lock",
        "sessions.json",
        "sessions.lock",
    ):
        if not (cache / name).resolve().is_relative_to(root):
            raise ValueError("Watcher paths must stay inside the project")
    return cache


def status(root):
    module = memory()
    value = module.read_json(checked_cache(root) / "watcher.json")
    value["healthy"] = alive(value.get("process"))
    value["log"] = str(root / ".cache/project-memory/watcher.log")
    return value


def leases(root, update=None):
    module = memory()
    cache = checked_cache(root)
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / "sessions.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = module.read_json(cache / "sessions.json")
        current = {
            key: value
            for key, value in current.items()
            if alive(value["process"])
            and module.read_json(
                root / f"vibe_scripts/{value['provider']}/memory-policy.json"
            ).get("watch", True)
        }
        if update:
            key, value = update
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
        module.write_json(cache / "sessions.json", current)
        return current


def session(root, provider, payload):
    root = root.resolve()
    checked_cache(root)
    module = memory()
    policy = module.read_json(root / f"vibe_scripts/{provider}/memory-policy.json")
    if not policy.get("watch", policy.get("hooks", False)):
        return {"status": "disabled"}
    session_id = payload.get("session_id") or payload.get("thread_id")
    if not session_id:
        return {"status": "unavailable", "error": "Session event has no session ID"}
    key = provider + ":" + str(session_id)
    value = (
        {"provider": provider, "process": owner()}
        if payload["hook_event_name"] == "SessionStart"
        else None
    )
    leases(root, (key, value))
    if value and not status(root)["healthy"]:
        executable = shutil.which("code-review-graph")
        if not executable:
            raise RuntimeError("code-review-graph environment is needed for watchdog")
        with (root / ".cache/project-memory/watcher.log").open("a") as log:
            subprocess.Popen(
                [
                    module.tool_python(executable),
                    str(Path(__file__).with_name("project-memory.py")),
                    "watch",
                    "--root",
                    str(root),
                    "--provider",
                    provider,
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    return {
        "status": "watching" if value else "released",
        "log": str(root / ".cache/project-memory/watcher.log"),
    }


class Pending:
    def __init__(self):
        self.lock = threading.Lock()
        self.paths = {"startup"}
        self.changed = time.monotonic() - 3

    def add(self, name):
        with self.lock:
            self.paths.add(name)
            self.changed = time.monotonic()

    def take(self):
        with self.lock:
            if time.monotonic() - self.changed < 3:
                return []
            paths = sorted(self.paths)
            self.paths.clear()
            return paths


def watch(root, provider):
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    module = memory()
    cache = root / ".cache/project-memory"
    with (cache / "watcher.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        pending = Pending()

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                if event.event_type not in {"created", "modified", "deleted", "moved"}:
                    return
                for path in (event.src_path, getattr(event, "dest_path", "")):
                    if not path:
                        continue
                    name = Path(path).relative_to(root).as_posix()
                    git_change = ".git" in Path(name).parts and (
                        name.endswith(("HEAD", "index")) or "/refs/" in name
                    )
                    if git_change or (
                        module.included(name)
                        and (not event.is_directory or event.event_type != "modified")
                    ):
                        pending.add(name)

        observer = Observer()
        observer.schedule(Handler(), str(root), recursive=True)
        observer.start()
        health = {
            "process": process(os.getpid()),
            "started_at": time.time(),
            "last_successful_refresh": None,
        }
        try:
            while current := leases(root):
                health.update(
                    heartbeat=time.time(),
                    sessions=len(current),
                    pending_changes=sorted(pending.paths),
                    phase="waiting",
                )
                module.write_json(cache / "watcher.json", health)
                batch = pending.take()
                if batch:
                    health.update(phase="refreshing", pending_changes=batch)
                    module.write_json(cache / "watcher.json", health)
                    # One lock in handle() serializes manual and watched refreshes.
                    active = sorted({value["provider"] for value in current.values()})
                    for selected in active:
                        args = argparse.Namespace(
                            action="refresh", provider=selected, architecture=True
                        )
                        try:
                            result = module.handle(root, args)
                            errors = {
                                key: row.get("error")
                                for key, row in result.get("freshness", {}).items()
                                if row.get("error") and row.get("status") != "disabled"
                            }
                            health["errors"] = errors
                            if result.get("status") == "busy" or any(
                                "files changed during refresh" in str(error)
                                for error in errors.values()
                            ):
                                pending.add("retry")
                            elif not errors:
                                health["last_successful_refresh"] = time.time()
                        except (
                            OSError,
                            ValueError,
                            TypeError,
                            KeyError,
                            RuntimeError,
                            subprocess.SubprocessError,
                        ) as error:
                            health["errors"] = {"watcher": str(error)}
                            print(f"Refresh failed: {error}", flush=True)
                time.sleep(0.5)
        finally:
            observer.stop()
            observer.join(timeout=5)
            health.update(phase="stopped", sessions=0, stopped_at=time.time())
            module.write_json(cache / "watcher.json", health)
    return 0
