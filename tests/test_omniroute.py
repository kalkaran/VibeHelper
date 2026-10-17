"""Free delegation must fail before inference when its safety assumptions change."""

import copy
import importlib.util
import io
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "omniroute_helper", ROOT / "templates/vibe_scripts/omniroute.py"
)
OMNI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OMNI)
TEST_MODEL = "openrouter/example/code:free"


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    """Keep mocked attempts and route failures out of the user's runtime."""
    monkeypatch.setattr(OMNI, "root", lambda: tmp_path / "runtime")


@pytest.fixture
def gateway(monkeypatch):
    responses = {
        "/api/auth/login": {},
        "/api/settings": dict(OMNI.POLICY),
        "/api/combos": {"combos": []},
        "/api/model-combo-mappings": {"mappings": []},
        "/api/settings/reasoning-routing-rules": {"rules": []},
        "/api/settings/task-routing": {"enabled": False},
        "/api/fallback/chains": {},
        "/api/settings/feature-flags": {
            "flags": [
                {
                    "key": "OMNIROUTE_EMERGENCY_FALLBACK",
                    "effectiveValue": "false",
                    "requiresRestart": False,
                }
            ]
        },
        "/api/models/alias": {"aliases": {}},
        "/api/usage/provider-limits": {"caches": {}},
        "/api/rate-limits": {"connections": [], "lockouts": []},
        "/api/usage/history": {
            "totalRequests": 0,
            "totalPromptTokens": 0,
            "totalCompletionTokens": 0,
            "byAccount": {},
        },
        "/api/synced-available-models?provider=openrouter": {"models": []},
        "/api/providers": {
            "connections": [
                {
                    "provider": "openrouter",
                    "id": "connection",
                    "isActive": True,
                    "authType": "apikey",
                }
            ]
        },
        "/v1/chat/completions": {
            "model": "example:free",
            "choices": [
                {"finish_reason": "stop", "message": {"content": "Small proposal"}}
            ],
            "usage": {"total_tokens": 12},
        },
    }
    calls = []

    def request(_self, path, body=None, **kwargs):
        calls.append((path, body, kwargs))
        if path == "/api/keys" and body:
            result = responses.get(
                "created_key", {**body, "id": "temporary", "key": "secret-worker"}
            )
            responses["/api/keys"] = {"keys": [copy.deepcopy(result)]}
        elif path == "/api/keys/temporary":
            if kwargs.get("method") == "DELETE":
                result = responses.get("delete_key", {})
            else:
                responses["/api/keys"]["keys"][0].update(
                    responses.get("key_controls", body)
                )
                result = {k: v for k, v in body.items() if k != "allowAutoCombos"}
        else:
            result = responses[path]
        if isinstance(result, Exception):
            raise result
        return copy.deepcopy(result)

    monkeypatch.setattr(OMNI.Client, "request", request)
    monkeypatch.setattr(OMNI, "verified_executable", lambda _: "/verified/omniroute")
    models = [
        {
            "id": "example/code:free",
            "description": "coding",
            "context_length": 131072,
            "pricing": {"prompt": "0", "completion": "0"},
        }
    ]
    responses["/api/providers/connection/models?excludeCustom=true&chatOnly=true"] = {
        "source": "api",
        "models": models,
    }
    monkeypatch.setattr(OMNI, "public_models", lambda: copy.deepcopy(models))
    return (
        OMNI.Client(
            {"password": "private", "api_key": "private", "api_key_id": "worker"}
        ),
        responses,
        calls,
    )


def test_bounded_proposal_requires_review(gateway):
    client, _, calls = gateway
    result = OMNI.ask(client, "Fix this one function; return a diff.")
    assert result["review_required"] is True
    assert result["proposal"] == "Small proposal"
    inference = [call for call in calls if call[0] == "/v1/chat/completions"]
    assert len(inference) == 1
    _, body, options = inference[0]
    assert body["model"] == TEST_MODEL
    assert body["max_tokens"] == 2048 and body["stream"] is False
    assert "tools" not in body and options["timeout"] == 45


