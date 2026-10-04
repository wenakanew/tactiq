import os
import sys
from pathlib import Path

import uvicorn

root = Path(__file__).resolve().parent
backend_path = root / "services" / "backend"
sys.path.insert(0, str(backend_path))

# Load simple .env key=value pairs into process env if not already present
for line in (root / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    os.environ[key.strip()] = value.strip()

from tactiq.presentation.api.main import app  # noqa: E402

port = int(os.environ.get("TACTIQ_DEV_BACKEND_PORT", "8765"))
uvicorn.run(app, host="127.0.0.1", port=port)
