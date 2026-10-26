"""Offline validation for the private bootstrap helper; no Azure requests."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest


@pytest.fixture
def bootstrap():
    path = Path(__file__).resolve().parents[2] / "infra" / "scripts" / "bootstrap.py"
    spec = importlib.util.spec_from_file_location("tactiq_infra_bootstrap", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def environment():
    return {
        "TACTIQ_BOOTSTRAP_DATABASE_HOST": "psql-tactiq-prod-3e4d.postgres.database.azure.com",
        "TACTIQ_BOOTSTRAP_VAULT_URL": "https://kv-tactiq-prod-3e4d.vault.azure.net/",
        "TACTIQ_BOOTSTRAP_LOGIN": "id-tactiq-bootstrap-3e4d",
        "TACTIQ_BOOTSTRAP_PRINCIPALS": json.dumps([
            {"name": "id-tactiq-api-3e4d", "principalId": "11111111-1111-1111-1111-111111111111"},
            {"name": "id-tactiq-jobs-3e4d", "principalId": "22222222-2222-2222-2222-222222222222"},
        ]),
        "TACTIQ_BOOTSTRAP_INPUT": json.dumps({
            "foundry-api-key": "offline-foundry", "microsoft-client-secret": "offline-microsoft",
        }),
    }


def test_private_bootstrap_inputs_are_bounded_and_explicit(bootstrap, environment):
    host, vault, login, principals, supplied = bootstrap.validated_inputs(environment)
    assert host.endswith(".postgres.database.azure.com")
    assert vault.startswith("https://")
    assert login == "id-tactiq-bootstrap-3e4d"
    assert len(principals) == 2 and "foundry-api-key" in supplied


def test_vm_bootstrap_requires_explicit_auth_and_exact_private_target(bootstrap, environment):
    environment["TACTIQ_BOOTSTRAP_DATABASE_HOST"] = "db.tactiq.internal"
    with pytest.raises(ValueError, match="hostname"):
        bootstrap.validated_inputs(environment)
    environment["TACTIQ_BOOTSTRAP_POSTGRES_AUTH"] = "password"
    assert bootstrap.validated_inputs(environment)[0] == "db.tactiq.internal"
    environment["TACTIQ_BOOTSTRAP_DATABASE_HOST"] = "attacker.internal"
    with pytest.raises(ValueError, match="hostname"):
        bootstrap.validated_inputs(environment)


def test_bootstrap_accepts_certificate_instead_of_secret_but_not_both(bootstrap, environment):
    supplied = {"foundry-api-key": "offline", "microsoft-client-certificate": "offline-pem"}
    environment["TACTIQ_BOOTSTRAP_INPUT"] = json.dumps(supplied)
    assert bootstrap.validated_inputs(environment)[-1] == supplied
    supplied["microsoft-client-secret"] = "ambiguous"
    environment["TACTIQ_BOOTSTRAP_INPUT"] = json.dumps(supplied)
    with pytest.raises(ValueError, match="Missing required"):
        bootstrap.validated_inputs(environment)


@pytest.mark.parametrize("field,value", [
    ("TACTIQ_BOOTSTRAP_DATABASE_HOST", "127.0.0.1"),
    ("TACTIQ_BOOTSTRAP_DATABASE_HOST", "db.postgres.database.azure.com.attacker.invalid"),
    ("TACTIQ_BOOTSTRAP_VAULT_URL", "http://vault.vault.azure.net/"),
    ("TACTIQ_BOOTSTRAP_VAULT_URL", "https://vault.vault.azure.net/secret-path"),
    ("TACTIQ_BOOTSTRAP_LOGIN", "unrelated-administrator"),
    ("TACTIQ_BOOTSTRAP_PRINCIPALS", "[]"),
    ("TACTIQ_BOOTSTRAP_INPUT", "{}"),
    ("TACTIQ_BOOTSTRAP_INPUT", '{"unrelated-secret":"no"}'),
])
def test_bootstrap_rejects_unexpected_targets_and_secret_shapes(bootstrap, environment, field, value):
    environment[field] = value
    with pytest.raises(ValueError):
        bootstrap.validated_inputs(environment)


def test_bootstrap_refuses_unrecognized_secret_and_invalid_identity(bootstrap, environment):
    supplied = json.loads(environment["TACTIQ_BOOTSTRAP_INPUT"])
    supplied["unapproved"] = "offline"
    environment["TACTIQ_BOOTSTRAP_INPUT"] = json.dumps(supplied)
    with pytest.raises(ValueError, match="Unknown"):
        bootstrap.validated_inputs(environment)
    supplied.pop("unapproved")
    environment["TACTIQ_BOOTSTRAP_INPUT"] = json.dumps(supplied)
    principals = json.loads(environment["TACTIQ_BOOTSTRAP_PRINCIPALS"])
    principals[0]["principalId"] = "invalid-object-id"
    environment["TACTIQ_BOOTSTRAP_PRINCIPALS"] = json.dumps(principals)
    with pytest.raises(ValueError):
        bootstrap.validated_inputs(environment)


def test_vault_missing_is_distinct_from_auth_failure_and_error_is_redacted(bootstrap, monkeypatch):
    def missing(*args, **kwargs):
        raise HTTPError("https://private", 404, "missing", {}, None)

    monkeypatch.setattr(bootstrap, "urlopen", missing)
    assert bootstrap.vault_request("https://private/", "TOKEN", "name") is None
    with pytest.raises(RuntimeError, match="HTTP 404"):
        bootstrap.vault_request("https://private/", "TOKEN", "name", "SECRET")

    def forbidden(*args, **kwargs):
        raise HTTPError("https://private", 403, "sensitive response", {}, None)

    monkeypatch.setattr(bootstrap, "urlopen", forbidden)
    with pytest.raises(RuntimeError) as error:
        bootstrap.vault_request("https://private/", "TOKEN", "name")
    assert str(error.value) == "Private Key Vault operation failed with HTTP 403"


def test_vault_uses_bearer_token_without_printing_or_url_embedding(bootstrap, monkeypatch):
    calls = []

    class Response:
        def __enter__(self):
            return SimpleNamespace(read=lambda: b'{"value":"offline"}')

        def __exit__(self, *args):
            pass

    def request(req, timeout):
        calls.append((req, timeout))
        return Response()

    monkeypatch.setattr(bootstrap, "urlopen", request)
    assert bootstrap.vault_request("https://private/", "TOKEN", "name", "SECRET") == "offline"
    req, timeout = calls[0]
    assert req.get_method() == "PUT" and timeout == 20
    assert req.headers["Authorization"] == "Bearer TOKEN"
    assert "TOKEN" not in req.full_url and "SECRET" not in req.full_url
