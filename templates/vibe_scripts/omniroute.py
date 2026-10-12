#!/usr/bin/env python3
"""Headless, bounded delegation to a private OmniRoute free-only instance."""

import argparse
import fnmatch
import getpass
import http.cookiejar
import json
import math
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

VERSION = "3.8.51"
PORT = 20130
BASE_URL = f"http://127.0.0.1:{PORT}"
MAX_INPUT = 12000
MAX_OUTPUT = 12000
# Only these API-key model-list paths were audited for no generation in VERSION.
MODEL_DISCOVERY = {
    "gemini",
    "groq",
    "nvidia",
    "openrouter",
    "cloudflare-ai",
    "kilo-gateway",
    "sambanova",
}
TASKS = ("code", "edit", "summary", "extract")
POLICY = {
    "freeAccessPolicy": "strict",
    "hidePaidModels": True,
    "excludeTosAvoid": True,
    "cloudEnabled": False,
}
SYSTEM = """Complete only the bounded task supplied. Return a minimal unified diff or
concise answer, followed by assumptions and suggested checks (at most five lines).
Use existing project patterns. No new dependencies, speculative abstractions,
unrequested refactoring, placeholders, or weakening tests/security/validation.
If context is insufficient, say NEEDS_CONTEXT and identify the missing fact.
Your output is an untrusted proposal for the supervising agent to review.
Do not claim to have run commands or tests; you have no tools or filesystem access."""


