"""Shared Kafka and signing setup, kept out of the exercise tests."""

import json
from collections.abc import Callable, Sequence
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from confluent_kafka import OFFSET_BEGINNING, OFFSET_END, OFFSET_INVALID, OFFSET_STORED, TopicPartition
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from student_client import kafka_io
from student_client.client import GameClient
from student_client.config import Settings
from student_client.models import Fact, TruckModelDefinition
from student_client.security import load_identity, sign_payload
from student_client.state import GameState, Location, Truck
from student_client.strategy import Context, Strategy

DeliveryCallback = Callable[[str | None, SimpleNamespace], None]


class ProducerStub:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bytes | None, bytes]] = []
        self.error: str | None = None
        self._pending: list[tuple[str, DeliveryCallback]] = []

    def produce(
        self,
        topic: str,
        value: bytes,
        key: str | bytes | None = None,
        partition: int = -1,
        on_delivery: DeliveryCallback | None = None,
        *,
        callback: DeliveryCallback | None = None,
    ) -> None:
        self.messages.append((topic, key.encode("utf-8") if isinstance(key, str) else key, value))
        report = on_delivery if on_delivery is not None else callback
        if report is not None:
            self._pending.append((topic, report))

    def poll(self, timeout: float) -> int:
        pending, self._pending = self._pending, []
        for topic, report in pending:
            report(self.error, SimpleNamespace(topic=lambda: topic))
        return len(pending)

    def flush(self, timeout: float = 5.0) -> int:
        self.poll(0)
        return 0


class HistoryStub:
    def __init__(self) -> None:
        self.history = {0: [b"world_snapshot", b"offer_change"], 1: [b"truck_purchased"]}
        self._pending: list[tuple[int, int, bytes]] = []

    def list_topics(self, topic: str, timeout: float) -> SimpleNamespace:
        return SimpleNamespace(topics={
            topic: SimpleNamespace(error=None, partitions=dict.fromkeys(self.history)),
        })

    def get_watermark_offsets(
        self, partition: TopicPartition, timeout: float, cached: bool
    ) -> tuple[int, int]:
        return 0, len(self.history[partition.partition])

    def assign(self, partitions: list[TopicPartition]) -> None:
        self._pending = []
        for partition in partitions:
            records = self.history[partition.partition]
            starts = {
                OFFSET_BEGINNING: 0,
                OFFSET_END: len(records),
                OFFSET_STORED: len(records),
                OFFSET_INVALID: len(records),
            }
            start = starts[partition.offset] if partition.offset < 0 else partition.offset
            self._pending.extend(
                (partition.partition, offset, records[offset]) for offset in range(start, len(records))
            )

    def poll(self, timeout: float) -> SimpleNamespace | None:
        if not self._pending:
            return None
        partition, offset, value = self._pending.pop(0)
        return SimpleNamespace(
            partition=lambda: partition, offset=lambda: offset, value=lambda: value, error=lambda: None
        )


@pytest.fixture
def publication(monkeypatch):
    broker = ProducerStub()
    monkeypatch.setattr(kafka_io, "Producer", lambda config: broker)
    return kafka_io.CommandProducer(Settings(_env_file=None)), broker


@pytest.fixture
def consumer(monkeypatch):
    broker = HistoryStub()
    monkeypatch.setattr(kafka_io, "Consumer", lambda config: broker)
    return kafka_io.FactsConsumer(Settings(_env_file=None))


def _public_pem(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()


@pytest.fixture
def identity_settings():
    team_key, admin_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    settings = Settings(
        team_username="team-a",
        team_private_key=team_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode(),
        team_public_key=_public_pem(team_key),
        admin_public_key=_public_pem(admin_key),
        _env_file=None,
    )
    return settings, admin_key


class RecordingStrategy(Strategy):
    def __init__(self) -> None:
        self.live = False
        self.facts: list[Fact] = []

    def on_live(self, ctx: Context) -> None:
        self.live = True

    def on_facts(self, ctx: Context, facts: Sequence[Fact]) -> None:
        self.facts.extend(facts)


@pytest.fixture
def client(identity_settings):
    settings, _ = identity_settings
    return GameClient(settings, load_identity(settings), RecordingStrategy(), lambda *args: None)


@pytest.fixture
def signed_fact(identity_settings):
    _, admin_key = identity_settings

    def encode(fact: dict[str, Any], *, team: str | None = "team-a", seq: int = 1) -> bytes:
        payload = {"team": team, "fact": fact}
        signature = sign_payload(admin_key, {"seq": seq, "payload": payload})
        return json.dumps({
            "username": "admin", "seq": seq, "payload": payload, "signature": signature,
        }).encode("utf-8")

    return encode


@pytest.fixture
def score():
    return {
        "type": "score_updated",
        "content": {
            "reason": "purchase", "money_delta": -10, "reputation_delta": 0,
            "balance": 90, "reputation": 0,
        },
    }


@pytest.fixture
def state():
    world = GameState()
    world.world_known = True
    world.starting_balance = 100
    supplier = Location(UUID(int=1), "supplier", "Supplier", "supplier", 1, 0, True, 0)
    factory = Location(UUID(int=2), "factory", "Factory", "factory", 0, 0, True, 0)
    world.locations = {supplier.id: supplier, factory.id: factory}
    world.stock[(supplier.id, "rubber")] = 5
    world.truck_models["small"] = TruckModelDefinition(
        code="small", name="Small", capacity=2, speed=1, co2_per_distance_unit=1, price=10
    )
    return world


@pytest.fixture
def supplier(state):
    return state.locations_of_kind("supplier")[0]


@pytest.fixture
def factory(state):
    return state.locations_of_kind("factory")[0]


@pytest.fixture
def truck(state, supplier):
    vehicle = Truck(UUID(int=3), "small", location_id=supplier.id)
    state.trucks[vehicle.id] = vehicle
    return vehicle
