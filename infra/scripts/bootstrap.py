"""Run ONLY in the approved manual job inside the private application subnet."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import sys
import ssl
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import UUID


def validated_inputs(environment):
    host = environment["TACTIQ_BOOTSTRAP_DATABASE_HOST"]
    vault = environment["TACTIQ_BOOTSTRAP_VAULT_URL"]
    login = environment["TACTIQ_BOOTSTRAP_LOGIN"]
    auth = environment.get("TACTIQ_BOOTSTRAP_POSTGRES_AUTH", "managed_identity")
    if auth not in {"managed_identity", "password"}:
        raise ValueError("Invalid bootstrap database authentication")
    if not (re.fullmatch(r"[a-z0-9-]+\.postgres\.database\.azure\.com", host)
            if auth == "managed_identity" else host == "db.tactiq.internal"):
        raise ValueError("Invalid private PostgreSQL hostname")
    if not re.fullmatch(r"https://[a-z0-9-]+\.vault\.azure\.net/", vault):
        raise ValueError("Invalid private Key Vault endpoint")
    if login != "id-tactiq-bootstrap-3e4d":
        raise ValueError("Unexpected bootstrap identity")
    principals = json.loads(environment["TACTIQ_BOOTSTRAP_PRINCIPALS"])
    if not isinstance(principals, list) or len(principals) != 2:
        raise ValueError("Expected API and jobs identities")
    if {p["name"] for p in principals} != {"id-tactiq-api-3e4d", "id-tactiq-jobs-3e4d"}:
        raise ValueError("Unexpected runtime identity")
    for principal in principals:
        UUID(principal["principalId"])
    supplied = json.loads(environment["TACTIQ_BOOTSTRAP_INPUT"])
    identity_keys = {"microsoft-client-secret", "microsoft-client-certificate"}
    allowed = {"foundry-api-key", "speech-api-key"} | identity_keys
    if (not isinstance(supplied, dict) or "foundry-api-key" not in supplied
            or len(identity_keys & supplied.keys()) != 1):
        raise ValueError("Missing required bootstrap secrets")
    if supplied.keys() - allowed:
        raise ValueError("Unknown bootstrap secret")
    if any(not isinstance(value, str) or not 1 <= len(value) <= 16384 for value in supplied.values()):
        raise ValueError("Invalid bootstrap secret")
    return host, vault, login, principals, supplied


def vault_request(vault, token, name, value=None):
    data = None if value is None else json.dumps({
        "value": value, "attributes": {"enabled": True},
    }).encode()
    request = Request(
        f"{vault}secrets/{name}?api-version=7.4", data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="GET" if value is None else "PUT",
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response)["value"]
    except HTTPError as exc:
        if exc.code == 404 and value is None:
            return None
        # Do not print the HTTP body, token, request data or secret value.
        raise RuntimeError(f"Private Key Vault operation failed with HTTP {exc.code}") from None


def main():
    from azure.identity import ManagedIdentityCredential
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    from tactiq.runtime.cloud_store import CloudApplicationStore
    from tactiq.runtime.postgres_access import PostgresAccessStore

    host, vault, login, principals, supplied = validated_inputs(os.environ)
    with ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"]) as credential, TemporaryDirectory() as tls:
        vault_token = credential.get_token("https://vault.azure.net/.default").token
        password = None
        root_certificate = "/etc/ssl/certs/ca-certificates.crt"
        if os.environ.get("TACTIQ_BOOTSTRAP_POSTGRES_AUTH") == "password":
            password = vault_request(vault, vault_token, "postgres-bootstrap-password")
            certificate = vault_request(vault, vault_token, "postgres-ca-pem")
            if not password or not certificate:
                raise ValueError("Private database credentials and CA must already be initialized")
            ssl.create_default_context(cadata=certificate)
            path = Path(tls) / "database-ca.pem"
            path.write_text(certificate, encoding="ascii")
            path.chmod(0o600)
            root_certificate = str(path)
        hmac = vault_request(vault, vault_token, "ip-hmac-key")
        if hmac is None:
            hmac = secrets.token_hex(32)
        if not re.fullmatch(r"[0-9a-f]{64}", hmac):
            raise ValueError("Existing HMAC key is invalid; refusing to rotate it")
        values = {"ip-hmac-key": hmac, **supplied}
        pending = {}
        for name, value in values.items():
            current = vault_request(vault, vault_token, name)
            if current is not None and current != value:
                raise ValueError("Existing secret differs; rotation requires separate approval")
            if current is None:
                pending[name] = value

        def connect(database="tactiq"):
            return psycopg.connect(
                host=host, dbname=database, user=login,
                password=password or credential.get_token("https://ossrdbms-aad.database.windows.net/.default").token,
                sslmode="verify-full", sslrootcert=root_certificate,
                row_factory=dict_row, connect_timeout=10,
            )

        # Entra role mappings are server-wide; the extension lives in postgres.
        with connect("tactiq" if password else "postgres") as connection:
            with connection.transaction():
                connection.execute("SELECT pg_advisory_xact_lock(854917020609)")
                for principal in principals:
                    name, oid = principal["name"], principal["principalId"]
                    if password:
                        role = connection.execute(
                            "SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=%s", (name,),
                        ).fetchone()
                        if role is None or any(role.values()):
                            raise ValueError("Private database runtime roles must exist without elevated privileges")
                        continue
                    exists = connection.execute(
                        "SELECT 1 FROM pg_roles WHERE rolname=%s", (name,),
                    ).fetchone()
                    if exists:
                        # Verify an existing mapping before granting it data access.
                        mapped = connection.execute(
                            "SELECT to_jsonb(p) AS principal FROM pg_catalog.pgaadauth_list_principals(false) p "
                            "WHERE rolename=%s", (name,),
                        ).fetchone()
                        details = {} if mapped is None else {
                            key.lower(): value for key, value in mapped["principal"].items()
                        }
                        if (str(details.get("objectid", "")).lower() != oid.lower()
                                or details.get("principaltype") != "service" or details.get("isadmin") != 0):
                            raise ValueError("Existing database principal mapping differs")
                    else:
                        connection.execute(
                            "SELECT * FROM pg_catalog.pgaadauth_create_principal_with_oid(%s,%s,'service',false,false)",
                            (name, oid),
                        )

        CloudApplicationStore(connect).migrate()
        PostgresAccessStore(connect, ip_hmac_key=bytes.fromhex(hmac)).migrate()
        with connect() as connection:
            with connection.transaction():
                connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
                connection.execute("REVOKE CONNECT ON DATABASE tactiq FROM PUBLIC")
                for principal in principals:
                    role = sql.Identifier(principal["name"])
                    for statement in (
                        "GRANT CONNECT ON DATABASE tactiq TO {}",
                        "GRANT USAGE ON SCHEMA public TO {}",
                        "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO {}",
                        "GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {}",
                        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO {}",
                        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE,SELECT ON SEQUENCES TO {}",
                    ):
                        connection.execute(sql.SQL(statement).format(role))
        for name, value in pending.items():
            vault_request(vault, vault_token, name, value)
    print("Private bootstrap completed: migrations, principal grants and secret initialization.")
    print("Remove the bootstrap job and revoke its secret-writer/admin privileges before public release.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Private bootstrap failed ({type(exc).__name__}); inspect sanitized dependency health.", file=sys.stderr)
        sys.exit(1)
