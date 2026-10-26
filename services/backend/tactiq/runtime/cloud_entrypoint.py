"""Explicit cloud commands: migrate, worker, dispatch, cleanup.

These commands never create Azure resources. Provider operation verification
still requires an approved real private Blob/Service Bus environment.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import signal
import tempfile
import time
from pathlib import Path
from threading import Event
from uuid import uuid4

from tactiq.application.cloud import AnalysisHandlers, expire_guest_data

from .cloud_composition import compose_cloud
from .cloud_execution import create_analysis_worker
from .cloud_worker import dispatch_once, receive_once, send_reference


def main() -> None:
    parser = argparse.ArgumentParser(description="Tactiq opt-in cloud runtime")
    parser.add_argument("command", choices=("migrate", "worker", "dispatch", "cleanup"))
    parser.add_argument("--once", action="store_true", help="One bounded batch instead of a polling process")
    args = parser.parse_args()
    dependencies = compose_cloud(include_identity=False)
    try:
        if args.command == "migrate":
            dependencies.store.migrate()
            dependencies.access.migrate()
            return
        dependencies.store.healthcheck()
        if args.command == "worker":
            dependencies.media.healthcheck()
        if args.command == "cleanup":
            expire_guest_data(dependencies.store, dependencies.access, dependencies.media)
            return
        namespace = os.environ.get("TACTIQ_SERVICEBUS_NAMESPACE", "")
        queue = os.environ.get("TACTIQ_SERVICEBUS_QUEUE", "")
        if not re.fullmatch(r"[a-zA-Z0-9-]+\.servicebus\.(windows\.net|usgovcloudapi\.net|chinacloudapi\.cn)", namespace):
            raise ValueError("TACTIQ_SERVICEBUS_NAMESPACE must be an Azure Service Bus namespace hostname")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", queue):
            raise ValueError("TACTIQ_SERVICEBUS_QUEUE is required")
        from azure.identity import ManagedIdentityCredential
        from azure.servicebus import ServiceBusClient

        stop = Event()
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: stop.set())
        owner = "worker_" + uuid4().hex
        credential = ManagedIdentityCredential(client_id=os.getenv("AZURE_CLIENT_ID") or None)
        worker = None
        if args.command == "worker":
            scratch = os.environ.get("TACTIQ_WORKER_SCRATCH_ROOT", "").strip()
            if not scratch:
                raise ValueError("TACTIQ_WORKER_SCRATCH_ROOT must identify dedicated disposable scratch")
            root = Path(scratch).resolve()
            root.mkdir(parents=True, exist_ok=True)
            # Native normalization/extraction helpers use tempfile internally.
            # This is the isolated worker PROCESS, never an API/thread setting.
            tempfile.tempdir = str(root)
            handlers = AnalysisHandlers(
                dependencies.store, dependencies.media, root,
                max_upload_bytes=dependencies.settings.max_upload_bytes,
            )
            worker = create_analysis_worker(dependencies.store, handlers.handlers(), owner)
        with credential, ServiceBusClient(namespace, credential, retry_total=2) as client:
            with client.get_queue_sender(queue) as sender:
                if worker is None:
                    maintenance_at = 0.0
                    while not stop.is_set():
                        if time.monotonic() >= maintenance_at:
                            dependencies.store.recover()
                            maintenance_at = time.monotonic() + 60
                        dispatched = dispatch_once(
                            dependencies.store, owner,
                            lambda reference, operation: send_reference(sender, reference, operation),
                        )
                        if args.once:
                            break
                        if not dispatched:
                            stop.wait(1)
                else:
                    with client.get_queue_receiver(queue, prefetch_count=0, max_wait_time=5) as receiver:
                        maintenance_at = 0.0
                        while not stop.is_set():
                            if time.monotonic() >= maintenance_at:
                                dependencies.store.recover()
                                expire_guest_data(dependencies.store, dependencies.access, dependencies.media)
                                maintenance_at = time.monotonic() + 60
                            dispatch_once(
                                dependencies.store, owner,
                                lambda reference, operation: send_reference(sender, reference, operation),
                            )
                            receive_once(receiver, worker)
                            if args.once:
                                break
    finally:
        dependencies.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
