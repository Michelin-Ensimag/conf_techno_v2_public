"""Verification of incoming messages: only valid, admin-signed, in-order facts reach the state."""

import json
from collections.abc import Sequence
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from student_client.client import GameClient
from student_client.config import Settings
from student_client.models import Fact
from student_client.security import load_identity, sign_payload
from student_client.strategy import Context, Strategy

from .test_identity import keypair


class RecordingStrategy(Strategy):
    def __init__(self) -> None:
        self.facts: list[Fact] = []
        self.live = False
        self.resets = 0

    def on_live(self, ctx: Context) -> None:
        self.live = True

    def on_facts(self, ctx: Context, facts: Sequence[Fact]) -> None:
        self.facts.extend(facts)

    def on_reset(self, ctx: Context) -> None:
        assert ctx.state.trucks == {} and ctx.state.stock == {}
        self.resets += 1




@pytest.fixture
def setup():
    admin_key = Ed25519PrivateKey.generate()
    admin_public = admin_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    team_private, team_public = keypair()
    settings = Settings(
        team_username="team-a",
        team_private_key=team_private,
        team_public_key=team_public,
        admin_public_key=admin_public,
        _env_file=None,
    )
    identity = load_identity(settings)
    strategy = RecordingStrategy()
    sent: list[tuple[str, bytes, str]] = []
    client = GameClient(settings, identity, strategy, lambda *args: sent.append(args))
    client.check_live(True)
    return client, strategy, admin_key, sent


def admin_message(admin_key, seq, team, fact, username="admin", key=None) -> bytes:
    payload = {"team": team, "fact": fact}
    signature = sign_payload(key or admin_key, {"seq": seq, "payload": payload})
    return json.dumps({"username": username, "seq": seq, "payload": payload, "signature": signature}).encode()


SCORE = {
    "type": "score_updated",
    "content": {"reason": "x", "money_delta": -10, "reputation_delta": 0, "balance": 90, "reputation": 0},
}
PURCHASE = {"type": "truck_purchased", "content": {"truck_id": str(uuid4()), "model_code": "small"}}
RESET = {
    "type": "score_updated",
    "content": {
        "reason": "team reset by an organizer", "money_delta": 10, "reputation_delta": 0,
        "balance": 100, "reputation": 0, "reset": True,
    },
}


def test_live_reset_discards_old_notifications_and_keeps_the_state_object(setup):
    client, strategy, admin_key, _ = setup
    state = client.state
    client.handle_message(admin_message(admin_key, 10, "team-a", PURCHASE))
    client.state.stock[(uuid4(), "rubber")] = 3

    client.handle_message(admin_message(admin_key, 11, "team-a", RESET))
    client.flush()

    assert client.state is state and client._ctx.state is state
    assert (state.trucks, state.stock, state.balance, state.reputation) == ({}, {}, 100, 0)
    assert strategy.resets == 1
    assert [fact.type for fact in strategy.facts] == ["score_updated"]
    # The reset must not allow old signed facts to be applied again.
    client.handle_message(admin_message(admin_key, 10, "team-a", PURCHASE))
    client.handle_message(admin_message(admin_key, 11, "team-a", RESET))
    assert state.trucks == {} and strategy.resets == 1
    client.handle_message(admin_message(admin_key, 12, "team-a", PURCHASE))
    assert len(state.trucks) == 1


def test_replaying_multiple_resets_starts_strategy_only_with_the_final_state(setup):
    client, strategy, admin_key, _ = setup
    client.live = strategy.live = False
    final_purchase = {
        "type": "truck_purchased", "content": {"truck_id": str(uuid4()), "model_code": "small"},
    }
    for seq, fact in enumerate([PURCHASE, RESET, PURCHASE, RESET, final_purchase, SCORE], 1):
        client.handle_message(admin_message(admin_key, seq, "team-a", fact))
    client.flush()

    assert strategy.resets == 0 and strategy.facts == [] and not strategy.live
    assert list(map(str, client.state.trucks)) == [final_purchase["content"]["truck_id"]]
    assert client.state.balance == 90
    client.check_live(True)
    assert strategy.live


@pytest.mark.parametrize("kind", ["other-team", "bad-signature", "unsigned"])
def test_reset_must_be_authenticated_and_target_our_team(setup, kind):
    client, strategy, admin_key, _ = setup
    client.handle_message(admin_message(admin_key, 10, "team-a", PURCHASE))
    if kind == "unsigned":
        raw = json.dumps({"team": "team-a", "fact": RESET}).encode()
    else:
        raw = admin_message(
            admin_key, 11, "team-b" if kind == "other-team" else "team-a", RESET,
            key=Ed25519PrivateKey.generate() if kind == "bad-signature" else None,
        )
    client.handle_message(raw)
    assert len(client.state.trucks) == 1 and strategy.resets == 0


def test_valid_fact_for_the_team_is_applied(setup):
    client, strategy, admin_key, _ = setup

    client.handle_message(admin_message(admin_key, 10, "team-a", SCORE))
    client.flush()

    assert client.state.balance == 90
    assert [f.type for f in strategy.facts] == ["score_updated"]


