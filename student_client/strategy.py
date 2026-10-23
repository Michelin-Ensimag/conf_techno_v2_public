"""Strategy interface and the team's implementation to complete."""

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from student_client.models import Command, Fact
from student_client.state import GameState


@dataclass
class Context:
    """Current state, command sender and monotonic clock available to a strategy."""

    state: GameState
    send: Callable[[Command], None]
    now: Callable[[], float]


class Strategy(ABC):
    @abstractmethod
    def on_live(self, ctx: Context) -> None:
        """History has been replayed; the state is up to date and the strategy may start."""

    @abstractmethod
    def on_facts(self, ctx: Context, facts: Sequence[Fact]) -> None:
        """React to a batch of facts already applied to the state."""

    def on_tick(self, ctx: Context) -> None:
        """Optional hook called about every second once history has been replayed."""

    def on_reset(self, ctx: Context) -> None:
        """Clear planned actions after a live team reset; ctx.state is already reset. Do not send commands here."""


class StudentStrategy(Strategy):
    def on_live(self, ctx: Context) -> None:
        """Start a first supply-chain circuit using ctx.state and ctx.send."""
        raise NotImplementedError("TODO 5: implement StudentStrategy.on_live")

    def on_facts(self, ctx: Context, facts: Sequence[Fact]) -> None:
        """Continue the circuit when facts confirm the previous actions."""
        raise NotImplementedError("TODO 5: implement StudentStrategy.on_facts")
