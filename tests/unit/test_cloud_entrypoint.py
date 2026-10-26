from contextlib import nullcontext
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tactiq.runtime import cloud_entrypoint


@pytest.fixture
def dispatcher(monkeypatch):
    store = SimpleNamespace(healthcheck=Mock(), recover=Mock())
    dependencies = SimpleNamespace(store=store, close=Mock())
    monkeypatch.setattr(cloud_entrypoint, "compose_cloud", lambda **_: dependencies)
    monkeypatch.setattr(cloud_entrypoint.signal, "signal", lambda *_: None)
    monkeypatch.setenv("TACTIQ_SERVICEBUS_NAMESPACE", "example.servicebus.windows.net")
    monkeypatch.setenv("TACTIQ_SERVICEBUS_QUEUE", "analysis")
    monkeypatch.setattr("sys.argv", ["tactiq", "dispatch", "--once"])
    monkeypatch.setitem(
        sys.modules, "azure.identity",
        SimpleNamespace(ManagedIdentityCredential=lambda **_: nullcontext()),
    )
    sender = object()
    client = SimpleNamespace(get_queue_sender=lambda _: nullcontext(sender))
    monkeypatch.setitem(
        sys.modules, "azure.servicebus",
        SimpleNamespace(ServiceBusClient=lambda *_, **__: nullcontext(client)),
    )
    return dependencies


def test_one_shot_dispatch_recovers_before_sending_without_worker_or_queue_message(dispatcher, monkeypatch):
    calls = []
    dispatcher.store.recover.side_effect = lambda: calls.append("recover")
    monkeypatch.setattr(
        cloud_entrypoint, "dispatch_once", lambda *_: calls.append("dispatch") or False,
    )

    cloud_entrypoint.main()

    assert calls == ["recover", "dispatch"]
    dispatcher.store.healthcheck.assert_called_once_with()
    dispatcher.close.assert_called_once_with()


def test_dispatch_recovery_failure_propagates_and_closes_dependencies(dispatcher, monkeypatch):
    dispatcher.store.recover.side_effect = RuntimeError("Database unavailable")
    dispatch = Mock()
    monkeypatch.setattr(cloud_entrypoint, "dispatch_once", dispatch)

    with pytest.raises(RuntimeError, match="Database unavailable"):
        cloud_entrypoint.main()

    dispatch.assert_not_called()
    dispatcher.close.assert_called_once_with()