class Unavailable(Exception):
    """The main agent should continue the task itself."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Unavailable("Local gateway redirected the request.")


def root():
    return Path.home() / ".local/share/vibehelper/omniroute"


def private_dir(path):
    if path.is_symlink():
        raise Unavailable("Managed runtime directory must not be a symlink.")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def save_config(config):
    path = root() / "client.json"
    if path.is_symlink():
        raise Unavailable("Managed client config must not be a symlink.")
    fd, temporary = tempfile.mkstemp(dir=root(), prefix=".client-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(config, stream)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_config():
    path = root() / "client.json"
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise Unavailable("Client config must be private (mode 600), without symlinks.")
    config = json.loads(path.read_text())
    if config.get("owner") != "VibeHelper-OmniRoute-v1":
        raise Unavailable("Refusing an unrecognized client configuration.")
    return config


def choose_password():
    password = os.environ.get("VIBEHELPER_OMNIROUTE_PASSWORD")
    if password is None:
        if not sys.stdin.isatty():
            raise Unavailable(
                "Runtime installed; dashboard setup is pending. Run setup in a terminal "
                "to choose your password, or supply VIBEHELPER_OMNIROUTE_PASSWORD"
            )
        password = getpass.getpass("Choose your OmniRoute dashboard password: ")
        if password != getpass.getpass("Confirm dashboard password: "):
            raise Unavailable("Passwords did not match; nothing was changed.")
    if len(password) < 8:
        raise Unavailable("Choose a dashboard password of at least 8 characters.")
    return password


def environment(config):
    # Keep credentials and remote contexts from another OmniRoute out of this instance.
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("OMNIROUTE_")
        and k not in ("CLOUD_URL", "NEXT_PUBLIC_CLOUD_URL")
    }
    env.update(
        DATA_DIR=str(root() / "data"),
        OMNIROUTE_DATA_DIR=str(root() / "data"),
        OMNIROUTE_SERVER_HOST="127.0.0.1",
        OMNIROUTE_EMERGENCY_FALLBACK="false",
        PORT=str(PORT),
        DASHBOARD_PORT=str(PORT),
        API_PORT=str(PORT),
        INITIAL_PASSWORD=config["password"],
        JWT_SECRET=config["jwt_secret"],
        API_KEY_SECRET=config["key_secret"],
        REQUIRE_API_KEY="true",
        AUTH_COOKIE_SECURE="false",
    )
    return env


def verified_executable(config):
    executable = config.get(
        "executable", str(root() / "runtime/node_modules/.bin/omniroute")
    )
    result = subprocess.run(
        [executable, "--version"],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    if result.stdout.strip().lstrip("v") != VERSION:
        raise Unavailable("OmniRoute version changed; review it before upgrading.")
    return executable


def cli(config, *args, timeout=60):
    executable = verified_executable(config)
    subprocess.run(
        [str(executable), *args],
        env=environment(config),
        cwd=root(),
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    )


class Client:
    def __init__(self, config):
        self.config = config
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            NoRedirect(),
        )

    def request(self, path, body=None, method=None, timeout=3, max_bytes=262144):
        headers = {"Content-Type": "application/json"}
        if path.startswith("/v1/"):
            headers["Authorization"] = "Bearer " + self.config["api_key"]
        request = urllib.request.Request(
            BASE_URL + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        with self.opener.open(request, timeout=timeout) as response:
            data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise Unavailable("Gateway response exceeded the size limit.")
        return json.loads(data)

    def login(self):
        self.request("/api/auth/login", {"password": self.config["password"]})

    def verify_policy(self, model=None):
        settings = self.request("/api/settings")
        if not isinstance(settings, dict) or any(
            settings.get(key) != value for key, value in POLICY.items()
        ):
            raise Unavailable("Free-only policy is missing or changed; run setup.")
        if any(
            settings.get(key)
            for key in ("globalFallbackModel", "modelAliases", "wildcardAliases")
        ):
            raise Unavailable("Gateway routing overrides are enabled.")
        for path, field in (
            ("/api/combos", "combos"),
            ("/api/model-combo-mappings", "mappings"),
            ("/api/settings/reasoning-routing-rules", "rules"),
        ):
            if self.request(path).get(field) != []:
                raise Unavailable("Gateway routing rules are present or unverifiable.")
        if self.request("/api/settings/task-routing").get("enabled") is not False:
            raise Unavailable("Gateway task routing must be disabled.")
        if self.request("/api/fallback/chains") != {}:
            raise Unavailable("Gateway fallback chains must be empty.")
        flags = self.request("/api/settings/feature-flags")["flags"]
        fallback = next(
            (
                flag
                for flag in flags
                if flag.get("key") == "OMNIROUTE_EMERGENCY_FALLBACK"
            ),
            {},
        )
        if (
            fallback.get("effectiveValue") != "false"
            or fallback.get("requiresRestart") is not False
        ):
            raise Unavailable(
                "Emergency fallback is enabled or unverifiable; run setup."
            )
        aliases = self.request("/api/models/alias")["aliases"]
        if not isinstance(aliases, dict) or any(
            fnmatch.fnmatchcase(name, pattern)
            for pattern in aliases
            for name in ((model, model.split("/", 1)[-1]) if model else ())
        ):
            raise Unavailable("Free router has a model alias override.")

    def connection(self):
        connections = self.request("/api/providers")["connections"]
        active = [
            c
            for c in connections
            if c.get("isActive") is not False and c.get("provider") == "openrouter"
        ]
        if len(active) != 1:
            raise Unavailable(
                "Connect one OpenRouter account for the dedicated free worker."
            )
        connection = active[0]
        if custom_routing(connection):
            raise Unavailable(
                "Custom provider routing is not allowed for the free worker."
            )
        return connection["id"]


def custom_routing(connection):
    metadata = connection.get("providerSpecificData") or {}
    return (
        bool(connection.get("baseUrl"))
        or not isinstance(metadata, dict)
        or any(
            key not in ("importFreeModelsOnly", "autoFetchModels")
            or type(value) is not bool
            for key, value in metadata.items()
        )
    )


def public_models():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open("https://openrouter.ai/api/v1/models", timeout=5) as response:
        data = response.read(2097153)
    if len(data) > 2097152:
        raise Unavailable("Free model price catalog exceeded the size limit.")
    return json.loads(data)["data"]


def zero_price(model):
    prices = model.get("pricing")
    return (
        isinstance(prices, dict)
        and all(prices.get(key) in ("0", 0) for key in ("prompt", "completion"))
        and all(value in ("0", 0) for value in prices.values())
    )


def verify_free_price(model_id):
    if any(
        model.get("id") == model_id and zero_price(model) for model in public_models()
    ):
        return
    raise Unavailable("Free router price is missing, unknown or nonzero.")


def confirm_free_accounts(client, connection_id):
    client.login()
    connections = client.request("/api/providers")["connections"]
    selected = [
        c for c in connections if connection_id == "all" or c["id"] == connection_id
    ]
    if not selected:
        raise Unavailable("No matching connection to authorize.")
    grants = client.config.setdefault("free_accounts", {})
    for connection in selected:
        grants[connection["id"]] = {
            "provider": connection["provider"],
            "authType": connection.get("authType"),
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
        }
    save_config(client.config)
    print(f"Recorded free-only account confirmation for {len(selected)} connection(s).")


def task_score(task, model):
    """Capability/name hints rank candidates; these are not quality benchmarks."""
    text = " ".join(
        str(model.get(key, "")) for key in ("id", "name", "description")
    ).lower()
    caps = model.get("capabilities") or {}
    params = model.get("supported_parameters") or []
    coding = bool(re.search(r"\b(code|coding|coder|programming)\b", text))
    compact = bool(re.search(r"\b(mini|lite|small|nano|fast)\b", text))
    reasoning = caps.get("reasoning") is True or "reasoning" in params
    structured = caps.get("structured_output") is True or "response_format" in params
    if task == "code":
        return 4 * coding + reasoning
    if task == "edit":
        return 3 * coding + compact
    if task == "extract":
        return 3 * structured + compact - reasoning
    return 2 * compact - reasoning


def select_route(client, task, prompt):
    if not prompt.strip() or len(prompt) > MAX_INPUT:
        raise Unavailable(f"Provide a nonempty task of at most {MAX_INPUT} characters.")
    verified_executable(client.config)
    if task not in TASKS:
        raise Unavailable("Choose a task: code, edit, summary or extract.")
    connections = client.request("/api/providers")["connections"]
    try:
        reference = {
            m["id"]: m
            for m in public_models()
            if isinstance(m, dict) and isinstance(m.get("id"), str)
        }
    except (Unavailable, OSError, ValueError):
        reference = {}
    required = len((SYSTEM + prompt).encode("utf-8")) + 2048
    candidates = []
    for connection in connections:
        provider, connection_id = connection["provider"], connection["id"]
        if (
            connection.get("isActive") is False
            or provider not in MODEL_DISCOVERY
            or connection.get("authType") != "apikey"
        ):
            continue
        grant = client.config.get("free_accounts", {}).get(connection_id, {})
        if provider != "openrouter" and (
            grant.get("provider") != provider or grant.get("authType") != "apikey"
        ):
            continue
        # Connection-level overrides could redirect even an explicit model route.
        if custom_routing(connection):
            continue
        until = timestamp(connection.get("rateLimitedUntil"))
        if until is not None and until > time.time():
            continue
        try:
            catalog = client.request(
                "/api/providers/"
                + urllib.parse.quote(connection_id, safe="")
                + "/models?excludeCustom=true&chatOnly=true",
                timeout=3,
                max_bytes=2097152,
            )
            if catalog.get("source") == "local_catalog":
                catalog = client.request(
                    "/api/providers/"
                    + urllib.parse.quote(connection_id, safe="")
                    + "/models?refresh=true&excludeCustom=true&chatOnly=true",
                    timeout=3,
                    max_bytes=2097152,
                )
            if catalog.get("source") not in ("api", "cache") or catalog.get("warning"):
                continue
            for model in catalog["models"]:
                model_id = model.get("id")
                if not isinstance(model_id, str) or model_id.startswith("openrouter/"):
                    continue
                ref = reference.get(model_id, {})
                if provider in ("openrouter", "kilo-gateway") and not zero_price(
                    ref if provider == "openrouter" else model
                ):
                    continue
                metadata = {**ref, **model}
                # Some provider chat catalogs still include speech and moderation models.
                if re.search(
                    r"(?:^|[/_ -])(tts|audio|image|embedding|moderation|guard|safeguard)(?:$|[/_ -]|\d)",
                    model_id.lower(),
                ):
                    continue
                modalities = (metadata.get("architecture") or {}).get(
                    "output_modalities"
                )
                if modalities is not None and modalities != ["text"]:
                    continue
                context = next(
                    (
                        number(model.get(k))
                        for k in ("context_length", "context_window", "inputTokenLimit")
                        if number(model.get(k)) is not None
                    ),
                    None,
                )
                context_source = "account_catalog"
                if context is None:
                    context = number(ref.get("context_length"))
                    context_source = "same_model_reference"
                if context is None or context < required:
                    continue
                output = number(model.get("outputTokenLimit")) or number(
                    model.get("max_output_tokens")
                )
                if output is not None and output < 2048:
                    continue
                candidates.append(
                    (
                        task_score(task, metadata),
                        {
                            "connection_id": connection_id,
                            "provider": provider,
                            "model": provider + "/" + model_id,
                            "task": task,
                            "context": context,
                            "context_source": context_source,
                            "catalog_source": catalog["source"],
                            "free_basis": "zero_price"
                            if provider in ("openrouter", "kilo-gateway")
                            else "confirmed_free_account",
                        },
                    )
                )
        except (Unavailable, OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    if not candidates:
        raise Unavailable(
            "No suitable discovered model on an eligible account; run report or confirm-free."
        )
    snapshot = (
        connections,
        client.request("/api/rate-limits"),
        client.request("/api/usage/provider-limits"),
    )
    for _, route in sorted(
        candidates,
        key=lambda item: (-item[0], item[1]["model"], item[1]["connection_id"]),
    ):
        try:
            preflight(
                client,
                route["connection_id"],
                route["model"],
                route["provider"],
                snapshot,
            )
            return route
        except Unavailable:
            continue
    raise Unavailable("Suitable routes are currently blocked.")


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def clean_text(value):
    if not isinstance(value, str):
        return None
    return "".join(char for char in value[:500] if char.isprintable())


def timestamp(value):
    if number(value) is not None:
        return value / 1000 if value > 1e12 else value
    if not isinstance(value, str) or not value:
        return None
    try:
        if value.isdigit():
            return timestamp(int(value))
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, OverflowError):
        return None


def quota_snapshot(cache, provider):
    """Project explicit fields only; cache values are observations, not guarantees."""
    cache = cache if isinstance(cache, dict) else {}
    windows = []
    for scope in ("quotas", "modelQuotas"):
        quotas = cache.get(scope) or {}
        if not isinstance(quotas, dict):
            continue
        for name, quota in quotas.items():
            if not isinstance(quota, dict):
                continue
            local_free = provider == "openrouter" and name in ("free_daily", "free_rpm")
            window = {
                "name": clean_text(name),
                "scope": scope,
                "source": "local_free_variant_estimate"
                if local_free
                else clean_text(quota.get("quotaSource")),
                "unit": "requests" if local_free else clean_text(quota.get("currency")),
                "reset_at": clean_text(quota.get("resetAt")),
                "unlimited": quota.get("unlimited") is True,
            }
            for field in ("used", "total", "remaining", "remainingPercentage"):
                window[field] = number(quota.get(field))
            if quota.get("fractionReported") is False:
                window["remaining"] = window["remainingPercentage"] = None
            windows.append(window)
    return {"fetched_at": clean_text(cache.get("fetchedAt")), "windows": windows}


def preflight(client, connection_id, model, provider, snapshot=None):
    """Inspect local metadata for the selected route; never probe inference."""
    now = time.time()
    connections, limits, caches = (
        snapshot
        if snapshot is not None
        else (
            client.request("/api/providers")["connections"],
            client.request("/api/rate-limits"),
            client.request("/api/usage/provider-limits"),
        )
    )
    connection = next((c for c in connections if c.get("id") == connection_id), None)
    if connection is None:
        raise Unavailable("Selected connection was removed.")
    until = timestamp(connection.get("rateLimitedUntil"))
    if connection.get("isActive") is False or (until is not None and until > now):
        raise Unavailable("Selected connection is disabled or cooling down.")
    for lock in limits["lockouts"]:
        if lock.get("connectionId") != connection_id:
            continue
        if lock.get("model") not in (model, model.split("/", 1)[-1], "*", "all", None):
            continue
        remaining = number(lock.get("remainingMs"))
        if remaining is None or remaining > 0:
            raise Unavailable("Selected free model is locked out.")
    cache = caches["caches"].get(connection_id, {})
    snapshot = quota_snapshot(cache, provider)
    fetched = timestamp(snapshot["fetched_at"])
    for quota in snapshot["windows"]:
        if quota["source"] != "local_free_variant_estimate" or fetched is None:
            continue
        max_age = 60 if quota["name"] == "free_rpm" else 300
        reset = timestamp(quota["reset_at"])
        if not 0 <= now - fetched < max_age or (reset is not None and reset <= now):
            continue
        if quota["remaining"] == 0 and not quota["unlimited"]:
            raise Unavailable("Cached free-request window is exhausted.")
    # Unknown quota permits the existing single bounded attempt; it never proves capacity.


def report(client, refresh_models=False):
    client.login()
    errors = []

    def read(path, lists=(), maps=(), **kwargs):
        try:
            result = client.request(path, **kwargs)
            if not isinstance(result, dict) or "error" in result:
                raise ValueError
            for field in lists:
                if not isinstance(result.get(field), list) or not all(
                    isinstance(row, dict) for row in result[field]
                ):
                    raise ValueError
            for field in maps:
                if not isinstance(result.get(field), dict) or not all(
                    isinstance(row, dict) for row in result[field].values()
                ):
                    raise ValueError
            return result
        except (Unavailable, OSError, ValueError):
            errors.append(
                path
            )  # Never include upstream errors, keys or response bodies.
            return {}

    connections = client.request("/api/providers")["connections"]
    usage = read("/api/usage/history", maps=("byAccount",))
    caches = read("/api/usage/provider-limits", maps=("caches",)).get("caches", {})
    limits = read("/api/rate-limits", lists=("connections", "lockouts"))
    result = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "notes": [
            "Catalog entries do not prove account access, free pricing or inference availability.",
            "Tokens are recorded gateway usage; external traffic and unrecorded usage are excluded.",
            "Cached quotas may be stale; null means unknown, not unlimited. Units are not assumed to be tokens.",
            "OpenRouter free windows are local :free estimates and may omit openrouter/free traffic.",
            "Synced catalogs are provider-wide. Explicit refresh lists chat models for the selected connection.",
        ],
        "gateway_usage": {
            field: number(usage.get(field))
            for field in ("totalRequests", "totalPromptTokens", "totalCompletionTokens")
        },
        "connections": [],
        "unavailable_endpoints": errors,
    }
    catalogs = {}
    for connection in connections:
        provider, connection_id = connection["provider"], connection["id"]
        encoded_provider = urllib.parse.quote(provider, safe="")
        if provider not in catalogs:
            catalogs[provider] = read(
                "/api/synced-available-models?provider=" + encoded_provider,
                lists=("models",),
                max_bytes=2097152,
            )
        catalog = catalogs[provider]
        source = "synced_cache" if catalog.get("models") else "unknown"
        scope = "provider_cache"
        if (
            refresh_models
            and connection.get("isActive") is not False
            and connection.get("authType") == "apikey"
            and provider in MODEL_DISCOVERY
        ):
            path = "/api/providers/" + urllib.parse.quote(connection_id, safe="")
            live = read(
                path + "/models?refresh=true&excludeCustom=true&chatOnly=true",
                timeout=15,
                max_bytes=2097152,
                lists=("models",),
            )
            if isinstance(live.get("models"), list):
                catalog = live
                scope = "connection_chat_metadata"
                source = (
                    live.get("source")
                    if live.get("source") in ("api", "cache", "local_catalog")
                    else "unknown"
                )
        models = []
        for model in catalog.get("models", []):
            if isinstance(model, dict) and isinstance(model.get("id"), str):
                models.append(
                    {
                        "id": clean_text(model["id"]),
                        "context_length": number(model.get("context_length")),
                    }
                )
        rows = [
            row
            for row in usage.get("byAccount", {}).values()
            if row.get("connectionId") == connection_id
        ]
        counters = next(
            (
                row
                for row in limits.get("connections", [])
                if row.get("connectionId") == connection_id
            ),
            {},
        )
        result["connections"].append(
            {
                "id": clean_text(connection_id),
                "provider": clean_text(provider),
                "name": clean_text(connection.get("name")),
                "enabled": connection.get("isActive") is not False,
                "stored_test_status": clean_text(connection.get("testStatus")),
                "models_source": source,
                "models_scope": scope,
                "models": models,
                "recorded_tokens": {
                    field: sum(row[field] for row in rows)
                    if "byAccount" in usage
                    and all(number(row.get(field)) is not None for row in rows)
                    else None
                    for field in ("requests", "promptTokens", "completionTokens")
                },
                "quota": quota_snapshot(caches.get(connection_id), provider),
                "rate_limits": {
                    "cooldown_until": timestamp(connection.get("rateLimitedUntil")),
                    "queued": number(counters.get("queued")),
                    "running": number(counters.get("running")),
                    "model_lockouts": [
                        {
                            "model": clean_text(lock.get("model")),
                            "remaining_ms": number(lock.get("remainingMs")),
                        }
                        for lock in limits.get("lockouts", [])
                        if lock.get("connectionId") == connection_id
                    ],
                },
            }
        )
    return result


def print_report(result):
    def show(value):
        return "unknown" if value is None else str(value)

    print("OmniRoute inventory at " + result["collected_at"])
    for note in result["notes"]:
        print(note)
    print("Recorded gateway usage: " + json.dumps(result["gateway_usage"]))
    for connection in result["connections"]:
        print(f"\n{connection['provider']} / {connection['name']} ({connection['id']})")
        print(
            f"  Enabled: {connection['enabled']}; stored test status: {show(connection['stored_test_status'])}"
        )
        print(
            "  Recorded connection usage: " + json.dumps(connection["recorded_tokens"])
        )
        print("  Local rate status: " + json.dumps(connection["rate_limits"]))
        quota = connection["quota"]
        print("  Quota snapshot: " + show(quota["fetched_at"]))
        for window in quota["windows"]:
            print("    " + json.dumps(window))
        if not quota["windows"]:
            print("    Remaining tokens and provider rate limits: unknown")
        print(
            f"  Models ({connection['models_source']}, {connection['models_scope']}): {len(connection['models'])} listed"
        )
        for model in connection["models"]:
            print("    " + model["id"])
    if result["unavailable_endpoints"]:
        print(
            "Partial report; unavailable metadata: "
            + ", ".join(result["unavailable_endpoints"])
        )


def listening():
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", PORT)) == 0


def start(config):
    client = Client(config)
    if listening():
        # Authentication must establish ownership before any settings are changed.
        client.login()
        return client
    cli(config, "serve", "--daemon", "--no-open", "--no-tray", "--port", str(PORT))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            client.login()
            return client
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    raise Unavailable("Local gateway did not become ready within 30 seconds.")


def disable_cloud_sync(client):
    settings = client.request("/api/settings")
    if settings.get("cloudEnabled") is False:
        return
    if settings.get("cloudConfigured") is not False or settings.get("cloudUrl"):
        raise Unavailable(
            "Cloud sync is configured; disable it in the dashboard before using this local worker."
        )
    # With no cloud endpoint this route only sets the local flag, then returns 500.
    try:
        client.request("/api/sync/cloud", {"action": "disable"})
    except urllib.error.HTTPError as exc:
        if exc.code != 500:
            raise
    if client.request("/api/settings").get("cloudEnabled") is not False:
        raise Unavailable("Cloud sync could not be disabled locally.")


def setup():
    private_dir(root())
    path = root() / "client.json"
    config = None
    if path.exists() or path.is_symlink():
        config = load_config()
    else:
        if listening() or (root() / "data").exists():
            raise Unavailable(
                "Port or data directory already in use; refusing to overwrite it."
            )
    runtime = root() / "runtime"
    private_dir(runtime)
    package = runtime / "node_modules/omniroute/package.json"
    executable = str(runtime / "node_modules/.bin/omniroute")
    global_executable = shutil.which("omniroute")
    if not package.exists() and global_executable:
        result = subprocess.run(
            [global_executable, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip().lstrip("v") == VERSION:
            executable = global_executable
            print("Reusing compatible OmniRoute installation at " + executable)
        else:
            print(
                "Existing OmniRoute version is not verified; preserving it and installing the pinned runtime."
            )
    if not package.exists() and executable != global_executable:
        npm = shutil.which("npm")
        if not npm:
            raise Unavailable("npm is required to install OmniRoute.")
        print(f"Installing OmniRoute {VERSION} in {runtime}.", flush=True)
        subprocess.run(
            [
                npm,
                "install",
                "--prefix",
                str(runtime),
                "--no-audit",
                "--no-fund",
                f"omniroute@{VERSION}",
            ],
            cwd=root(),
            check=True,
            timeout=900,
        )
    if (
        executable != global_executable
        and json.loads(package.read_text()).get("version") != VERSION
    ):
        raise Unavailable(
            "Managed runtime version differs; review it before upgrading."
        )
    if config is None:
        config = {
            "owner": "VibeHelper-OmniRoute-v1",
            "password": choose_password(),
            "jwt_secret": secrets.token_hex(32),
            "key_secret": secrets.token_hex(32),
        }
        save_config(config)
    config["executable"] = executable
    save_config(config)
    private_dir(root() / "data")
    if not listening():
        cli(config, "setup", "--non-interactive")
    client = start(config)
    disable_cloud_sync(client)
    client.request(
        "/api/settings",
        {k: v for k, v in POLICY.items() if k != "cloudEnabled"},
        method="PATCH",
    )
    client.request(
        "/api/settings/feature-flags",
        {
            "key": "OMNIROUTE_EMERGENCY_FALLBACK",
            "value": "false",
        },
        method="PUT",
    )
    # The live emergency-fallback resolver caches its effective flag for 500ms.
    time.sleep(0.6)
    client.verify_policy()
    print("Configured a private free-only gateway at " + BASE_URL)
    print(
        "A configured service does not guarantee an eligible free provider. Run status."
    )


def ask(client, prompt, task="edit"):
    if not prompt.strip() or len(prompt) > MAX_INPUT:
        raise Unavailable(f"Provide a nonempty task of at most {MAX_INPUT} characters.")
    client.login()
    client.verify_policy()
    route = select_route(client, task, prompt)
    client.verify_policy(route["model"])
    expires = (
        datetime.fromtimestamp(time.time() + 120, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    restriction = {
        "name": "VibeHelper bounded task",
        "noLog": True,
        "modelAccessMode": "restricted",
        "allowedModels": [route["model"]],
        "allowedConnections": [route["connection_id"]],
        "allowedCombos": [],
        "expiresAt": expires,
    }
    key = client.request("/api/keys", restriction)
    if not isinstance(key, dict) or not isinstance(key.get("id"), str) or not key["id"]:
        raise Unavailable("Gateway did not return a temporary key identifier.")
    key_path = "/api/keys/" + urllib.parse.quote(key["id"], safe="")
    try:
        if any(key.get(field) != value for field, value in restriction.items()):
            raise Unavailable("Temporary key restrictions were not applied.")
        controls = {
            "compressionEnabled": False,
            "autoResolve": False,
            "cacheDefaultMode": "bypass",
            "allowAutoCombos": False,
        }
        client.request(key_path, controls, method="PATCH")
        configured = next(
            (
                item
                for item in client.request("/api/keys")["keys"]
                if item.get("id") == key["id"]
            ),
            {},
        )
        if any(
            configured.get(field) != value
            for field, value in {**restriction, **controls}.items()
        ):
            raise Unavailable("Temporary key routing controls were not applied.")
        preflight(client, route["connection_id"], route["model"], route["provider"])
        # This new client has no dashboard session cookie; only the restricted bearer.
        inference = Client({"api_key": key["key"]})
        result = inference.request(
            "/v1/chat/completions",
            {
                "model": route["model"],
                "stream": False,
                "max_tokens": 2048,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=45,
        )
    finally:
        try:
            client.request(key_path, method="DELETE")
        except (Unavailable, OSError, ValueError):
            print(
                "Temporary worker key cleanup failed; its expiry remains set.",
                file=sys.stderr,
            )
    choice = result["choices"][0]
    answer = choice["message"].get("content")
    if (
        choice.get("finish_reason") != "stop"
        or not isinstance(answer, str)
        or not answer.strip()
    ):
        raise Unavailable(
            "Gateway returned an incomplete answer; main agent should continue."
        )
    if len(answer) > MAX_OUTPUT or answer.lstrip().startswith("NEEDS_CONTEXT"):
        raise Unavailable("Proposal exceeds the output budget or needs more context.")
    return {
        "review_required": True,
        "selected_route": route,
        "model": result.get("model"),
        "usage": result.get("usage"),
        "proposal": answer,
    }


def timed_out(_signum, _frame):
    raise Unavailable("Delegation exceeded the 60-second wall-clock limit.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "setup",
            "start",
            "status",
            "ask",
            "connect",
            "password",
            "report",
            "route",
            "confirm-free",
            "revoke-free",
        ),
    )
    parser.add_argument(
        "provider", nargs="?", help="Provider ID for connect; API key comes from stdin"
    )
    parser.add_argument("--json", action="store_true", help="Report as JSON")
    parser.add_argument("--task", choices=TASKS, help="Task type for ask/route")
    parser.add_argument(
        "--billing-disabled",
        action="store_true",
        help="Confirm the selected accounts have free-only usage and no paid billing",
    )
    parser.add_argument(
        "--refresh-models",
        action="store_true",
        help="Report: refresh audited API-key model catalogs (metadata only)",
    )
    args = parser.parse_args()
    try:
        if args.command == "setup":
            setup()
            return 0
        config = load_config()
        if args.command == "confirm-free":
            if not args.billing_disabled or not args.provider:
                raise Unavailable(
                    "Use confirm-free CONNECTION_ID (or all) --billing-disabled only for confirmed free accounts."
                )
            confirm_free_accounts(Client(config), args.provider)
        elif args.command == "revoke-free":
            if not args.provider:
                raise Unavailable("revoke-free requires a connection ID or all.")
            grants = config.setdefault("free_accounts", {})
            if args.provider == "all":
                grants.clear()
            else:
                grants.pop(args.provider, None)
            save_config(config)
            print("Free-account confirmation removed.")
        elif args.command == "route":
            if not args.task:
                raise Unavailable("route requires --task.")
            signal.signal(signal.SIGALRM, timed_out)
            signal.alarm(60)
            try:
                client = Client(config)
                client.login()
                client.verify_policy()
                print(
                    json.dumps(
                        select_route(client, args.task, sys.stdin.read(MAX_INPUT + 1))
                    )
                )
            finally:
                signal.alarm(0)
        elif args.command == "report":
            if args.refresh_models:
                verified_executable(config)
            result = report(Client(config), args.refresh_models)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print_report(result)
        elif args.command == "password":
            password = choose_password()
            client = Client(config)
            client.login()
            client.request(
                "/api/settings",
                {"currentPassword": config["password"], "newPassword": password},
                method="PATCH",
            )
            config["password"] = password
            save_config(config)
            print("Dashboard password updated. Log in at " + BASE_URL)
        elif args.command == "start":
            start(config).verify_policy()
            print("Free-only gateway started.")
        elif args.command == "connect":
            if args.provider != "openrouter":
                raise Unavailable("The free worker supports connect openrouter only.")
            client = Client(config)
            client.login()
            client.verify_policy()
            connections = client.request("/api/providers")["connections"]
            connections = [c for c in connections if c.get("provider") == "openrouter"]
            if len(connections) > 1 or any(
                c.get("authType") != "apikey" or not c.get("name") or custom_routing(c)
                for c in connections
            ):
                raise Unavailable(
                    "Existing accounts cannot be safely updated; review them in the dashboard."
                )
            name = connections[0]["name"] if connections else "VibeHelper OpenRouter"
            key = (
                getpass.getpass("OpenRouter API key: ")
                if sys.stdin.isatty()
                else sys.stdin.read(8193).strip()
            )
            if not key or len(key) > 8192:
                raise Unavailable("Provide a provider API key on stdin.")
            env = environment(config)
            env["OMNIROUTE_API_KEY"] = key
            executable = verified_executable(config)
            subprocess.run(
                [
                    str(executable),
                    "setup",
                    "--non-interactive",
                    "--add-provider",
                    "--provider",
                    args.provider,
                    "--provider-name",
                    name,
                ],
                env=env,
                cwd=root(),
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )
            client.verify_policy()
            client.connection()
            print(
                "OpenRouter connected. Task routing discovers eligible zero-priced models."
            )
        else:
            client = Client(config)
            if args.command == "ask":
                if not args.task:
                    raise Unavailable(
                        "ask requires --task code, edit, summary or extract."
                    )
                signal.signal(signal.SIGALRM, timed_out)
                signal.alarm(60)
                try:
                    print(
                        json.dumps(
                            ask(client, sys.stdin.read(MAX_INPUT + 1), args.task)
                        )
                    )
                finally:
                    signal.alarm(0)
            else:
                client.login()
                client.verify_policy()
                print(
                    "Gateway policy verified. Use route --task TYPE to inspect task-based selection without inference."
                )
        return 0
    except (
        Unavailable,
        OSError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        AttributeError,
        subprocess.SubprocessError,
    ) as exc:
        # Never echo upstream bodies, CLI output, prompts or secrets on failure.
        detail = str(exc) if isinstance(exc, Unavailable) else type(exc).__name__
        print(
            f"OmniRoute unavailable: {detail}. Continue with the current agent; do not retry this task.",
            file=sys.stderr,
        )
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
