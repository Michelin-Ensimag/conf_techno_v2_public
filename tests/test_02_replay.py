"""Step 2: receive the existing history before declaring the client live."""

import pytest

pytestmark = pytest.mark.step2


def test_existing_facts_are_read_from_every_partition(consumer):
    received = []

    for _ in range(3):
        message = consumer.poll(0)
        assert message is not None, "Replay must read existing facts, not start at the end of the topic."
        received.append(message.value())

    assert set(received) == {b"world_snapshot", b"offer_change", b"truck_purchased"}, (
        "Every partition contributes to the team's history."
    )


def test_caught_up_is_true_only_after_the_whole_history(consumer):
    assert consumer.caught_up is False

    for expected in (False, False, True):
        assert consumer.poll(0) is not None, "A historical fact is still waiting to be read."
        assert consumer.caught_up is expected, (
            "Do not start the strategy until all pre-existing facts have been read."
        )
