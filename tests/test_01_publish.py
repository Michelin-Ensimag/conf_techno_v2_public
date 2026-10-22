"""Step 1: publish the command unchanged, with the team's routing key."""

import pytest

pytestmark = pytest.mark.step1


def test_command_reaches_the_right_topic_with_the_team_key(publication):
    producer, broker = publication
    command = b'{"username":"team-a","seq":1,"payload":{},"signature":"example"}'

    producer.send("game.commands", command, "team-a")

    assert broker.messages == [("game.commands", b"team-a", command)], (
        "Publish the signed bytes unchanged on game.commands, keyed by the team name."
    )


def test_failed_delivery_is_reported(publication, caplog):
    producer, broker = publication
    broker.error = "broker unavailable"

    producer.send("game.commands", b"command", "team-a")

    assert "broker unavailable" in caplog.text, (
        "Register and service the delivery callback so failed commands are visible."
    )
