"""Offline deployment preflight; fixtures contain no real images or credentials."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def validator():
    path = Path(__file__).resolve().parents[2] / "infra" / "scripts" / "validate_template.py"
    spec = importlib.util.spec_from_file_location("tactiq_infra_validation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def environment():
    registry = "b8e2968c0n2026ca14acr.azurecr.io/tactiq-3e4d"
    return {
        "TACTIQ_DEPLOY_BACKEND_IMAGE": f"{registry}/backend@sha256:{'a' * 64}",
        "TACTIQ_DEPLOY_WEB_IMAGE": f"{registry}/web@sha256:{'b' * 64}",
        "TACTIQ_MICROSOFT_CLIENT_ID": "11111111-1111-1111-1111-111111111111",
        "AZURE_FOUNDRY_PROJECT_ENDPOINT": "https://offline.services.ai.azure.com/api/projects/offline",
        "AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME": "offline-model",
        "TACTIQ_AI_BUDGET_PERIOD_START": "100",
        "TACTIQ_AI_BUDGET_PERIOD_END": "10000",
        "TACTIQ_CV_MODEL_PATH": "/opt/models/offline.onnx",
        "TACTIQ_CV_MODEL_SHA256": "c" * 64,
        "TACTIQ_TRUSTED_PROXY_CIDRS": "10.42.0.0/23",
        "TACTIQ_BOOTSTRAP_SECRETS_JSON": json.dumps({
            "foundry-api-key": "offline-key", "microsoft-client-secret": "offline-key",
        }),
    }


def test_all_stages_validate_without_cloud_calls(validator, environment):
    validator.validate_environment({}, "foundation", 1000)
    validator.validate_environment(environment, "bootstrap", 1000)
    validator.validate_environment(environment, "runtime", 1000)


@pytest.mark.parametrize("name,value", [
    ("TACTIQ_DEPLOY_BACKEND_IMAGE", "registry/backend:latest"),
    ("TACTIQ_DEPLOY_WEB_IMAGE", "b8e2968c0n2026ca14acr.azurecr.io/unrelated@sha256:" + "a" * 64),
    ("TACTIQ_DEPLOY_WEB_IMAGE", "b8e2968c0n2026ca14acr.azurecr.io/tactiq-3e4d/web@sha256:" + "A" * 64),
    ("TACTIQ_MICROSOFT_CLIENT_ID", "missing"),
    ("AZURE_FOUNDRY_PROJECT_ENDPOINT", "http://offline.services.ai.azure.com/api/projects/offline"),
    ("AZURE_FOUNDRY_PROJECT_ENDPOINT", "https://offline.services.ai.azure.com.attacker.invalid/api/projects/offline"),
    ("AZURE_FOUNDRY_PROJECT_ENDPOINT", "https://user:secret@offline.services.ai.azure.com/api/projects/offline"),
    ("AZURE_FOUNDRY_PROJECT_ENDPOINT", "https://offline.services.ai.azure.com/api/projects/offline?key=secret"),
    ("AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME", ""),
    ("TACTIQ_AI_BUDGET_PERIOD_START", "2000"),
    ("TACTIQ_AI_BUDGET_PERIOD_END", "1000"),
    ("TACTIQ_AI_BUDGET_PERIOD_END", str(100 + 32 * 86400)),
    ("TACTIQ_CV_MODEL_PATH", ""),
    ("TACTIQ_CV_MODEL_PATH", "/opt/../offline.onnx"),
    ("TACTIQ_CV_MODEL_SHA256", "invalid"),
    ("TACTIQ_TRUSTED_PROXY_CIDRS", ""),
    ("TACTIQ_TRUSTED_PROXY_CIDRS", "0.0.0.0/0"),
    ("TACTIQ_TRUSTED_PROXY_CIDRS", "::/0"),
    ("TACTIQ_TRUSTED_PROXY_CIDRS", "10.42.0.1/23"),
])
def test_runtime_refuses_unsafe_or_missing_inputs(validator, environment, name, value):
    environment[name] = value
    with pytest.raises(ValueError):
        validator.validate_environment(environment, "runtime", 1000)


@pytest.mark.parametrize("value", ["{}", "[]", '{"foundry-api-key":null}', "invalid"])
def test_bootstrap_refuses_invalid_secret_shape_without_echo(validator, environment, value):
    environment["TACTIQ_BOOTSTRAP_SECRETS_JSON"] = value
    with pytest.raises(ValueError) as error:
        validator.validate_environment(environment, "bootstrap", 1000)
    assert "offline-key" not in str(error.value)


@pytest.fixture
def template():
    return {
        "parameters": {
            "activateJobs": {"defaultValue": False},
            "bootstrapSecretJson": {"type": "securestring"},
        },
        "resources": [{
            "type": "Microsoft.Resources/deployments",
            "properties": {"mode": "Incremental", "template": {"resources": [{
                "type": "Microsoft.Storage/storageAccounts",
                "sku": {"name": "Standard_LRS"},
                "properties": {
                    "publicNetworkAccess": "Disabled",
                    "networkAcls": {"defaultAction": "Deny", "bypass": "None"},
                    "allowSharedKeyAccess": False, "allowBlobPublicAccess": False,
                    "supportsHttpsTrafficOnly": True, "minimumTlsVersion": "TLS1_2",
                },
            }]}},
        }],
    }


def test_compiled_validation_walks_nested_modules(validator, template):
    assert validator.validate(template) == 2


@pytest.mark.parametrize("field,value", [
    ("publicNetworkAccess", "Enabled"),
    ("allowSharedKeyAccess", True),
    ("allowBlobPublicAccess", True),
    ("networkAcls", {"defaultAction": "Deny", "bypass": "AzureServices"}),
])
def test_storage_exceptions_do_not_hide_weaker_controls(validator, template, field, value):
    template["resources"][0]["properties"]["template"]["resources"][0]["properties"][field] = value
    with pytest.raises(ValueError):
        validator.validate(template)


def test_no_shared_resource_recreation_or_complete_deployment(validator, template):
    template["resources"][0]["properties"]["mode"] = "Complete"
    with pytest.raises(ValueError, match="incremental"):
        validator.validate(template)
    template["resources"][0]["properties"]["mode"] = "Incremental"
    template["resources"].append({"type": "Microsoft.ContainerRegistry/registries"})
    with pytest.raises(ValueError, match="never recreated"):
        validator.validate(template)