@pytest.mark.parametrize(
    "path,value",
    [
        ("/api/settings", {}),
        ("/api/settings", dict(OMNI.POLICY, freeAccessPolicy="off")),
        ("/api/settings", dict(OMNI.POLICY, globalFallbackModel="paid/model")),
        ("/api/combos", {"combos": [{"name": TEST_MODEL}]}),
        ("/api/model-combo-mappings", {"mappings": [{"pattern": "*"}]}),
        ("/api/settings/reasoning-routing-rules", {"rules": [{}]}),
        ("/api/settings/task-routing", {"enabled": True}),
        ("/api/fallback/chains", {"default": ["paid/model"]}),
        ("/api/models/alias", {"aliases": {"openrouter/*": "paid/model"}}),
        (
            "/api/settings/feature-flags",
            {
                "flags": [
                    {
                        "key": "OMNIROUTE_EMERGENCY_FALLBACK",
                        "effectiveValue": "true",
                        "requiresRestart": False,
                    }
                ]
            },
        ),
        ("/api/providers", {"connections": []}),
        ("/api/providers", {"connections": [{"provider": "paid", "id": "x"}]}),
        (
            "/api/providers",
            {
                "connections": [
                    {
                        "provider": "openrouter",
                        "id": "x",
                        "providerSpecificData": {"baseUrl": "https://other.invalid"},
                    }
                ]
            },
        ),
        ("created_key", {}),
    ],
)
def test_changed_routes_never_reach_inference(gateway, path, value):
    client, responses, calls = gateway
    responses[path] = value
    with pytest.raises(OMNI.Unavailable):
        OMNI.ask(client, "Small task")
    assert not any(path == "/v1/chat/completions" for path, _, _ in calls)


@pytest.mark.parametrize("prompt", ["", " " * 10, "x" * (OMNI.MAX_INPUT + 1)])
def test_prompt_budget_fails_without_network(gateway, prompt):
    client, _, calls = gateway
    with pytest.raises(OMNI.Unavailable):
        OMNI.ask(client, prompt)
    assert not calls


