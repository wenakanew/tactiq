import json
import sys
import time

import pytest

from tactiq.video.resolver_process import run_resolver_process


def test_provider_process_does_not_inherit_cloud_credentials(monkeypatch):
    monkeypatch.setenv("AZURE_FOUNDRY_API_KEY", "private-fixture-key")
    output = run_resolver_process(
        [sys.executable, "-I", "-c", "import json,os; print(json.dumps({'key':os.getenv('AZURE_FOUNDRY_API_KEY')}))"],
        time.monotonic() + 10,
    )
    assert json.loads(output) == {"key": None}


def test_provider_process_has_a_real_output_bound():
    with pytest.raises(RuntimeError, match="output"):
        run_resolver_process([sys.executable, "-I", "-c", "print('x'*(3*1024*1024))"], time.monotonic() + 10)


def test_provider_process_deadline_stops_owned_work():
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="time budget"):
        run_resolver_process([sys.executable, "-I", "-c", "import time; time.sleep(30)"], started + .3)
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "169.254.169.254", "attacker.invalid"])
def test_provider_network_policy_rejects_private_or_unapproved_hosts_without_dialling(host):
    script = (
        "import json,socket;"
        "from tactiq.video.youtube_resolver import restrict_network;"
        "restrict_network();"
        "\ntry: socket.create_connection((" + repr(host) + ",443))"
        "\nexcept ValueError: print(json.dumps({'rejected':True}))"
        "\nelse: raise AssertionError('Unapproved connection was admitted')"
    )
    assert json.loads(run_resolver_process([sys.executable, "-I", "-c", script], time.monotonic() + 10)) == {"rejected": True}
