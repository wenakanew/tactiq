from __future__ import annotations

import argparse
import logging
import os
import time

import requests

from tactiq.operations.monitor import send_alerts


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll authenticated Tactiq SLO status and deliver configured alerts.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()
    if args.interval < 10:
        raise ValueError("The monitoring interval must be at least ten seconds")
    base = os.environ["TACTIQ_MONITOR_API_URL"].rstrip("/")
    credential = os.environ["TACTIQ_MONITOR_ACCESS_TOKEN"]
    webhook = os.environ["TACTIQ_ALERT_WEBHOOK_URL"]
    if not base.startswith("https://") and os.getenv("APP_ENV") == "production":
        raise ValueError("Production monitoring must use HTTPS")
    while True:
        response = requests.get(
            f"{base}/api/v1/operations/status",
            headers={"Authorization": f"Bearer {credential}"}, timeout=10, allow_redirects=False,
        )
        try:
            response.raise_for_status()
            status = response.json()
        finally:
            response.close()
        send_alerts(webhook, status["alerts"])
        logging.info("SLO monitor state: %s", status["status"])
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
