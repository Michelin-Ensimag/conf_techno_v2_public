"""Entry point: `uv run python -m student_client` (play) or `uv run python -m student_client ping`."""

import argparse
import logging
import signal
import sys
import threading

from student_client.client import GameClient
from student_client.config import get_settings
from student_client.kafka_io import CommandProducer, FactsConsumer
from student_client.security import StartupError, load_identity
from student_client.strategy import StudentStrategy

logger = logging.getLogger("student_client")


def ping(client: GameClient, consumer: FactsConsumer) -> bool:
    """Send one ping and wait for its pong; True if the admin-panel answered."""
    client.send_ping()
    while client.ping_ok is None:
        message = consumer.poll(1.0)
        if message is not None:
            client.handle_message(message.value() or b"")
        client.check_ping_timeout()
    return client.ping_ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="student_client")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["play", "ping"],
        default="play",
        help="play (default): run your strategy; ping: only check that the admin-panel answers, then exit",
    )
    command = parser.parse_args(argv).command
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    try:
        identity = load_identity(settings)
    except StartupError as exc:
        logger.error("Cannot start: %s", exc)
        logger.error(
            "Set TEAM_USERNAME, TEAM_PRIVATE_KEY, TEAM_PUBLIC_KEY (from the admin-panel registration page) "
            "and ADMIN_PUBLIC_KEY in the environment or in a .env file."
        )
        return 1
    logger.info("Identity OK: playing as team %r, verifying facts signed by %r", identity.username, identity.admin_username)

    producer = CommandProducer(settings)
    consumer = FactsConsumer(settings)
    if command == "ping":
        client = GameClient(settings, identity, StudentStrategy(), producer.send)
        try:
            answered = ping(client, consumer)
        finally:
            consumer.close()
            producer.flush()
        return 0 if answered else 1
    strategy = StudentStrategy()
    client = GameClient(settings, identity, strategy, producer.send)

    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    try:
        client.run(consumer, stop)
    finally:
        consumer.close()
        remaining = producer.flush()
        if remaining:
            logger.error("%d command(s) were not delivered", remaining)
    return 0


if __name__ == "__main__":
    sys.exit(main())
