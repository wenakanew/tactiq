FROM node:22-bookworm-slim AS provider-runtime

FROM python:3.13-slim-bookworm

COPY --from=provider-runtime /usr/local/bin/node /usr/local/bin/node

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    TACTIQ_MEDIA_DIR=/data/media \
    TACTIQ_DATABASE_PATH=/data/metadata.sqlite3

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 libstdc++6 \
    && apt-get clean \
    && groupadd --gid 10001 tactiq && useradd --uid 10001 --gid tactiq --create-home tactiq \
    && mkdir -p /app /data/media && chown -R tactiq:tactiq /app /data

WORKDIR /app
COPY tools/fetch_detector.py /tmp/fetch_detector.py
RUN python /tmp/fetch_detector.py
COPY requirements.lock pyproject.toml ./
RUN python -m pip install --no-cache-dir -r requirements.lock
COPY services/backend ./services/backend
RUN python -m pip install --no-cache-dir --no-deps .
RUN python -c "from tactiq.agents.workflow import run_foundry_commentary; from agent_framework.openai import OpenAIChatClient; from azure.core.pipeline.transport import AioHttpTransport; AioHttpTransport()"

USER tactiq
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn tactiq.presentation.api.entrypoint:app --host 0.0.0.0 --port \"$PORT\" --workers 1 --no-proxy-headers --no-access-log"]
