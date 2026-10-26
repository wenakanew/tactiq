"""The remote build context is an explicit source allowlist, not the worktree."""
import importlib.util
from pathlib import Path
import tomllib

import pytest


@pytest.fixture
def staging():
    path = Path(__file__).resolve().parents[2] / "infra" / "scripts" / "stage_build_context.py"
    spec = importlib.util.spec_from_file_location("tactiq_stage_context", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backend_excludes_credentials_media_and_generated_files(staging, tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    for name in ["Dockerfile", "requirements.lock", "pyproject.toml", ".env", "clip.mp4", "model.onnx"]:
        (root / name).write_text("offline")
    package = root / "services" / "backend" / "tactiq"
    package.mkdir(parents=True)
    (package / "main.py").write_text("pass")
    (package / "migrations").mkdir()
    (package / "migrations" / "001.sql").write_text("SELECT 1;")
    (package / ".env").write_text("offline")
    (package / "private.sqlite3").write_text("offline")
    output = tmp_path / "context"
    staging.stage(root, output, "backend")
    assert {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()} == {
        "Dockerfile", "requirements.lock", "pyproject.toml", "services/backend/tactiq/main.py",
        "services/backend/tactiq/migrations/001.sql",
    }
    with pytest.raises(ValueError, match="already exist"):
        staging.stage(root, output, "backend")


def test_web_includes_source_and_configuration_not_build_output(staging, tmp_path):
    root = tmp_path / "source"
    web = root / "apps" / "web"
    (web / "src").mkdir(parents=True)
    for name in ["Dockerfile", "package.json", "package-lock.json", "next.config.mjs", ".env"]:
        (web / name).write_text("offline")
    (web / "src" / "page.tsx").write_text("offline")
    (web / ".next").mkdir()
    (web / ".next" / "generated.js").write_text("offline")
    output = tmp_path / "context"
    staging.stage(root, output, "web")
    assert (output / "src" / "page.tsx").is_file()
    assert not (output / ".env").exists()
    assert not (output / ".next").exists()


def test_foundry_image_includes_and_checks_async_azure_transport():
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    assert "aiohttp>=3.12,<4" in project["project"]["optional-dependencies"]["foundry"]
    dockerfile = (root / "Dockerfile").read_text()
    assert "from azure.core.pipeline.transport import AioHttpTransport; AioHttpTransport()" in dockerfile
