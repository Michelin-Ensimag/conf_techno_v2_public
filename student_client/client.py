"""Glue between Kafka, the verified facts, the game state and the strategy."""

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from student_client.config import Settings
from student_client.kafka_io import FactsConsumer
from student_client.models import Command, Fact, FactMessage, Ping, PingContent, Pong
from student_client.security import Identity, is_signed_envelope
from student_client.state import GameState
from student_client.strategy import Context, Strategy

logger = logging.getLogger(__name__)

TICK_SECONDS = 1.0
MAX_BATCH = 500
BATCH_WAIT_SECONDS = 0.05

# (topic, serialized message, key)
Sender = Callable[[str, bytes, str], None]


class GameClient:
    def __init__(
        self,
        settings: Settings,
        identity: Identity,
        strategy: Strategy,
        sender: Sender,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.identity = identity
        self.strategy = strategy
        self.state = GameState()
        self.live = False
        self._batch: list[Fact] = []
        self._sender = sender
        self._last_admin_seq = 0
        self._last_tick = clock()
        self._clock = clock
        self._ctx = Context(self.state, self.send_command, clock)
        self._ping_nonce: str | None = None
        self._ping_sent_at = 0.0
        self.ping_ok: bool | None = None

    def send_command(self, command: Command) -> None:
        envelope = self.identity.build_envelope(command.model_dump(mode="json"))
        self._sender(self.settings.game_commands_topic, json.dumps(envelope).encode("utf-8"), self.identity.username)

    def send_ping(self) -> None:
        """Check that the admin-panel can be reached: it must answer with a `pong` carrying the same nonce."""
        self._ping_nonce = uuid.uuid4().hex
        self._ping_sent_at = self._clock()
        self.ping_ok = None
        self.send_command(Ping(type="ping", content=PingContent(nonce=self._ping_nonce)))
        logger.info("Ping sent to the admin-panel, waiting for the pong")

    def check_ping_timeout(self) -> None:
        if self.ping_ok is None and self._ping_nonce is not None:
            waited = self._clock() - self._ping_sent_at
            if waited > self.settings.ping_timeout_seconds:
                self.ping_ok = False
                logger.error(
                    "Ping FAILED: no pong from the admin-panel after %.0fs. Check that Kafka is reachable at %s, "
                    "that the admin-panel is running and reads %s, and that TEAM_USERNAME and the keys match "
                    "your registration.",
                    waited,
                    self.settings.kafka_bootstrap_servers,
                    self.settings.game_commands_topic,
                )

    def _handle_pong(self, pong: Pong) -> None:
        if pong.content.nonce != self._ping_nonce:
            return
        was_failed = self.ping_ok is False
        self.ping_ok = True
        logger.info(
            "Ping OK: the admin-panel answered in %.2fs%s (game status: %s)",
            self._clock() - self._ping_sent_at,
            " (late)" if was_failed else "",
            pong.content.game_status,
        )

    def handle_message(self, raw: bytes) -> None:
        """Verify one message of the facts topic, apply it to the state and let the strategy react."""
        envelope = self._decode_envelope(raw)
        if envelope is None:
            return
        if not is_signed_envelope(envelope):
            logger.warning("Ignored a message that is not a signed envelope: %r", raw[:200])
            return
        if not self.identity.verify_admin_envelope(envelope):
            logger.warning(
                "Ignored a message with an invalid signature or not signed by %r (claimed sender: %r)",
                self.identity.admin_username,
                envelope["username"],
            )
            return
        if envelope["seq"] <= self._last_admin_seq:
            logger.warning("Ignored a replayed or out-of-order message (seq=%d)", envelope["seq"])
            return
        self._last_admin_seq = envelope["seq"]

        message = self._message_for_team(envelope["payload"])
        if message is None:
            return

        if isinstance(message.fact, Pong):
            self._handle_pong(message.fact)
            return
        self.state.apply(message.fact)
        if self.live:
            self._batch.append(message.fact)

    @staticmethod
    def _decode_envelope(raw: bytes) -> dict[str, Any] | None:
        """Decode a UTF-8 JSON object; log and ignore invalid messages by returning None."""
        raise NotImplementedError("TODO 3a: decode messages without letting poison pills stop the consumer")

    def _message_for_team(self, payload: dict[str, Any]) -> FactMessage | None:
        """Validate a FactMessage; log invalid payloads, ignore other teams and accept broadcasts."""
        raise NotImplementedError("TODO 3b: validate facts and filter their destination")

    def flush(self) -> None:
        """Let the strategy react to the facts applied since the last call, all at once."""
        if self._batch and self.live:
            batch, self._batch = self._batch, []
            self._call_strategy(lambda: self.strategy.on_facts(self._ctx, batch))

    def check_live(self, caught_up: bool) -> None:
        if caught_up and not self.live:
            self.live = True
            logger.info(
                "History replayed: %d truck(s), balance %d, reputation %d",
                len(self.state.trucks), self.state.balance, self.state.reputation,
            )
            self._call_strategy(lambda: self.strategy.on_live(self._ctx))

    def tick(self) -> None:
        now = self._clock()
        if self.live and now - self._last_tick >= TICK_SECONDS:
            self._last_tick = now
            self._call_strategy(lambda: self.strategy.on_tick(self._ctx))

    @staticmethod
    def _call_strategy(call: Callable[[], None]) -> None:
        try:
            call()
        except Exception:
            logger.exception("The strategy failed, continuing with the next message")

    def run(self, consumer: FactsConsumer, stop: threading.Event) -> None:
        self.send_ping()
        while not stop.is_set():
            message = consumer.poll(1.0)
            # Drain what is immediately available so the strategy sees consistent states.
            drained = 0
            while message is not None and drained < MAX_BATCH:
                self.handle_message(message.value() or b"")
                drained += 1
                message = consumer.poll(BATCH_WAIT_SECONDS)
            if message is not None:
                self.handle_message(message.value() or b"")
            self.check_live(consumer.caught_up)
            self.flush()
            self.tick()
            self.check_ping_timeout()
