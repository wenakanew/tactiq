"""Copy a source-only allowlist into a new local build directory before upload."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil


def stage(source: Path, destination: Path, component: str) -> int:
    if destination.exists():
        raise ValueError("Destination must not already exist")
    root = source.resolve()
    if component == "backend":
        exact = ["Dockerfile", "requirements.lock", "pyproject.toml", ".dockerignore", "tools/fetch_detector.py"]
        trees = ["services/backend"]
        suffixes = {".py", ".json", ".toml", ".txt", ".typed", ".sql"}
    elif component == "web":
        root /= "apps/web"
        exact = [
            "Dockerfile", ".dockerignore", "package.json", "package-lock.json",
            "next.config.mjs", "tsconfig.json", "postcss.config.mjs",
            "eslint.config.mjs", "next-env.d.ts",
        ]
        trees = ["src", "app", "components", "lib", "public"]
        suffixes = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".json", ".css", ".svg", ".ico",
                    ".png", ".jpg", ".jpeg", ".webp", ".woff", ".woff2", ".txt"}
    else:
        raise ValueError("Unknown component")
    files = [root / name for name in exact if (root / name).is_file()]
    for tree in trees:
        folder = root / tree
        if folder.is_dir():
            files.extend(path for path in folder.rglob("*")
                         if path.is_file() and path.suffix in suffixes
                         and not any(part in {"__pycache__", "node_modules", ".next", ".git"}
                                     or part.endswith(".egg-info") for part in path.relative_to(root).parts))
    required = {"Dockerfile", "requirements.lock", "pyproject.toml"} if component == "backend" else {
        "Dockerfile", "package.json", "package-lock.json", "next.config.mjs",
    }
    if not all((root / name).is_file() for name in required):
        raise ValueError("Required build inputs are missing")
    for path in files:
        relative = path.relative_to(root)
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("Build context must not follow links outside source")
        if any(part.startswith(".env") for part in relative.parts):
            raise ValueError("Environment files cannot enter a build context")
    destination.mkdir(parents=True)
    digest = hashlib.sha256()
    for path in sorted(set(files)):
        relative = path.relative_to(root)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        digest.update(relative.as_posix().encode() + b"\0" + hashlib.sha256(target.read_bytes()).digest())
    print(f"{component}: staged {len(set(files))} allowlisted files; source manifest SHA256 {digest.hexdigest()}")
    return len(set(files))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("backend", "web"))
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    stage(Path(__file__).resolve().parents[2], args.destination, args.component)
