"""Check Context7 through Codex: 0 accessible, 10 OAuth required, 1 inconclusive."""

import json
import os
import selectors
import signal
import subprocess
import sys
import time


def check_access(timeout=40):
    process = None
    try:
        servers = subprocess.run(
            ["codex", "mcp", "list", "--json"],
            capture_output=True,
            timeout=10,
            check=True,
        )
        command = ["codex", "app-server", "--stdio"]
        # Keep the user's credentials/config, but do not launch unrelated servers.
        for server in json.loads(servers.stdout):
            if server["name"] != "context7":
                command += [
                    "-c",
                    f"mcp_servers.{server['name']}.enabled=false",
                ]
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        def send(message):
            process.stdin.write(json.dumps(message).encode() + b"\n")
            process.stdin.flush()

        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {
                        "name": "vibehelper_context7_check",
                        "version": "1.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            }
        )
        deadline = time.monotonic() + timeout
        pending = b""
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if not selector.select(max(0, deadline - time.monotonic())):
                    break
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    message = json.loads(line)
                    if message.get("id") not in (1, 2):
                        continue
                    if "error" in message:
                        return 1
                    if message["id"] == 1:
                        send({"method": "initialized"})
                        send({"id": 2, "method": "mcpServerStatus/list", "params": {}})
                        continue
                    result = message["result"]
                    for server in result["data"]:
                        if server.get("name") != "context7":
                            continue
                        error = (server.get("toolsError") or "").lower()
                        if any(
                            text in error
                            for text in (
                                "oauth authorization required",
                                "requires oauth reauthentication",
                                "reauthentication required",
                                "auth required",
                            )
                        ):
                            return 10
                        tools = server.get("tools") or {}
                        if (
                            not error
                            and "resolve-library-id" in tools
                            and "query-docs" in tools
                        ):
                            return 0
                        return 1
                    if not result.get("nextCursor"):
                        return 1
                    send(
                        {
                            "id": 2,
                            "method": "mcpServerStatus/list",
                            "params": {"cursor": result["nextCursor"]},
                        }
                    )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        subprocess.SubprocessError,
    ):
        # Never print upstream errors or configuration: they may contain credentials.
        return 1
    finally:
        if process is not None:
            # Keep group cleanup for MCP children; sandbox policies may deny it.
            try:
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(process.pid, sig)
                    except ProcessLookupError:
                        pass
                    except PermissionError:
                        try:
                            process.send_signal(sig)
                        except (ProcessLookupError, PermissionError):
                            pass
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                process.stdin.close()
                process.stdout.close()
            if process.poll() is None:
                print(
                    "Context7 probe cleanup could not stop the Codex process.",
                    file=sys.stderr,
                )
    return 1


if __name__ == "__main__":
    sys.exit(check_access())
