"""Additional local release invariants beyond the generic Checkov policies."""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from uuid import UUID


def resources(template):
    for resource in template.get("resources", []):
        yield resource
        nested = resource.get("properties", {}).get("template")
        if nested:
            yield from resources(nested)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(template):
    all_resources = list(resources(template))
    by_type = {}
    for resource in all_resources:
        by_type.setdefault(resource["type"].lower(), []).append(resource)
        if resource["type"].lower() == "microsoft.resources/deployments":
            require(resource["properties"]["mode"] == "Incremental", "Only incremental deployments are allowed")
    require("microsoft.containerregistry/registries" not in by_type, "Shared registry must be existing, never recreated")
    require("microsoft.cognitiveservices/accounts" not in by_type, "Existing Foundry must not be recreated")
    storage = by_type.get("microsoft.storage/storageaccounts", [])
    require(len(storage) == 1, "Exceptions apply to exactly one approved media storage account")
    properties = storage[0]["properties"]
    require(storage[0]["sku"]["name"] == "Standard_LRS", "Storage replication must match the approved pilot exception")
    require(properties["publicNetworkAccess"] == "Disabled", "Media storage public access must be disabled")
    require(properties["networkAcls"] == {"defaultAction": "Deny", "bypass": "None"}, "No trusted-service bypass is approved")
    require(properties["allowSharedKeyAccess"] is False, "Storage keys must remain disabled")
    require(properties["allowBlobPublicAccess"] is False, "Anonymous media must remain disabled")
    require(properties["supportsHttpsTrafficOnly"] and properties["minimumTlsVersion"] == "TLS1_2", "Require storage HTTPS/TLS")
    for database in by_type.get("microsoft.dbforpostgresql/flexibleservers", []):
        props = database["properties"]
        require(props["network"]["publicNetworkAccess"] == "Disabled", "Database must remain private")
        require(props["authConfig"]["passwordAuth"] == "Disabled", "Database password auth must remain disabled")
    require("microsoft.dbforpostgresql/flexibleservers/firewallrules" not in by_type, "No database firewall exceptions")
    for vault in by_type.get("microsoft.keyvault/vaults", []):
        props = vault["properties"]
        require(props["publicNetworkAccess"] == "Disabled", "Vault must remain private")
        require(props["enableRbacAuthorization"] and props["enablePurgeProtection"], "Require vault RBAC/purge protection")
    for environment in by_type.get("microsoft.app/managedenvironments", []):
        require(environment["properties"]["workloadProfiles"] == [{
            "name": "Consumption", "workloadProfileType": "Consumption",
        }], "No Dedicated profiles are approved")
    for endpoint in by_type.get("microsoft.network/privateendpoints", []):
        groups = endpoint["properties"]["privateLinkServiceConnections"][0]["properties"]["groupIds"]
        require(groups in [["blob"], ["vault"]], "No additional/ACA ingress private endpoint is approved")
    for broker in by_type.get("microsoft.servicebus/namespaces", []):
        require(broker["sku"]["name"] == "Basic" and broker["properties"]["disableLocalAuth"], "Broker must use Basic and Entra")
    require(template["parameters"]["activateJobs"]["defaultValue"] is False, "Automatic jobs must require release activation")
    require(template["parameters"]["bootstrapSecretJson"]["type"] == "securestring", "Bootstrap input must be secure")
    return len(all_resources)


def validate_image(image, registry, repository):
    require(re.fullmatch(re.escape(f"{registry}/{repository}@sha256:") + r"[0-9a-f]{64}", image) is not None,
            "Image must use the approved repository and a lowercase sha256 digest, never a mutable tag")


def validate_environment(environment, stage, now):
    if stage == "foundation":
        return
    validate_image(
        environment.get("TACTIQ_DEPLOY_BACKEND_IMAGE", ""),
        "b8e2968c0n2026ca14acr.azurecr.io", "tactiq-3e4d/backend",
    )
    if stage == "bootstrap":
        supplied = json.loads(environment.get("TACTIQ_BOOTSTRAP_SECRETS_JSON", "{}"))
        require(isinstance(supplied, dict), "Bootstrap input must be a secret map")
        require({"foundry-api-key", "microsoft-client-secret"} <= supplied.keys(),
                "Required bootstrap secrets are missing")
        require(not supplied.keys() - {"foundry-api-key", "microsoft-client-secret", "speech-api-key"},
                "Unknown bootstrap secret name")
        require(all(isinstance(value, str) and 1 <= len(value) <= 16384 for value in supplied.values()),
                "Invalid bootstrap secret shape")
        return
    require(stage == "runtime", "Unknown deployment stage")
    validate_image(
        environment.get("TACTIQ_DEPLOY_WEB_IMAGE", ""),
        "b8e2968c0n2026ca14acr.azurecr.io", "tactiq-3e4d/web",
    )
    UUID(environment.get("TACTIQ_MICROSOFT_CLIENT_ID", ""))
    endpoint = urlsplit(environment.get("AZURE_FOUNDRY_PROJECT_ENDPOINT", ""))
    require(endpoint.scheme == "https" and endpoint.username is None and endpoint.password is None
            and endpoint.port in (None, 443) and not endpoint.query and not endpoint.fragment
            and re.fullmatch(r"[a-z0-9-]+\.services\.ai\.azure\.com", endpoint.hostname or "")
            and re.fullmatch(r"/api/projects/[A-Za-z0-9_-]+/?", endpoint.path),
            "Require a credential-free HTTPS Foundry project endpoint")
    require(re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", environment.get("AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME", "")),
            "Require the verified existing model deployment")
    start = int(environment.get("TACTIQ_AI_BUDGET_PERIOD_START", ""))
    end = int(environment.get("TACTIQ_AI_BUDGET_PERIOD_END", ""))
    require(0 <= start <= now < end and end - start <= 31 * 86400,
            "Require an active fixed budget period of at most 31 days")
    path = environment.get("TACTIQ_CV_MODEL_PATH", "")
    require(path.startswith("/") and ".." not in path.split("/") and path.endswith(".onnx"),
            "Require an absolute packaged Linux ONNX model path")
    require(re.fullmatch(r"[0-9a-f]{64}", environment.get("TACTIQ_CV_MODEL_SHA256", "")),
            "Require the packaged model SHA256")
    cidrs = environment.get("TACTIQ_TRUSTED_PROXY_CIDRS", "").split(",")
    require(all(cidr.strip() for cidr in cidrs), "Require verified trusted proxy CIDRs")
    for cidr in cidrs:
        network = ipaddress.ip_network(cidr.strip(), strict=True)
        require(network.prefixlen > 0, "Never trust every forwarded address")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", nargs="?", default=str(Path("infra") / "main.json"))
    parser.add_argument("--stage", choices=("foundation", "bootstrap", "runtime"), default="foundation")
    arguments = parser.parse_args()
    count = validate(json.loads(Path(arguments.template).read_text(encoding="utf-8")))
    validate_environment(os.environ, arguments.stage, int(time.time()))
    print(f"Local release invariants passed across {count} compiled resource declarations.")
    print(f"{arguments.stage} input preflight passed; no Azure calls were made.")
