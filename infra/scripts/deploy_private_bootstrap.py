"""Secure bootstrap delivery: no credentials in CLI arguments, files or output."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
import json
from pathlib import Path
import subprocess
import time
from urllib.request import Request, urlopen

from dotenv import dotenv_values


AZ = [r"C:\Program Files\Microsoft SDKs\Azure\CLI2\python.exe", "-IBm", "azure.cli"]
SUBSCRIPTION = "b8e29685-f42f-47d8-ac45-31adff4a18cf"
GROUP = "rg-tactiq-prod-3e4d"
APP_OBJECT = "3670161e-4c97-4397-9824-27d6c686907b"
ROOT = Path(__file__).resolve().parents[2]
BASE = f"https://management.azure.com/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}/providers/Microsoft.Resources/deployments/tactiq-bootstrap-release"


def cli(*args):
    result = subprocess.run(AZ + list(args), capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError("Azure CLI prerequisite failed; credential values withheld")
    return json.loads(result.stdout)


def request(url, token, body=None, method=None):
    data = None if body is None else json.dumps(body).encode()
    req = Request(
        url, data=data, method=method,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urlopen(req, timeout=90) as response:
        content = response.read()
        pending = response.headers.get("Location") if response.status == 202 else None
        result = json.loads(content) if content else {}
    deadline = time.monotonic() + 600
    while pending and time.monotonic() < deadline:
        time.sleep(5)
        with urlopen(Request(pending, headers={"Authorization": "Bearer " + token}), timeout=90) as response:
            result = json.load(response)
            if response.status != 202 and result.get("status") not in {"Running", "Accepted"}:
                pending = None
    if pending:
        raise TimeoutError("Azure operation remains pending; inspect existing operation before retrying")
    return result


def main():
    account = cli("account", "show", "-o", "json")
    if account["id"] != SUBSCRIPTION or account["tenantId"] != "84c31ca0-ac3b-4eae-ad11-519d80233e6f":
        raise ValueError("Unexpected deployment account")
    config = dotenv_values(ROOT / ".env")
    foundry = config.get("AZURE_FOUNDRY_API_KEY")
    if not foundry:
        raise ValueError("Existing Foundry credential is missing")
    arm_token = cli("account", "get-access-token", "--resource", "https://management.azure.com/", "-o", "json")["accessToken"]
    graph_token = cli("account", "get-access-token", "--resource", "https://graph.microsoft.com/", "-o", "json")["accessToken"]
    application = request(f"https://graph.microsoft.com/v1.0/applications/{APP_OBJECT}?$select=passwordCredentials,keyCredentials", graph_token)
    if application["passwordCredentials"] or application["keyCredentials"]:
        raise ValueError("App already has a credential; reuse secure bootstrap input rather than creating another")
    parameters = {
        "backendImage": {"value": "b8e2968c0n2026ca14acr.azurecr.io/tactiq-3e4d/backend@sha256:f6268fe44a7e4af99ff4d5b04357e7cb8da623b2a595f9038bb8e37629e7d458"},
        "databaseHost": {"value": "db.tactiq.internal"},
        "postgresAuth": {"value": "password"},
        "bootstrapSecretJson": {"value": '{"foundry-api-key":"preview-only","microsoft-client-certificate":"preview-only"}'},
    }
    body = {"properties": {
        "mode": "Incremental",
        "template": json.loads((ROOT / "infra" / "bootstrap-only.json").read_text(encoding="utf-8")),
        "parameters": parameters,
    }}
    preview = request(BASE + "/whatIf?api-version=2022-09-01", arm_token, body)
    changes = preview.get("properties", preview).get("changes")
    if preview.get("status") != "Succeeded" or not isinstance(changes, list):
        raise RuntimeError("Asynchronous preview requires review before secure input delivery")
    for change in changes:
        if change["changeType"] in {"Ignore", "NoChange"}:
            continue
        resource = change["resourceId"].lower()
        if change["changeType"] != "Create" or not (
            resource.endswith("/jobs/job-bootstrap-tactiq-3e4d")
            or "/microsoft.resources/deployments/tactiq-private-bootstrap" in resource
        ):
            raise ValueError("Unexpected bootstrap preview mutation; no deployment performed")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    now = datetime.now(timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Tactiq confidential client")])
    certificate = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=90))
        .sign(key, hashes.SHA256())
    )
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ) + certificate.public_bytes(serialization.Encoding.PEM)
    request(
        f"https://graph.microsoft.com/v1.0/applications/{APP_OBJECT}", graph_token,
        {"keyCredentials": [{
            "displayName": "Tactiq production pilot", "type": "AsymmetricX509Cert", "usage": "Verify",
            "key": base64.b64encode(certificate.public_bytes(serialization.Encoding.DER)).decode("ascii"),
        }]}, method="PATCH",
    )
    secrets = {"foundry-api-key": foundry, "microsoft-client-certificate": pem.decode("ascii")}
    if config.get("AZURE_SPEECH_API_KEY"):
        secrets["speech-api-key"] = config["AZURE_SPEECH_API_KEY"]
    parameters["bootstrapSecretJson"]["value"] = json.dumps(secrets)
    request(BASE + "?api-version=2022-09-01", arm_token, body, method="PUT")
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        result = request(BASE + "?api-version=2022-09-01", arm_token)
        state = result["properties"]["provisioningState"]
        if state == "Succeeded":
            print("Secure private bootstrap job deployed; Microsoft credential expires in 90 days.")
            return
        if state in {"Failed", "Canceled"}:
            raise RuntimeError("Secure bootstrap deployment failed; no secret values logged")
        time.sleep(5)
    raise TimeoutError("Bootstrap deployment still pending; inspect its existing deployment, do not duplicate")


if __name__ == "__main__":
    main()
