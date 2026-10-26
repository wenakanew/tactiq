"""Use ``uvicorn tactiq.presentation.api.entrypoint:app --no-proxy-headers --no-access-log``.

Cloud is opt-in and never catches a cloud failure by selecting the local app.
The legacy main:app remains local-only for existing launchers.
Structured application logs omit callback queries/bodies; disable raw access
logs at the server/ingress too. Ingress client-IP trust is explicitly configured.
"""
import os


def create_app():
    mode = os.getenv("TACTIQ_RUNTIME_MODE", "local").strip().lower()
    if mode == "local":
        from .main import app
        return app
    if mode == "cloud":
        from .cloud import create_cloud_app
        return create_cloud_app()
    raise ValueError("TACTIQ_RUNTIME_MODE must be local or cloud")


app = create_app()