def test_broadcast_fact_is_accepted_and_other_teams_are_ignored(setup):
    client, strategy, admin_key, _ = setup

    client.handle_message(admin_message(admin_key, 10, None, PURCHASE))
    client.handle_message(admin_message(admin_key, 11, "team-b", SCORE))

    assert len(client.state.trucks) == 1
    assert client.state.balance == 0


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda k: b"not json", id="poison-pill"),
        pytest.param(lambda k: json.dumps({"team": "team-a", "fact": SCORE}).encode(), id="unsigned"),
        pytest.param(
            lambda k: admin_message(k, 10, "team-a", SCORE, key=Ed25519PrivateKey.generate()), id="bad-signature"
        ),
        pytest.param(
            lambda k: admin_message(k, 10, "team-a", SCORE, username="team-b"), id="not-signed-by-admin"
        ),
        pytest.param(lambda k: admin_message(k, 10, "team-a", {"type": "nope", "content": {}}), id="unknown-fact"),
    ],
)
def test_invalid_messages_are_ignored(setup, build):
    client, strategy, admin_key, _ = setup

    client.handle_message(build(admin_key))
    client.flush()

    assert client.state.balance == 0 and strategy.facts == []


@pytest.mark.parametrize("raw", [b"\xff", b"null", b"[]", b"42", b'"text"'])
def test_invalid_encoding_and_non_objects_are_logged_and_ignored(setup, raw, caplog):
    client, strategy, _, _ = setup

    client.handle_message(raw)
    client.flush()

    assert client.state.balance == 0 and strategy.facts == []
    assert caplog.records


@pytest.mark.parametrize(
    "invalid",
    [
        lambda key: b"not json",
        lambda key: admin_message(key, 10, "team-a", {"type": "unknown", "content": {}}),
    ],
)
def test_a_poison_pill_does_not_block_the_next_valid_fact(setup, invalid, caplog):
    client, strategy, admin_key, _ = setup

    client.handle_message(invalid(admin_key))
    client.handle_message(admin_message(admin_key, 11, "team-a", SCORE))
    client.flush()

    assert client.state.balance == 90
    assert [fact.type for fact in strategy.facts] == ["score_updated"]
    assert caplog.records


def test_a_fact_for_another_team_still_advances_replay_protection(setup):
    client, strategy, admin_key, _ = setup

    client.handle_message(admin_message(admin_key, 11, "team-b", SCORE))
    client.handle_message(admin_message(admin_key, 10, "team-a", SCORE))
    client.flush()

    assert client.state.balance == 0 and strategy.facts == []


def test_replay_rebuilds_state_before_notifying_the_strategy(setup):
    client, strategy, admin_key, _ = setup
    client.live = strategy.live = False

    client.handle_message(admin_message(admin_key, 10, "team-a", SCORE))
    client.flush()
    client.check_live(False)
    assert client.state.balance == 90
    assert strategy.live is False and strategy.facts == []

    client.check_live(True)
    assert strategy.live is True and strategy.facts == []
    client.handle_message(admin_message(admin_key, 11, "team-a", PURCHASE))
    assert strategy.facts == []
    client.flush()
    assert [fact.type for fact in strategy.facts] == ["truck_purchased"]


def test_replayed_or_out_of_order_messages_are_ignored(setup):
    client, strategy, admin_key, _ = setup
    message = admin_message(admin_key, 10, "team-a", SCORE)

    client.handle_message(message)
    client.handle_message(message)
    client.handle_message(admin_message(admin_key, 9, "team-a", PURCHASE))
    client.flush()

    assert [f.type for f in strategy.facts] == ["score_updated"]


def test_commands_are_signed_with_the_team_key(setup):
    from student_client.models import BuyTruck, BuyTruckContent
    from student_client.security import verify_signature

    client, _, _, sent = setup

    client.send_command(BuyTruck(type="buy_truck", content=BuyTruckContent(model_code="small")))

    topic, raw, key = sent[0]
    envelope = json.loads(raw)
    assert (topic, key, envelope["username"]) == ("game.commands", "team-a", "team-a")
    assert envelope["payload"] == {"type": "buy_truck", "content": {"model_code": "small"}}
    assert verify_signature(
        client.identity.public_key,
        {"seq": envelope["seq"], "payload": envelope["payload"]},
        envelope["signature"],
    )


def pong(nonce, status="not_started") -> dict:
    return {
        "type": "pong",
        "content": {"nonce": nonce, "game_status": status, "server_time": "2030-01-01T00:00:00Z"},
    }


def test_ping_is_signed_and_the_matching_pong_confirms_it(setup, caplog):
    client, strategy, admin_key, sent = setup

    client.send_ping()

    topic, raw, _ = sent[-1]
    payload = json.loads(raw)["payload"]
    assert topic == "game.commands" and payload["type"] == "ping"
    nonce = payload["content"]["nonce"]
    client.handle_message(admin_message(admin_key, 1, "team-a", pong("stale")))
    assert client.ping_ok is None
    with caplog.at_level("INFO"):
        client.handle_message(admin_message(admin_key, 2, "team-a", pong(nonce)))
    assert client.ping_ok is True and "Ping OK" in caplog.text and "not_started" in caplog.text
    client.flush()
    assert strategy.facts == []


def test_ping_fails_after_the_timeout_and_a_late_pong_recovers(setup, caplog):
    client, _, admin_key, sent = setup
    now = [0.0]
    client._clock = lambda: now[0]
    client.send_ping()
    nonce = json.loads(sent[-1][1])["payload"]["content"]["nonce"]

    client.check_ping_timeout()
    assert client.ping_ok is None
    now[0] = client.settings.ping_timeout_seconds + 1
    client.check_ping_timeout()
    assert client.ping_ok is False and "Ping FAILED" in caplog.text

    client.handle_message(admin_message(admin_key, 1, "team-a", pong(nonce, "running")))
    assert client.ping_ok is True
