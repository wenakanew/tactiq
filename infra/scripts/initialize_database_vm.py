"""Run as root via Azure Run Command, never via a public database connection."""
from __future__ import annotations

import json
import os
from pathlib import Path
import pwd
import secrets
import subprocess
import sys

import psycopg2
from psycopg2 import sql
import requests


HOST = "db.tactiq.internal"
VAULT = "https://kv-tactiq-prod-3e4d.vault.azure.net/"
ROLES = ("bootstrap", "api", "jobs")


def checked(*command):
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    session = requests.Session()
    session.trust_env = False
    response = session.get(
        "http://169.254.169.254/metadata/identity/oauth2/token",
        params={"api-version": "2018-02-01", "resource": "https://vault.azure.net"},
        headers={"Metadata": "true"}, timeout=20,
    )
    response.raise_for_status()
    session.headers["Authorization"] = "Bearer " + response.json()["access_token"]

    def secret(name, value=None):
        url = f"{VAULT}secrets/{name}?api-version=7.4"
        response = session.get(url, timeout=30) if value is None else session.put(
            url, json={"value": value, "attributes": {"enabled": True}}, timeout=30,
        )
        if value is None and response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()["value"]

    tls = Path("/srv/postgresql/tls")
    tls.mkdir(mode=0o700, exist_ok=True)
    key, cert = tls / "server.key", tls / "server.crt"
    if not key.exists() and not cert.exists():
        checked(
            "openssl", "req", "-x509", "-newkey", "rsa:3072", "-sha256", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "365",
            "-subj", f"/CN={HOST}", "-addext", f"subjectAltName=DNS:{HOST}",
            "-addext", "basicConstraints=critical,CA:TRUE",
        )
    if not key.exists() or not cert.exists():
        raise ValueError("Incomplete database TLS material; refusing to rotate")
    postgres = pwd.getpwnam("postgres")
    for path in (tls, key, cert):
        os.chown(path, postgres.pw_uid, postgres.pw_gid)
    key.chmod(0o600)
    cert.chmod(0o644)
    current_ca = secret("postgres-ca-pem")
    certificate = cert.read_text()
    if current_ca is not None and current_ca != certificate:
        raise ValueError("Existing database CA differs; explicit rotation required")
    if current_ca is None:
        secret("postgres-ca-pem", certificate)

    config = Path("/etc/postgresql/17/tactiq/conf.d/tactiq.conf")
    config.write_text(
        "listen_addresses = '127.0.0.1,10.42.4.4'\nport = 5432\n"
        "max_connections = 32\nshared_buffers = '64MB'\nwork_mem = '2MB'\n"
        "maintenance_work_mem = '32MB'\npassword_encryption = 'scram-sha-256'\n"
        "ssl = on\nssl_min_protocol_version = 'TLSv1.2'\n"
        f"ssl_cert_file = '{cert}'\nssl_key_file = '{key}'\n"
        "log_connections = off\nlog_disconnections = off\n"
        "log_statement = 'none'\nlog_min_error_statement = 'panic'\n"
    )
    names = ",".join(f"id-tactiq-{role}-3e4d" for role in ROLES)
    Path("/etc/postgresql/17/tactiq/pg_hba.conf").write_text(
        "local all postgres peer\nlocal all all peer\n"
        f"hostssl tactiq {names} 10.42.0.0/23 scram-sha-256\n"
        "host all all 0.0.0.0/0 reject\nhost all all ::/0 reject\n"
    )
    checked("systemctl", "restart", "postgresql@17-tactiq")
    passwords = {}
    for role in ROLES:
        name = f"postgres-{role}-password"
        value = secret(name)
        if value is None:
            value = secrets.token_hex(32)
            secret(name, value)
        passwords[role] = value

    os.seteuid(postgres.pw_uid)
    try:
        connection = psycopg2.connect(dbname="postgres", user="postgres")
    finally:
        os.seteuid(0)
    with connection:
        with connection.cursor() as cursor:
            for role in ROLES:
                name = f"id-tactiq-{role}-3e4d"
                cursor.execute("SELECT rolsuper,rolcreaterole FROM pg_roles WHERE rolname=%s", (name,))
                existing = cursor.fetchone()
                if existing and any(existing):
                    raise ValueError("Existing runtime role has unexpected elevated permissions")
                if not existing:
                    cursor.execute(
                        sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE").format(sql.Identifier(name)),
                    )
                cursor.execute(
                    sql.SQL("ALTER ROLE {} PASSWORD %s").format(sql.Identifier(name)), (passwords[role],),
                )
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname='tactiq'")
        if cursor.fetchone() is None:
            cursor.execute('CREATE DATABASE tactiq OWNER "id-tactiq-bootstrap-3e4d"')
    connection.close()
    for role in ("api", "jobs"):
        name = f"postgres-{role}-dsn"
        value = (
            f"host={HOST} dbname=tactiq user=id-tactiq-{role}-3e4d "
            f"password={passwords[role]} sslmode=verify-full"
        )
        current = secret(name)
        if current is not None and current != value:
            raise ValueError("Existing database DSN differs; explicit rotation required")
        if current is None:
            secret(name, value)
    print(json.dumps({"database": HOST, "roles": list(ROLES), "tls": "verify-full", "secrets": "initialized"}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Private database initialization failed ({type(exc).__name__}); no credentials logged.", file=sys.stderr)
        sys.exit(1)
