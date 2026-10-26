"""Bounded in-process WebSocket admission, live messages, and cleanup contracts."""

from __future__ import annotations

import asyncio

import pytest


def test_websocket_sends_state_insights_and_real_service_broadcasts(api, populated_match):
    with api.websocket(populated_match, case="state-and-insight-snapshots") as websocket:
        state = websocket.receive_json()
        insights = websocket.receive_json()
        ready = websocket.receive_json()
        assert state["type"] == "match.snapshot" and state["payload"]["match_id"] == populated_match
        assert insights["type"] == "insights.snapshot" and insights["payload"]
        assert all(item["match_id"] == populated_match for item in insights["payload"])
        assert ready == {"type": "connection.ready", "match_id": populated_match}
        assert len(api.service.subscribers[populated_match]) == 1
        assert api.service.in_use[populated_match] == 1
        message = {"type": "insight.created", "payload": insights["payload"][-1]}
        api.client.portal.call(api.service._publish, populated_match, message)
        assert websocket.receive_json() == message
        websocket.send_json({"type": "offline-client-heartbeat"})
    assert not api.service.subscribers[populated_match]
    assert not api.service.in_use.get(populated_match)


@pytest.mark.parametrize("actor", [None, "viewer"])
def test_websocket_rejects_missing_or_invalid_credentials(api, match_id, actor):
    headers = {} if actor is None else {"Authorization": "Bearer offline-invalid"}
    api.websocket_denied(
        match_id, case="missing-auth" if actor is None else "invalid-auth", code=4401,
        actor=actor, headers=headers,
    )
    assert not api.service.subscribers.get(match_id)
    assert not api.service.in_use.get(match_id)


def test_websocket_unknown_match_is_hidden_with_a_not_found_close(api):
    api.websocket_denied("missing_match", case="unknown-match", code=4404)
    assert not api.service.subscribers.get("missing_match")


def test_websocket_missing_path_identifier_is_not_admitted(api):
    api.websocket_denied("", case="missing-match-path-identifier", code=1000)


def test_websocket_foreign_match_is_hidden_even_for_a_foreign_admin(api, match_id):
    api.websocket_denied(match_id, case="foreign-match", code=4404, actor="foreign")
    assert not api.service.subscribers.get(match_id)
    assert not api.service.in_use.get(match_id)


def test_websocket_browser_origin_is_enforced_for_bearer_auth(api, match_id):
    api.websocket_denied(
        match_id, case="untrusted-origin", code=4403, headers={"Origin": "https://untrusted.invalid"},
    )
    with api.websocket(
        match_id, case="verified-origin", headers={"Origin": "https://audit.example"},
    ) as websocket:
        assert websocket.receive_json()["type"] == "match.snapshot"
        assert websocket.receive_json() == {"type": "insights.snapshot", "payload": []}


def test_cookie_websocket_requires_a_verified_origin_and_honors_bad_header_precedence(api, match_id):
    api.request("POST", "/api/v1/session", case="websocket-cookie-fixture", actor="viewer")
    api.websocket_denied(match_id, case="cookie-without-origin", code=4403, actor=None)
    api.websocket_denied(
        match_id, case="cookie-with-wrong-origin", code=4403, actor=None,
        headers={"Origin": "https://untrusted.invalid"},
    )
    api.websocket_denied(
        match_id, case="cookie-cannot-rescue-invalid-header", code=4401, actor=None,
        headers={"Origin": "https://audit.example", "Authorization": "Bearer offline-invalid"},
    )
    with api.websocket(
        match_id, case="cookie-with-verified-origin", actor=None, headers={"Origin": "https://audit.example"},
    ) as websocket:
        assert websocket.receive_json()["payload"]["match_id"] == match_id


def test_websocket_subscriber_admission_is_bounded_and_rejection_unpins_match(api, match_id):
    subscribers = api.service.subscribers[match_id]
    subscribers.update(asyncio.Queue(maxsize=128) for _ in range(16))
    try:
        api.websocket_denied(match_id, case="subscriber-capacity-full", code=4429)
        assert len(subscribers) == 16
        assert not api.service.in_use.get(match_id)
    finally:
        subscribers.clear()
    with api.websocket(match_id, case="subscriber-capacity-recovered") as websocket:
        assert websocket.receive_json()["type"] == "match.snapshot"


def test_websocket_admission_uses_the_http_identity_rate_limit(api, match_id):
    api.client.app.state.limiter.general_limit = 1
    with api.websocket(match_id, case="rate-limit-first-connection") as websocket:
        assert websocket.receive_json()["type"] == "match.snapshot"
    api.websocket_denied(match_id, case="rate-limit-rejected-connection", code=4403)
    assert not api.service.subscribers[match_id]
    assert not api.service.in_use.get(match_id)


def test_live_websocket_prevents_deletion_until_it_disconnects(api, match_id):
    with api.websocket(match_id, case="active-viewer-pins-match") as websocket:
        assert websocket.receive_json()["payload"]["match_id"] == match_id
        api.request(
            "DELETE", "/api/v1/matches/{match_id}", case="delete-while-viewer-active", status=503,
            path_params={"match_id": match_id},
        )
        assert match_id in api.service.matches
    api.request(
        "DELETE", "/api/v1/matches/{match_id}", case="delete-after-viewer-disconnect",
        path_params={"match_id": match_id},
    )


def test_websocket_disconnect_is_per_connection_not_per_match(api, match_id):
    with api.websocket(match_id, case="first-viewer") as first:
        assert first.receive_json()["type"] == "match.snapshot"
        with api.websocket(match_id, case="second-viewer") as second:
            assert second.receive_json()["type"] == "match.snapshot"
            assert len(api.service.subscribers[match_id]) == 2
            assert api.service.in_use[match_id] == 2
        assert len(api.service.subscribers[match_id]) == 1
        assert api.service.in_use[match_id] == 1
    assert not api.service.subscribers[match_id]
    assert not api.service.in_use.get(match_id)
