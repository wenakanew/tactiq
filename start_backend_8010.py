import os
import sys
from pathlib import Path

import uvicorn

root = Path(__file__).resolve().parent
backend_path = root / "services" / "backend"
sys.path.insert(0, str(backend_path))

def _load_dotenv_if_present(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv_if_present(root / ".env")

from tactiq.presentation.api.main import app  # noqa: E402

host = os.environ.get("TACTIQ_DEV_BACKEND_HOST")
port_raw = os.environ.get("TACTIQ_DEV_BACKEND_PORT")

if not host or not host.strip():
    raise RuntimeError("TACTIQ_DEV_BACKEND_HOST must be set")
if not port_raw or not port_raw.strip():
    raise RuntimeError("TACTIQ_DEV_BACKEND_PORT must be set")

port = int(port_raw)
uvicorn.run(app, host=host.strip(), port=port)
