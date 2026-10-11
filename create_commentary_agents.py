import os
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

root = Path(__file__).resolve().parent


def _load_dotenv_if_present(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv_if_present(root / ".env")

client = AIProjectClient(
    endpoint=os.environ["AZURE_FOUNDRY_PROJECT_ENDPOINT"],
    credential=DefaultAzureCredential(),
)

model = os.environ.get("AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME")
playbyplay_agent_name = os.environ.get("FOUNDRY_COMMENTARY_PLAYBYPLAY_AGENT_NAME")
color_agent_name = os.environ.get("FOUNDRY_COMMENTARY_COLOR_AGENT_NAME")
playbyplay_profile = os.environ.get("FOUNDRY_COMMENTARY_PLAYBYPLAY_PROFILE")
color_profile = os.environ.get("FOUNDRY_COMMENTARY_COLOR_PROFILE")
description = os.environ.get("FOUNDRY_COMMENTARY_AGENT_DESCRIPTION") or ""

if not model:
    raise RuntimeError("AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME must be set")

if not playbyplay_agent_name or not color_agent_name:
    raise RuntimeError(
        "FOUNDRY_COMMENTARY_PLAYBYPLAY_AGENT_NAME and FOUNDRY_COMMENTARY_COLOR_AGENT_NAME must be set",
    )

if not playbyplay_profile or not color_profile:
    raise RuntimeError(
        "FOUNDRY_COMMENTARY_PLAYBYPLAY_PROFILE and FOUNDRY_COMMENTARY_COLOR_PROFILE must be set",
    )

agent_specs = [
    (playbyplay_agent_name, playbyplay_profile),
    (color_agent_name, color_profile),
]

for name, instructions in agent_specs:
    try:
        version = client.agents.create_version(
            agent_name=name,
            definition={
                "kind": "prompt",
                "model": model,
                "instructions": instructions,
            },
            description=description,
            draft=False,
        )
        print("created_or_updated", name, "version", getattr(version, "version", None))
    except Exception as exc:  # noqa: BLE001
        print("create_failed", name, type(exc).__name__, str(exc))

print("--- commentary agents in project ---")
for agent in client.agents.list(limit=50):
    print(getattr(agent, "name", None))
