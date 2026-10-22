"""Step 5: test decisions without Kafka; these examples do not test StudentStrategy.

The state fixture in conftest.py starts with 100 money and a small truck model
costing 10. Each test gets a fresh state and a fresh strategy.
"""

from collections.abc import Sequence

import pytest

from student_client.models import (
    BuyTruck,
    BuyTruckContent,
    Command,
    Fact,
    OfferChanged,
    WorldContent,
    WorldSnapshot,
)
from student_client.state import GameState, Location, Truck
from student_client.strategy import Context, Strategy

pytestmark = pytest.mark.step5


class ExampleStrategy(Strategy):
    """Only one decision: buy a first small truck when affordable, then wait.

    This is not a complete strategy: no transport, sale or retry after rejection.
    """

    def __init__(self) -> None:
        self._purchase_pending = False

    def on_live(self, ctx: Context) -> None:
        self._buy_first_truck(ctx)

    def on_facts(self, ctx: Context, facts: Sequence[Fact]) -> None:
        self._buy_first_truck(ctx)

    def on_tick(self, ctx: Context) -> None:
        self._buy_first_truck(ctx)

    def _buy_first_truck(self, ctx: Context) -> None:
        if not ctx.state.world_known or ctx.state.trucks or self._purchase_pending:
            return
        model = ctx.state.truck_models["small"]
        if ctx.state.balance >= model.price:
            self._purchase_pending = True
            ctx.send(BuyTruck(type="buy_truck", content=BuyTruckContent(model_code=model.code)))


def test_requests_a_truck_without_changing_the_game_state(state: GameState) -> None:
    sent: list[Command] = []
    # Capture decisions instead of sending them to Kafka.
    context = Context(state=state, send=sent.append, now=lambda: 0.0)
    strategy = ExampleStrategy()

    strategy.on_live(context)

    assert sent == [BuyTruck(type="buy_truck", content=BuyTruckContent(model_code="small"))]
    assert state.trucks == {}, "Sending an order does not confirm the purchase."
    assert state.balance == 100, "Only a later fact may update the balance."


@pytest.mark.parametrize("balance, expected_orders", [(9, 0), (10, 1), (11, 1)])
def test_only_buys_when_the_budget_covers_the_price(balance: int, expected_orders: int, state: GameState) -> None:
    """Check both sides of the price, including the exact amount needed."""
    state.starting_balance = balance
    sent: list[Command] = []
    context = Context(state=state, send=sent.append, now=lambda: 0.0)
    strategy = ExampleStrategy()

    strategy.on_live(context)

    assert len(sent) == expected_orders


def test_reuses_a_truck_already_present_after_replay(state: GameState, truck: Truck) -> None:
    sent: list[Command] = []
    context = Context(state=state, send=sent.append, now=lambda: 0.0)
    strategy = ExampleStrategy()
    assert truck.id in state.trucks

    strategy.on_live(context)
    strategy.on_tick(context)

    assert sent == [], "Restarting must not buy another truck for this one-truck strategy."


def test_waits_for_the_world_then_reacts_to_the_snapshot(state: GameState) -> None:
    """The client may catch up before the game starts and receive its world later."""
    model = state.truck_models["small"]
    unknown_state = GameState()
    sent: list[Command] = []
    context = Context(state=unknown_state, send=sent.append, now=lambda: 0.0)
    strategy = ExampleStrategy()

    strategy.on_live(context)
    strategy.on_tick(context)

    assert sent == [], "Without the snapshot, no truck model is known."

    snapshot = WorldSnapshot(
        type="world_snapshot",
        content=WorldContent(
            starting_balance=100,
            seconds_per_distance_unit=10,
            resources=[],
            truck_models=[model],
            locations=[],
        ),
    )
    # The real client updates GameState before it calls on_facts.
    unknown_state.apply(snapshot)
    strategy.on_facts(context, [snapshot])

    assert sent == [BuyTruck(type="buy_truck", content=BuyTruckContent(model_code="small"))]
    assert unknown_state.trucks == {}


def test_does_not_repeat_a_purchase_while_waiting_for_confirmation(state: GameState, supplier: Location) -> None:
    supplier.offers["rubber"] = 2
    sent: list[Command] = []
    context = Context(state=state, send=sent.append, now=lambda: 0.0)
    strategy = ExampleStrategy()
    strategy.on_live(context)
    price_changed = OfferChanged.model_validate({
        "type": "offer_change",
        "content": {
            "location_id": supplier.id,
            "location_code": supplier.code,
            "resource_code": "rubber",
            "price": 3,
        },
    })

    state.apply(price_changed)
    strategy.on_facts(context, [price_changed])
    for _ in range(3):
        strategy.on_tick(context)

    assert state.trucks == {}, "No truck_purchased fact has arrived yet."
    assert sent == [BuyTruck(type="buy_truck", content=BuyTruckContent(model_code="small"))], (
        "Unrelated facts and repeated ticks must not duplicate a pending purchase."
    )
