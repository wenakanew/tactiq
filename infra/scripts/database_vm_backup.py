"""Daily logical backup to private Blob using only the VM managed identity."""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

import requests


def main(verify_restore=False):
    session = requests.Session()
    session.trust_env = False
    response = session.get(
        "http://169.254.169.254/metadata/identity/oauth2/token",
        params={"api-version": "2018-02-01", "resource": "https://storage.azure.com/"},
        headers={"Metadata": "true"}, timeout=20,
    )
    response.raise_for_status()
    token = response.json()["access_token"]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with tempfile.TemporaryDirectory(prefix="tactiq-db-backup-") as root:
        path = Path(root) / "tactiq.dump"
        with path.open("wb") as output:
            subprocess.run(
                ["runuser", "-u", "postgres", "--", "pg_dump", "-Fc", "-d", "tactiq"],
                stdout=output, stderr=subprocess.DEVNULL, check=True,
            )
        with path.open("rb") as content:
            response = session.put(
                f"https://sttactiqprod3e4d.blob.core.windows.net/database-backups/{timestamp}.dump",
                data=content, headers={
                    "Authorization": "Bearer " + token, "x-ms-version": "2023-11-03",
                    "x-ms-blob-type": "BlockBlob", "Content-Type": "application/octet-stream",
                }, timeout=120,
            )
        response.raise_for_status()
        print(f"Private database backup uploaded: {timestamp}, {path.stat().st_size} bytes.")
        if verify_restore:
            response = session.get(response.url, headers={
                "Authorization": "Bearer " + token, "x-ms-version": "2023-11-03",
            }, timeout=120)
            response.raise_for_status()
            downloaded = Path(root) / "downloaded.dump"
            downloaded.write_bytes(response.content)
            if downloaded.read_bytes() != path.read_bytes():
                raise RuntimeError("Downloaded backup differs from uploaded dump")
            database = "tactiq_restore_probe_" + uuid.uuid4().hex[:12]
            subprocess.run(["runuser", "-u", "postgres", "--", "createdb", database], check=True)
            try:
                with downloaded.open("rb") as source:
                    subprocess.run(
                        ["runuser", "-u", "postgres", "--", "pg_restore", "--exit-on-error", "-d", database],
                        stdin=source, stderr=subprocess.DEVNULL, check=True,
                    )
                query = (
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name LIKE 'tactiq_%'"
                )
                result = subprocess.run(
                    ["runuser", "-u", "postgres", "--", "psql", "-At", "-d", database, "-c", query],
                    capture_output=True, text=True, check=True,
                )
                if int(result.stdout.strip()) != 22:
                    raise RuntimeError("Restored application schema is incomplete")
                print("Private Blob download and isolated PostgreSQL restore verified: all 22 application tables.")
            finally:
                subprocess.run(["runuser", "-u", "postgres", "--", "dropdb", database], check=True)


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--verify-restore", action="store_true")
        main(parser.parse_args().verify_restore)
    except Exception as exc:
        print(f"Private database backup failed ({type(exc).__name__}).", file=sys.stderr)
        sys.exit(1)
