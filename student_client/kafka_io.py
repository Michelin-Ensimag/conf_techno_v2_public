"""Kafka access: a consumer that replays the whole facts topic, and a producer for commands."""

import logging
import time
import uuid
from typing import Any

from confluent_kafka import OFFSET_BEGINNING, Consumer, KafkaException, Message, Producer, TopicPartition

from student_client.config import Settings

logger = logging.getLogger(__name__)


class FactsConsumer:
    """Reads `game.facts` from its very first message, then tells when the history is exhausted.

    The state of the game is rebuilt from the facts, so a (re)started player must always
    replay the topic: partitions are assigned manually from the beginning, no consumer group
    offsets are stored.
    """

    def __init__(self, settings: Settings) -> None:
        self._topic = settings.game_facts_topic
        self._consumer = Consumer(
            {
                "bootstrap.servers": settings.kafka_bootstrap_servers,
                "group.id": f"{settings.kafka_client_id}-{uuid.uuid4().hex[:8]}",
                "client.id": settings.kafka_client_id,
                "enable.auto.commit": False,
                "topic.metadata.refresh.interval.ms": 10_000,
            }
        )
        self._end_offsets: dict[int, int] = {}
        self._next_offsets: dict[int, int] = {}
        self._assigned = False

    def poll(self, timeout: float) -> Message | None:
        if not self._assigned:
            self._assigned = self._try_assign()
            if not self._assigned:
                time.sleep(timeout)
                return None
        try:
            message = self._consumer.poll(timeout)
        except KafkaException:
            logger.exception("Kafka poll error")
            return None
        if message is None:
            return None
        if message.error():
            logger.error("Kafka consumer error: %s", message.error())
            return None
        self._next_offsets[message.partition()] = message.offset() + 1
        return message

    @property
    def caught_up(self) -> bool:
        """True once everything that was in the topic when the player started has been read."""
        return self._assigned and all(
            self._next_offsets.get(partition, 0) >= end for partition, end in self._end_offsets.items()
        )

    def _try_assign(self) -> bool:
        try:
            metadata = self._consumer.list_topics(self._topic, timeout=5.0)
            topic = metadata.topics.get(self._topic)
            if topic is None or topic.error is not None or not topic.partitions:
                logger.info("Topic %s does not exist yet, waiting for the admin-panel", self._topic)
                return False
            partitions = [TopicPartition(self._topic, p) for p in topic.partitions]
            for partition in partitions:
                _, high = self._consumer.get_watermark_offsets(partition, timeout=5.0, cached=False)
                self._end_offsets[partition.partition] = high
            self._assign_from_beginning(partitions)
        except KafkaException:
            logger.exception("Could not subscribe to %s yet", self._topic)
            return False
        logger.info("Replaying %s (end offsets: %s)", self._topic, self._end_offsets)
        return True

    def _assign_from_beginning(self, partitions: list[TopicPartition]) -> None:
        """Assign all discovered partitions from the beginning, ignoring stored group offsets."""
        raise NotImplementedError("TODO 2: replay every facts partition from its beginning")

    def close(self) -> None:
        self._consumer.close()


class CommandProducer:
    def __init__(self, settings: Settings) -> None:
        self._producer = Producer(
            {
                "bootstrap.servers": settings.kafka_bootstrap_servers,
                "client.id": settings.kafka_client_id,
                # Keeps the messages of the team in order: the admin rejects a seq lower than a previous one.
                "enable.idempotence": True,
            }
        )

    @staticmethod
    def _delivery_report(err: Any, message: Any) -> None:
        if err is not None:
            logger.error("Kafka delivery failed for %s: %s", message.topic(), err)

    def send(self, topic: str, value: bytes, key: str) -> None:
        """Enqueue the signed bytes with the team's key and service delivery callbacks without blocking."""
        raise NotImplementedError("TODO 1: publish a command and handle delivery callbacks")

    def flush(self, timeout: float = 5.0) -> int:
        return self._producer.flush(timeout)