@pytest.mark.parametrize(
    "finish,answer",
    [
        ("length", "truncated"),
        ("stop", ""),
        ("stop", "NEEDS_CONTEXT: file missing"),
        ("stop", "x" * (OMNI.MAX_OUTPUT + 1)),
    ],
)
def test_weak_or_incomplete_output_falls_back(gateway, finish, answer):
    client, responses, _ = gateway
    responses["/v1/chat/completions"]["choices"] = [
        {"finish_reason": finish, "message": {"content": answer}}
    ]
    with pytest.raises(OMNI.Unavailable):
        OMNI.ask(client, "Small task")


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("secret prompt"),
        ValueError("secret body"),
        OMNI.urllib.error.HTTPError("url", 429, "secret", {}, None),
        OMNI.urllib.error.HTTPError("url", 401, "secret", {}, None),
    ],
    ids=["timeout", "invalid-json", "rate-limit", "unauthorized"],
)
def test_failure_is_redacted_and_not_retried(monkeypatch, capsys, gateway, error):
    client, responses, calls = gateway
    responses["/v1/chat/completions"] = error
    monkeypatch.setattr(OMNI, "load_config", lambda: client.config)
    monkeypatch.setattr(sys, "argv", ["omniroute.py", "ask", "--task", "edit"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("Small task"))
    assert OMNI.main() == 75
    output = capsys.readouterr()
    assert "secret" not in output.err and output.out == ""
    assert "Continue with the current agent" in output.err
    assert sum(path == "/v1/chat/completions" for path, _, _ in calls) == 1


@pytest.mark.parametrize(
    "price",
    [
        {"prompt": "0", "completion": "0"},
        {"prompt": "0.001", "completion": "0"},
        {"prompt": "0"},
        {"prompt": "0", "completion": "0", "request": "1"},
    ],
)
def test_official_price_must_be_known_and_zero(monkeypatch, price):
    data = json.dumps({"data": [{"id": "openrouter/free", "pricing": price}]}).encode()

    class Opener:
        def open(self, url, timeout):
            assert url == "https://openrouter.ai/api/v1/models" and timeout == 5
            return io.BytesIO(data)

    monkeypatch.setattr(OMNI.urllib.request, "build_opener", lambda *_: Opener())
    if price == {"prompt": "0", "completion": "0"}:
        OMNI.verify_free_price("openrouter/free")
    else:
        with pytest.raises(OMNI.Unavailable):
            OMNI.verify_free_price("openrouter/free")


def test_password_is_chosen_not_generated(monkeypatch):
    monkeypatch.delenv("VIBEHELPER_OMNIROUTE_PASSWORD", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    with pytest.raises(OMNI.Unavailable, match="pending"):
        OMNI.choose_password()
    monkeypatch.setenv("VIBEHELPER_OMNIROUTE_PASSWORD", "chosen-password")
    assert OMNI.choose_password() == "chosen-password"
    monkeypatch.delenv("VIBEHELPER_OMNIROUTE_PASSWORD")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    answers = iter(["chosen-password", "different-password"])
    monkeypatch.setattr(OMNI.getpass, "getpass", lambda _: next(answers))
    with pytest.raises(OMNI.Unavailable, match="did not match"):
        OMNI.choose_password()


def test_config_write_is_private_atomic_and_preserves_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(OMNI, "root", lambda: tmp_path)
    config = {"owner": "VibeHelper-OmniRoute-v1", "password": "existing"}
    OMNI.save_config(config)
    assert OMNI.load_config() == config
    assert (tmp_path / "client.json").stat().st_mode & 0o077 == 0
    monkeypatch.setattr(
        OMNI.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk failure"))
    )
    with pytest.raises(OSError):
        OMNI.save_config({**config, "password": "new"})
    assert OMNI.load_config() == config


@pytest.mark.parametrize("provider", ["CodexHelper", "ClaudeHelper"])
@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
@pytest.mark.parametrize(
    "mode", ["default", "pending", "repo-only", "opt-out", "dry-run"]
)
def test_installer_default_setup_and_skips(tmp_path, provider, platform, mode):
    source = (ROOT / provider / platform / "part1.sh").read_text()
    function = source.split("install_omniroute() {", 1)[1].split("\n}\n", 1)[0]
    script = tmp_path / "exercise.sh"
    script.write_text(
        """set -eu
log() { :; }
warn() { printf '%s\\n' "$*" >&2; }
record_install_failed() { exit 1; }
record_failure() { printf 'recorded failure: %s\\n' "$*" >&2; FINAL_EXIT_CODE=1; }
confirm() { return 0; }
write_file() { mkdir -p "$(dirname "$1")"; printf '%s\\n' "$2" > "$1"; }
python3() { printf '%s\\n' "$*" >> calls; }
"""
        + f'HELPER_ROOT="{ROOT / provider}"\n'
        + f"SKIP_GLOBAL={int(mode == 'repo-only')}\nRUN_OMNIROUTE={int(mode != 'opt-out')}\nDRY_RUN={int(mode == 'dry-run')}\n"
        + (
            'python3() { printf "%s\\n" "$*" >> calls; return 75; }\n'
            if mode == "pending"
            else ""
        )
        + "install_omniroute() {"
        + function
        + "\n}\ninstall_omniroute\n"
    )
    result = subprocess.run(
        ["bash", str(script)],
        cwd=tmp_path,
        input="y\n" if mode in ("default", "pending") else "",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "calls").exists() is (mode in ("default", "pending"))
    label = provider.removesuffix("Helper").lower()
    assert (tmp_path / f"vibe_scripts/{label}/omniroute.py").is_file()
    doc = (tmp_path / f"agent/{label}/omniroute.md").read_text()
    assert (
        "Review before applying" in doc and "No dashboard password is generated" in doc
    )
    assert "preflight automatically" in doc and "report --json" in doc
    assert "ask --task" in doc and "confirm-free" in doc and "revoke-free" in doc
    assert "no fixed model list" in doc
    assert (tmp_path / f"vibe_scripts/{label}/omniroute.py").read_text().strip() == (
        ROOT / "templates/vibe_scripts/omniroute.py"
    ).read_text().strip()
    assert "connect openrouter" in doc and "untrusted proposal" in doc
    assert "RUN_OMNIROUTE=1" in source and "--no-omniroute" in source
    assert "--no-omniroute" in (ROOT / provider / "install.sh").read_text()


@pytest.mark.parametrize(
    "blocked",
    ["cooldown", "disabled", "lockout", "free_daily", "free_rpm", "metadata_failure"],
)
def test_preflight_blocks_without_inference(gateway, blocked):
    client, responses, calls = gateway
    now = OMNI.time.time()
    connection = responses["/api/providers"]["connections"][0]
    if blocked == "cooldown":
        connection["rateLimitedUntil"] = (now + 120) * 1000
    elif blocked == "disabled":
        connection["isActive"] = False
    elif blocked == "lockout":
        responses["/api/rate-limits"]["lockouts"] = [
            {
                "connectionId": "connection",
                "model": "example/code:free",
                "remainingMs": 9000,
            }
        ]
    elif blocked == "metadata_failure":
        responses["/api/usage/provider-limits"] = TimeoutError("secret")
    else:
        responses["/api/usage/provider-limits"]["caches"]["connection"] = {
            "fetchedAt": datetime.now(UTC).isoformat(),
            "quotas": {blocked: {"remaining": 0, "total": 20}},
        }
    with pytest.raises((OMNI.Unavailable, TimeoutError)):
        OMNI.ask(client, "Small task")
    assert not any(path.startswith("/v1/") for path, _, _ in calls)


@pytest.mark.parametrize(
    "kind",
    [
        "unknown",
        "credits_zero",
        "stale",
        "reset",
        "unreported",
        "other_model",
        "other_connection",
    ],
)
def test_preflight_does_not_invent_exhaustion_or_probe_others(gateway, kind):
    client, responses, calls = gateway
    now = OMNI.time.time()
    if kind in ("other_model", "other_connection"):
        responses["/api/rate-limits"]["lockouts"] = [
            {
                "connectionId": "someone_else"
                if kind == "other_connection"
                else "connection",
                "model": "paid/model" if kind == "other_model" else "example/code:free",
                "remainingMs": 9000,
            }
        ]
    elif kind != "unknown":
        responses["/api/usage/provider-limits"]["caches"]["connection"] = {
            "fetchedAt": datetime.fromtimestamp(
                now - (3600 if kind == "stale" else 0), UTC
            ).isoformat(),
            "quotas": {
                "credits" if kind == "credits_zero" else "free_daily": {
                    "remaining": 0,
                    "fractionReported": kind != "unreported",
                    "resetAt": datetime.fromtimestamp(now - 1, UTC).isoformat()
                    if kind == "reset"
                    else None,
                }
            },
        }
    OMNI.ask(client, "Small task")
    assert sum(path == "/v1/chat/completions" for path, _, _ in calls) == 1
    assert not any(
        "refresh=true" in path or path.startswith("/api/usage/connection")
        for path, _, _ in calls
    )


def test_report_is_redacted_cache_only_and_keeps_zero_distinct(gateway):
    client, responses, calls = gateway
    responses["/api/providers"]["connections"][0].update(
        name="main\x1b\n", apiKey="SECRET", providerSpecificData={"key": "SECRET"}
    )
    responses["/api/synced-available-models?provider=openrouter"]["models"] = [
        {"id": "model/free", "apiKey": "SECRET", "context_length": 1000}
    ]
    responses["/api/usage/provider-limits"]["caches"]["connection"] = {
        "fetchedAt": "2026-10-10T12:00:00Z",
        "message": "SECRET",
        "quotas": {
            "free_daily": {"remaining": 0, "total": 20, "apiKey": "SECRET"},
            "tokens": {"fractionReported": False, "remaining": 0},
        },
    }
    result = OMNI.report(client)
    encoded = json.dumps(result)
    assert "SECRET" not in encoded and "\\u001b" not in encoded
    assert result["gateway_usage"]["totalPromptTokens"] == 0
    connection = result["connections"][0]
    assert (
        connection["name"] == "main" and connection["models"][0]["id"] == "model/free"
    )
    assert connection["quota"]["windows"][0]["remaining"] == 0
    assert connection["quota"]["windows"][0]["source"] == "local_free_variant_estimate"
    assert connection["quota"]["windows"][1]["remaining"] is None
    assert connection["rate_limits"]["queued"] is None
    assert all(body is None for path, body, _ in calls if path != "/api/auth/login")
    assert {path for path, _, _ in calls} == {
        "/api/auth/login",
        "/api/providers",
        "/api/usage/history",
        "/api/usage/provider-limits",
        "/api/rate-limits",
        "/api/synced-available-models?provider=openrouter",
    }


def test_report_endpoint_failure_is_partial_and_does_not_leak(gateway, capsys):
    client, responses, _ = gateway
    responses["/api/usage/history"] = ValueError("SECRET")
    result = OMNI.report(client)
    OMNI.print_report(result)
    output = capsys.readouterr().out
    assert "SECRET" not in output and "Partial report" in output
    assert result["gateway_usage"]["totalPromptTokens"] is None
    assert result["connections"][0]["quota"]["fetched_at"] is None


@pytest.mark.parametrize(
    "path,field,value",
    [
        ("/api/usage/history", "byAccount", None),
        ("/api/usage/history", "byAccount", []),
        ("/api/usage/provider-limits", "caches", None),
        ("/api/rate-limits", "connections", [None]),
        ("/api/rate-limits", "lockouts", None),
        ("/api/synced-available-models?provider=openrouter", "models", None),
    ],
)
def test_malformed_report_metadata_is_partial(gateway, path, field, value):
    client, responses, _ = gateway
    responses[path][field] = value
    result = OMNI.report(client)
    assert path in result["unavailable_endpoints"]
    assert len(result["connections"]) == 1


def test_invalid_usage_is_not_zero_and_models_have_scope(gateway):
    client, responses, _ = gateway
    responses["/api/usage/history"]["byAccount"] = {
        "account": {
            "connectionId": "connection",
            "requests": 2,
            "promptTokens": None,
            "completionTokens": 8,
        }
    }
    result = OMNI.report(client)["connections"][0]
    assert result["recorded_tokens"] == {
        "requests": 2,
        "promptTokens": None,
        "completionTokens": 8,
    }
    assert result["models_scope"] == "provider_cache"


def test_explicit_catalog_refresh_is_metadata_only_and_allowlisted(gateway):
    client, responses, calls = gateway
    responses["/api/providers"]["connections"].extend(
        [
            {"id": "unsafe", "provider": "alibaba", "authType": "apikey"},
            {"id": "oauth", "provider": "openrouter", "authType": "oauth"},
        ]
    )
    responses["/api/providers"]["connections"][0]["authType"] = "apikey"
    responses["/api/synced-available-models?provider=alibaba"] = {"models": []}
    path = (
        "/api/providers/connection/models?refresh=true&excludeCustom=true&chatOnly=true"
    )
    responses[path] = {"source": "api", "models": [{"id": "new-model"}]}
    result = OMNI.report(client, refresh_models=True)
    assert result["connections"][0]["models_source"] == "api"
    assert result["connections"][0]["models"][0]["id"] == "new-model"
    assert result["connections"][1]["models_source"] == "unknown"
    assert [p for p, _, _ in calls if "/models?" in p] == [path]
    assert not any(p.startswith("/v1/") for p, _, _ in calls)


@pytest.mark.parametrize("existing", [False, True])
def test_setup_reuses_global_and_preserves_password(
    tmp_path, monkeypatch, gateway, existing
):
    client, _, calls = gateway
    monkeypatch.setattr(OMNI, "root", lambda: tmp_path)
    config = dict(
        client.config,
        owner="VibeHelper-OmniRoute-v1",
        jwt_secret="jwt",
        key_secret="secret",
    )
    if existing:
        OMNI.save_config(config)
    monkeypatch.setenv("VIBEHELPER_OMNIROUTE_PASSWORD", "chosen-password")
    monkeypatch.setattr(
        OMNI.shutil,
        "which",
        lambda name: "/global/omniroute" if name == "omniroute" else None,
    )
    runs = []

    def run(args, **kwargs):
        runs.append(args)
        return subprocess.CompletedProcess(args, 0, OMNI.VERSION + "\n", "")

    monkeypatch.setattr(OMNI.subprocess, "run", run)
    monkeypatch.setattr(OMNI, "listening", lambda: existing)
    monkeypatch.setattr(OMNI, "cli", lambda *_: None)
    monkeypatch.setattr(OMNI, "start", lambda _: client)
    monkeypatch.setattr(OMNI, "disable_cloud_sync", lambda _: None)
    monkeypatch.setattr(OMNI.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        OMNI.Client,
        "request",
        lambda self, path, body=None, **kwargs: (
            {"key": "worker", "id": "worker"}
            if path == "/api/keys"
            else calls.append((path, body, kwargs))
        ),
    )
    monkeypatch.setattr(OMNI.Client, "verify_policy", lambda _: None)
    OMNI.setup()
    saved = OMNI.load_config()
    assert saved["password"] == ("private" if existing else "chosen-password")
    assert saved["executable"] == "/global/omniroute"
    assert runs == [["/global/omniroute", "--version"]]
    assert any(
        path == "/api/settings/feature-flags" and body["value"] == "false"
        for path, body, _ in calls
    )


def test_unattended_setup_installs_runtime_without_inventing_password(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(OMNI, "root", lambda: tmp_path)
    monkeypatch.setattr(OMNI, "listening", lambda: False)
    monkeypatch.setattr(
        OMNI.shutil, "which", lambda name: "/bin/npm" if name == "npm" else None
    )
    monkeypatch.delenv("VIBEHELPER_OMNIROUTE_PASSWORD", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    runs = []

    def run(args, **kwargs):
        runs.append(args)
        package = tmp_path / "runtime/node_modules/omniroute/package.json"
        package.parent.mkdir(parents=True)
        package.write_text(json.dumps({"version": OMNI.VERSION}))

    monkeypatch.setattr(OMNI.subprocess, "run", run)
    with pytest.raises(OMNI.Unavailable, match="pending"):
        OMNI.setup()
    assert len(runs) == 1 and runs[0][1] == "install"
    assert not (tmp_path / "client.json").exists()
    assert not (tmp_path / "data").exists()


def test_unknown_instance_is_not_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(OMNI, "root", lambda: tmp_path)
    monkeypatch.setattr(OMNI, "listening", lambda: True)
    with pytest.raises(OMNI.Unavailable, match="refusing to overwrite"):
        OMNI.setup()
    assert not list(tmp_path.iterdir())


def test_changed_global_version_is_not_started(monkeypatch):
    runs = []

    def run(args, **kwargs):
        runs.append(args)
        return subprocess.CompletedProcess(args, 0, "99.0.0", "")

    monkeypatch.setattr(OMNI.subprocess, "run", run)
    with pytest.raises(OMNI.Unavailable, match="version changed"):
        OMNI.cli({"executable": "/global/omniroute"}, "serve")
    assert runs == [["/global/omniroute", "--version"]]


def test_connect_updates_dashboard_account_without_duplicate(monkeypatch, gateway):
    client, responses, calls = gateway
    responses["/api/providers"]["connections"][0].update(
        name="My dashboard account", authType="apikey"
    )
    monkeypatch.setattr(
        OMNI,
        "load_config",
        lambda: dict(client.config, jwt_secret="jwt", key_secret="secret"),
    )
    monkeypatch.setattr(OMNI, "verified_executable", lambda _: "/global/omniroute")
    monkeypatch.setattr(sys, "argv", ["omniroute.py", "connect", "openrouter"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("provider-secret"))
    runs = []
    monkeypatch.setattr(
        OMNI.subprocess, "run", lambda args, **kwargs: runs.append((args, kwargs))
    )
    assert OMNI.main() == 0
    args, kwargs = runs[0]
    assert args[-2:] == ["--provider-name", "My dashboard account"]
    assert (
        "provider-secret" not in args
        and kwargs["env"]["OMNIROUTE_API_KEY"] == "provider-secret"
    )
    assert not any(path.startswith("/api/keys") for path, _, _ in calls)


def test_failed_password_update_keeps_local_credentials(monkeypatch, gateway):
    client, responses, _ = gateway
    responses["/api/settings"] = OSError("server unavailable")
    monkeypatch.setattr(OMNI, "load_config", lambda: client.config)
    monkeypatch.setattr(OMNI, "choose_password", lambda: "new-password")
    monkeypatch.setattr(sys, "argv", ["omniroute.py", "password"])
    assert OMNI.main() == 75
    assert client.config["password"] == "private"


@pytest.mark.parametrize(
    "task,expected",
    [
        ("code", "new/coder"),
        ("edit", "new/coder"),
        ("summary", "new/mini"),
        ("extract", "new/structured"),
    ],
)
def test_task_ranking_discovers_new_ids(gateway, monkeypatch, task, expected):
    client, responses, calls = gateway
    models = [
        {
            "id": name,
            "context_length": 131072,
            "pricing": {"prompt": "0", "completion": "0"},
        }
        for name in ("new/coder", "new/mini", "new/structured")
    ]
    models[2]["supported_parameters"] = ["response_format"]
    responses["/api/providers/connection/models?excludeCustom=true&chatOnly=true"][
        "models"
    ] = models
    monkeypatch.setattr(OMNI, "public_models", lambda: models)
    assert (
        OMNI.select_route(client, task, "Small task")["model"]
        == "openrouter/" + expected
    )
    assert not any(path.startswith(("/v1/", "/api/keys")) for path, _, _ in calls)


@pytest.mark.parametrize(
    "grant",
    [
        {},
        {"provider": "gemini", "authType": "apikey"},
        {"provider": "groq", "authType": "oauth"},
        {"provider": "groq", "authType": "apikey"},
    ],
)
def test_account_confirmation_scopes_nonzero_catalog_models(gateway, grant):
    client, responses, _ = gateway
    responses["/api/providers"]["connections"][0]["provider"] = "groq"
    client.config["free_accounts"] = {"connection": grant}
    if grant == {"provider": "groq", "authType": "apikey"}:
        route = OMNI.select_route(client, "code", "Small task")
        assert route["model"] == "groq/example/code:free"
        assert route["free_basis"] == "confirmed_free_account"
    else:
        with pytest.raises(OMNI.Unavailable):
            OMNI.select_route(client, "code", "Small task")


@pytest.mark.parametrize(
    "change", ["context", "paid", "static", "warning", "tts", "guard"]
)
def test_unsuitable_catalog_returns_to_main_agent(gateway, monkeypatch, change):
    client, responses, calls = gateway
    catalog = responses[
        "/api/providers/connection/models?excludeCustom=true&chatOnly=true"
    ]
    model = catalog["models"][0]
    if change == "context":
        model.pop("context_length")
    elif change == "paid":
        model["pricing"]["prompt"] = "0.01"
    elif change == "static":
        catalog["source"] = "unverified_source"
    elif change == "warning":
        catalog["warning"] = "cached fallback"
    else:
        model["id"] = "new/" + change
    monkeypatch.setattr(OMNI, "public_models", lambda: catalog["models"])
    with pytest.raises(OMNI.Unavailable):
        OMNI.select_route(client, "summary", "Small task")
    assert not any(path.startswith("/v1/") for path, _, _ in calls)


def test_unrelated_stock_alias_does_not_block(gateway):
    client, responses, _ = gateway
    responses["/api/models/alias"] = {"aliases": {"stock-alias": "unrelated/model"}}
    assert OMNI.ask(client, "Small task")["review_required"]


def test_runtime_drift_blocks_catalog_reads(gateway, monkeypatch):
    client, _, calls = gateway

    def changed(_):
        raise OMNI.Unavailable("version changed")

    monkeypatch.setattr(OMNI, "verified_executable", changed)
    with pytest.raises(OMNI.Unavailable):
        OMNI.select_route(client, "code", "Small task")
    assert not calls


def test_blocked_candidates_share_one_metadata_snapshot(gateway, monkeypatch):
    client, responses, calls = gateway
    models = [
        {
            "id": f"new/coder-{i}",
            "context_length": 131072,
            "pricing": {"prompt": "0", "completion": "0"},
        }
        for i in range(100)
    ]
    responses["/api/providers/connection/models?excludeCustom=true&chatOnly=true"][
        "models"
    ] = models
    responses["/api/rate-limits"]["lockouts"] = [
        {"connectionId": "connection", "model": "*", "remainingMs": 1000}
    ]
    monkeypatch.setattr(OMNI, "public_models", lambda: models)
    with pytest.raises(OMNI.Unavailable):
        OMNI.select_route(client, "code", "Small task")
    for endpoint in (
        "/api/providers",
        "/api/rate-limits",
        "/api/usage/provider-limits",
    ):
        assert sum(path == endpoint for path, _, _ in calls) == 1


@pytest.mark.parametrize("failure", ["timeout", "controls", "answer", "none"])
def test_temporary_key_scoped_verified_and_deleted(gateway, monkeypatch, failure):
    client, responses, calls = gateway
    configs = []
    original_init = OMNI.Client.__init__

    def init(self, config):
        configs.append(config)
        original_init(self, config)

    monkeypatch.setattr(OMNI.Client, "__init__", init)
    if failure == "timeout":
        responses["/v1/chat/completions"] = TimeoutError()
    elif failure == "controls":
        responses["key_controls"] = {"allowAutoCombos": True}
    elif failure == "answer":
        responses["/v1/chat/completions"]["choices"][0]["finish_reason"] = "length"
    if failure == "none":
        OMNI.ask(client, "Small task")
    else:
        with pytest.raises((OMNI.Unavailable, TimeoutError)):
            OMNI.ask(client, "Small task")
    created = next(body for path, body, _ in calls if path == "/api/keys" and body)
    assert created["allowedModels"] == [TEST_MODEL]
    assert (
        created["allowedConnections"] == ["connection"]
        and created["allowedCombos"] == []
    )
    assert 0 < OMNI.timestamp(created["expiresAt"]) - OMNI.time.time() <= 120
    assert calls[-1][0] == "/api/keys/temporary" and calls[-1][2]["method"] == "DELETE"
    assert configs == ([] if failure == "controls" else [{"api_key": "secret-worker"}])


def test_confirmation_and_revocation_are_local(gateway, monkeypatch):
    client, _, _ = gateway
    saved = []
    monkeypatch.setattr(OMNI, "save_config", lambda c: saved.append(copy.deepcopy(c)))
    OMNI.confirm_free_accounts(client, "all")
    assert set(saved[-1]["free_accounts"]) == {"connection"}
    monkeypatch.setattr(OMNI, "load_config", lambda: saved[-1])
    monkeypatch.setattr(sys, "argv", ["omniroute.py", "revoke-free", "all"])
    assert OMNI.main() == 0 and saved[-1]["free_accounts"] == {}


def test_route_rejects_oversized_prompt(gateway, monkeypatch):
    client, _, calls = gateway
    monkeypatch.setattr(OMNI, "load_config", lambda: client.config)
    monkeypatch.setattr(sys, "argv", ["omniroute.py", "route", "--task", "code"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * (OMNI.MAX_INPUT + 1)))
    assert OMNI.main() == 75
    assert not any("/models?" in path for path, _, _ in calls)


def test_cold_catalog_refreshes_metadata_once(gateway):
    client, responses, calls = gateway
    path = "/api/providers/connection/models?excludeCustom=true&chatOnly=true"
    refresh = (
        "/api/providers/connection/models?refresh=true&excludeCustom=true&chatOnly=true"
    )
    responses[refresh] = responses[path]
    responses[path] = {
        "source": "local_catalog",
        "warning": "auto fetch disabled",
        "models": [],
    }
    assert OMNI.select_route(client, "edit", "Small task")["model"] == TEST_MODEL
    assert sum(path == refresh for path, _, _ in calls) == 1
    assert not any(path.startswith("/v1/") for path, _, _ in calls)


@pytest.mark.parametrize("configured", [True, False, None])
def test_cloud_disable_only_without_remote_endpoint(gateway, monkeypatch, configured):
    client, responses, calls = gateway
    responses["/api/settings"].update(
        cloudEnabled=True, cloudConfigured=configured, cloudUrl=None
    )
    original = OMNI.Client.request

    def request(self, path, body=None, **kwargs):
        if path == "/api/sync/cloud":
            calls.append((path, body, kwargs))
            responses["/api/settings"]["cloudEnabled"] = False
            raise OMNI.urllib.error.HTTPError("local", 500, "not configured", {}, None)
        return original(self, path, body, **kwargs)

    monkeypatch.setattr(OMNI.Client, "request", request)
    if configured is False:
        OMNI.disable_cloud_sync(client)
        assert responses["/api/settings"]["cloudEnabled"] is False
    else:
        with pytest.raises(OMNI.Unavailable):
            OMNI.disable_cloud_sync(client)
        assert not any(path == "/api/sync/cloud" for path, _, _ in calls)


def test_managed_environment_excludes_cloud_destinations(monkeypatch):
    monkeypatch.setenv("CLOUD_URL", "https://remote.invalid")
    monkeypatch.setenv("NEXT_PUBLIC_CLOUD_URL", "https://other.invalid")
    env = OMNI.environment(
        {"password": "private", "jwt_secret": "secret", "key_secret": "secret"}
    )
    assert "CLOUD_URL" not in env and "NEXT_PUBLIC_CLOUD_URL" not in env


@pytest.mark.parametrize(
    "metadata,allowed",
    [
        ({"importFreeModelsOnly": True}, True),
        ({"autoFetchModels": False}, True),
        ({"baseUrl": "https://other.invalid"}, False),
        ({"importFreeModelsOnly": "true"}, False),
    ],
)
def test_catalog_flags_are_not_routing_overrides(gateway, metadata, allowed):
    client, responses, _ = gateway
    responses["/api/providers"]["connections"][0]["providerSpecificData"] = metadata
    if allowed:
        assert OMNI.select_route(client, "edit", "Small task")["model"] == TEST_MODEL
    else:
        with pytest.raises(OMNI.Unavailable):
            OMNI.select_route(client, "edit", "Small task")
