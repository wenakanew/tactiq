import os
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

root = Path(__file__).resolve().parent
for line in (root / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

client = AIProjectClient(
    endpoint=os.environ["AZURE_FOUNDRY_PROJECT_ENDPOINT"],
    credential=DefaultAzureCredential(),
)

model = os.environ.get("AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME", "gpt-5.6-sol")
agent_specs = [
    (
        "commentary-playbyplay",
        "You are the Play-by-Play commentator for football broadcasts. Output exactly one short natural line (8-22 words), urgent visual call, no invented stats, no markdown.",
    ),
    (
        "commentary-color",
        "You are the Color commentator for football broadcasts. Output exactly one short natural line (8-22 words), tactical context, supportive tone, no invented stats, no markdown.",
    ),
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
            description="Tactiq dual-commentary agent",
            draft=False,
        )
        print("created_or_updated", name, "version", getattr(version, "version", None))
    except Exception as exc:  # noqa: BLE001
        print("create_failed", name, type(exc).__name__, str(exc))

print("--- commentary agents in project ---")
for agent in client.agents.list(limit=50):
    name = getattr(agent, "name", None)
    if name and "commentary" in name:
        print(name)
