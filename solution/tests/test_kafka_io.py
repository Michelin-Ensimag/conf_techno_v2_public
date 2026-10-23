"""Instructor regression tests for the reference client's Kafka transport."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from confluent_kafka import OFFSET_BEGINNING, KafkaException

from student_client import kafka_io
from student_client.config import Settings


@pytest.fixture
def consumer(monkeypatch):
    driver = Mock()
    driver.list_topics.return_value = SimpleNamespace(
        topics={"game.facts": SimpleNamespace(error=None, partitions={0: None, 1: None})}
    )
    driver.get_watermark_offsets.side_effect = lambda partition, **kwargs: (0, {0: 2, 1: 1}[partition.partition])
    monkeypatch.setattr(kafka_io, "Consumer", Mock(return_value=driver))
    return kafka_io.FactsConsumer(Settings(_env_file=None)), driver


def test_commands_keep_the_team_key_and_service_delivery_callbacks(monkeypatch):
    driver = Mock()
    monkeypatch.setattr(kafka_io, "Producer", Mock(return_value=driver))
    producer = kafka_io.CommandProducer(Settings(_env_file=None))
    raw = b'{"username":"team-a","seq":1}'

    producer.send("game.commands", raw, "team-a")

    driver.produce.assert_called_once_with(
        topic="game.commands", key=b"team-a", value=raw, callback=producer._delivery_report
    )
    driver.poll.assert_called_once_with(0)
    driver.flush.assert_not_called()


def test_delivery_failures_are_logged(caplog):
    message = Mock()
    message.topic.return_value = "game.commands"

    kafka_io.CommandProducer._delivery_report("broker unavailable", message)

    assert "Kafka delivery failed" in caplog.text
    assert "broker unavailable" in caplog.text


def test_facts_consumer_does_not_commit_offsets(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(kafka_io, "Consumer", factory)

    kafka_io.FactsConsumer(Settings(_env_file=None))

    assert factory.call_args.args[0]["enable.auto.commit"] is False


def test_every_partition_is_replayed_from_the_beginning(consumer):
    service, driver = consumer

    assert service._try_assign() is True

    partitions = driver.assign.call_args.args[0]
    assert {(p.topic, p.partition, p.offset) for p in partitions} == {
        ("game.facts", 0, OFFSET_BEGINNING),
        ("game.facts", 1, OFFSET_BEGINNING),
    }
    driver.subscribe.assert_not_called()


def test_strategy_waits_for_history_from_all_partitions(consumer):
    service, driver = consumer
    driver.poll.side_effect = [
        Mock(partition=lambda: 0, offset=lambda: 0, error=lambda: None),
        Mock(partition=lambda: 1, offset=lambda: 0, error=lambda: None),
        Mock(partition=lambda: 0, offset=lambda: 1, error=lambda: None),
    ]

    assert service.caught_up is False
    for caught_up in (False, False, True):
        assert service.poll(0) is not None
        assert service.caught_up is caught_up


def test_an_empty_topic_is_immediately_caught_up_after_assignment(consumer):
    service, driver = consumer
    driver.get_watermark_offsets.side_effect = lambda *args, **kwargs: (0, 0)
    driver.poll.return_value = None

    assert service.caught_up is False
    assert service.poll(0) is None
    assert service.caught_up is True


@pytest.mark.parametrize(
    "topics",
    [
        {},
        {"game.facts": SimpleNamespace(error="unknown topic", partitions={})},
        {"game.facts": SimpleNamespace(error=None, partitions={})},
    ],
)
def test_missing_topic_does_not_start_the_strategy(consumer, topics):
    service, driver = consumer
    driver.list_topics.return_value = SimpleNamespace(topics=topics)

    assert service._try_assign() is False
    assert service.caught_up is False
    driver.assign.assert_not_called()


def test_metadata_errors_are_logged_and_can_be_retried(consumer, caplog):
    service, driver = consumer
    driver.list_topics.side_effect = KafkaException("unavailable")

    assert service._try_assign() is False
    assert service.caught_up is False
    assert "Could not subscribe" in caplog.text


def test_consumer_closes_its_driver(consumer):
    service, driver = consumer

    service.close()

    driver.close.assert_called_once_with()
