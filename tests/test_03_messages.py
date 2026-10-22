"""Step 3: keep useful facts and survive invalid messages."""

import logging

import pytest

pytestmark = pytest.mark.step3


def test_a_fact_for_our_team_updates_our_state(client, signed_fact, score):
    client.handle_message(signed_fact(score))

    assert client.state.balance == 90, "A valid fact addressed to team-a must be applied."


def test_broadcasts_are_applied_but_other_teams_are_ignored(client, signed_fact, score, supplier):
    client.state.locations[supplier.id] = supplier
    closure = {
        "type": "location_close",
        "content": {"location_id": str(supplier.id), "location_code": supplier.code},
    }

    client.handle_message(signed_fact(closure, team=None, seq=1))
    client.handle_message(signed_fact(score, team="team-b", seq=2))

    assert supplier.active is False, "A broadcast (team=null) also concerns our team."
    assert client.state.balance == 0, "Another team's score must not change ours."


def test_invalid_json_does_not_block_the_next_fact(client, signed_fact, score, caplog):
    client.handle_message(b"this is not JSON")
    client.handle_message(signed_fact(score))

    assert client.state.balance == 90, "Keep consuming after a malformed message."
    assert any(record.levelno >= logging.WARNING for record in caplog.records), (
        "Explain why the malformed message was ignored."
    )


def test_an_unknown_fact_does_not_block_the_next_fact(client, signed_fact, score, caplog):
    client.handle_message(signed_fact({"type": "unknown_event", "content": {}}, seq=1))
    client.handle_message(signed_fact(score, seq=2))

    assert client.state.balance == 90, "An unknown fact must not stop the consumer."
    assert any(record.levelno >= logging.WARNING for record in caplog.records), (
        "Explain why the unknown fact was ignored."
    )
