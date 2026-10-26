"""Preview or explicitly create the two configured Foundry commentary role profiles."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


def _configuration(env_file: Path | None) -> tuple[str, str, list[tuple[str, str, str]]]:
    settings = dict(os.environ)
    if env_file is not None:
        from dotenv import dotenv_values

        if not env_file.is_file():
            raise ValueError("The explicit environment file is not a regular file.")
        for key, value in dotenv_values(env_file).items():
            if value is not None:
                settings.setdefault(key, value)
    endpoint = settings.get("AZURE_FOUNDRY_PROJECT_ENDPOINT", "").strip()
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("Configure a credential-free HTTPS project endpoint.")
    model = settings.get("AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME", "").strip()
    if not model:
        raise ValueError("AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME is required.")
    roles = []
    for role, prefix in (("lead", "PLAYBYPLAY"), ("analyst", "COLOR")):
        name = settings.get(f"FOUNDRY_COMMENTARY_{prefix}_AGENT_NAME", "").strip()
        instructions = settings.get(f"FOUNDRY_COMMENTARY_{prefix}_PROFILE", "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", name) or not instructions:
            raise ValueError(f"Configure a valid {role} profile name and nonempty instructions.")
        roles.append((role, name, instructions))
    if roles[0][1] == roles[1][1]:
        raise ValueError("Lead and analyst must use different profile names.")
    return endpoint, model, roles


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Explicit private dotenv file; process values win.")
    parser.add_argument("--apply", action="store_true", help="Authorize creation of configured role versions.")
    parser.add_argument("--confirm-project", help="Must exactly match the configured project endpoint for --apply.")
    args = parser.parse_args(argv)
    try:
        endpoint, model, roles = _configuration(args.env_file)
    except (ImportError, OSError, ValueError):
        print(json.dumps({"status": "failed", "reason": "invalid_or_unavailable_profile_configuration"}))
        return 2
    if not args.apply:
        print(json.dumps({"status": "dry_run", "mutations": False,
                          "roles": [role for role, _, _ in roles],
                          "operation": "create_configured_role_profile_versions"}))
        return 0
    if args.confirm_project != endpoint:
        print(json.dumps({"status": "failed", "reason": "exact_project_confirmation_required"}))
        return 2

    credential = None
    client = None
    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()
        client = AIProjectClient(endpoint=endpoint, credential=credential)
        for role, name, instructions in roles:
            try:
                client.agents.create_version(
                    agent_name=name,
                    definition={"kind": "prompt", "model": model, "instructions": instructions},
                    draft=False,
                )
            except Exception:
                print(json.dumps({"status": "failed", "role": role,
                                  "reason": "profile_version_creation_failed",
                                  "partial_mutation_possible": True}))
                return 1
            print(json.dumps({"status": "created", "role": role}))
        return 0
    except Exception:
        print(json.dumps({"status": "failed", "reason": "profile_client_unavailable"}))
        return 1
    finally:
        for resource in (client, credential):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
