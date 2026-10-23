"""Instructor regression tests for the reference client's reconstructed state."""

from uuid import uuid4

import pytest

from student_client.models import TruckDispatched, fact_adapter
from student_client.state import GameState, Location, Truck


@pytest.fixture
def setup():
    supplier = Location(uuid4(), "supplier", "Supplier", "supplier", 1, 0, True, 0)
    factory = Location(uuid4(), "factory", "Factory", "factory", 0, 0, True, 0)
    truck = Truck(uuid4(), "small", location_id=supplier.id)
    state = GameState()
    state.locations = {supplier.id: supplier, factory.id: factory}
    state.trucks[truck.id] = truck
    state.stock[(supplier.id, "rubber")] = 5
    return state, truck, supplier, factory


def test_loading_moves_stock_into_cargo_and_can_top_up_the_same_resource(setup):
    state, truck, supplier, _ = setup
    loaded = fact_adapter.validate_python({
        "type": "truck_loaded",
        "content": {"truck_id": truck.id, "resource_code": "rubber", "quantity": 1},
    })

    state.apply(loaded)
    state.apply(loaded)

    assert state.stock_at(supplier.id, "rubber") == 3
    assert (truck.cargo_resource, truck.cargo_quantity) == ("rubber", 2)


def test_departure_marks_the_truck_as_travelling_without_changing_cargo(setup):
    state, truck, _, factory = setup
    truck.cargo_resource, truck.cargo_quantity = "rubber", 2
    departure = TruckDispatched.model_validate({
        "type": "truck_dispatched",
        "content": {
            "truck_id": truck.id,
            "destination_id": factory.id,
            "arrives_at": "2030-01-01T00:00:10Z",
        },
    })

    state.apply(departure)

    assert truck.travelling is True
    assert truck.location_id is None
    assert truck.destination_id == factory.id
    assert truck.arrives_at == departure.content.arrives_at
    assert (truck.cargo_resource, truck.cargo_quantity) == ("rubber", 2)


def test_arrival_parks_the_truck_and_clears_travel_fields(setup):
    state, truck, _, factory = setup
    truck.location_id = None
    truck.destination_id = factory.id
    departure = TruckDispatched.model_validate({
        "type": "truck_dispatched",
        "content": {
            "truck_id": truck.id,
            "destination_id": factory.id,
            "arrives_at": "2030-01-01T00:00:10Z",
        },
    })
    truck.arrives_at = departure.content.arrives_at

    state.apply(fact_adapter.validate_python({
        "type": "truck_arrived",
        "content": {"truck_id": truck.id, "location_id": factory.id},
    }))

    assert truck.travelling is False
    assert truck.location_id == factory.id
    assert truck.destination_id is None
    assert truck.arrives_at is None


def test_provided_unloading_deposits_cargo_at_a_factory(setup):
    state, truck, _, factory = setup
    truck.location_id = factory.id
    truck.cargo_resource, truck.cargo_quantity = "rubber", 2

    state.apply(fact_adapter.validate_python({
        "type": "truck_unloaded",
        "content": {"truck_id": truck.id, "quantity": 2},
    }))

    assert state.stock_at(factory.id, "rubber") == 2
    assert (truck.cargo_resource, truck.cargo_quantity) == (None, 0)


@pytest.mark.parametrize("reset", [None, False, True])
def test_only_an_explicit_reset_clears_team_data_and_preserves_the_world(setup, reset):
    state, truck, supplier, _ = setup
    state.world_known = True
    state.starting_balance = 150
    state.seconds_per_distance_unit = 10
    locations, models, trucks, stock = state.locations, state.truck_models, state.trucks, state.stock
    content = {
        "reason": "team reset by an organizer", "money_delta": 20, "reputation_delta": -5,
        "balance": 120, "reputation": 0,
    }
    if reset is not None:
        content["reset"] = reset

    state.apply(fact_adapter.validate_python({"type": "score_updated", "content": content}))

    assert state.trucks is trucks and state.stock is stock
    assert (state.balance, state.reputation) == (120, 0)
    assert state.locations is locations and state.truck_models is models
    assert state.world_known and state.starting_balance == 150 and state.seconds_per_distance_unit == 10
    if reset:
        assert trucks == {} and stock == {}
    else:
        assert truck.id in trucks and state.stock_at(supplier.id, "rubber") == 5


def test_replaying_the_same_history_rebuilds_the_same_state(setup):
    _, truck, supplier, factory = setup
    history = [
        {
            "type": "world_snapshot",
            "content": {
                "starting_balance": 100,
                "seconds_per_distance_unit": 10,
                "resources": [{"code": "rubber", "name": "Rubber"}],
                "truck_models": [{
                    "code": "small", "name": "Small", "capacity": 2,
                    "speed": 1, "co2_per_distance_unit": 1, "price": 10,
                }],
                "locations": [
                    {
                        "id": location.id, "code": location.code, "name": location.name,
                        "kind": location.kind, "x": location.x, "y": location.y,
                        "active": True, "reputation_delta_per_unit": 0,
                    }
                    for location in (supplier, factory)
                ],
            },
        },
        {"type": "truck_purchased", "content": {"truck_id": truck.id, "model_code": "small"}},
        {
            "type": "resource_purchased",
            "content": {"supplier_id": supplier.id, "resource_code": "rubber", "quantity": 2},
        },
        {
            "type": "truck_dispatched",
            "content": {
                "truck_id": truck.id, "destination_id": supplier.id,
                "arrives_at": "2030-01-01T00:00:05Z",
            },
        },
        {"type": "truck_arrived", "content": {"truck_id": truck.id, "location_id": supplier.id}},
        {
            "type": "truck_loaded",
            "content": {"truck_id": truck.id, "resource_code": "rubber", "quantity": 2},
        },
        {
            "type": "truck_dispatched",
            "content": {
                "truck_id": truck.id, "destination_id": factory.id,
                "arrives_at": "2030-01-01T00:00:10Z",
            },
        },
        {"type": "truck_arrived", "content": {"truck_id": truck.id, "location_id": factory.id}},
        {"type": "truck_unloaded", "content": {"truck_id": truck.id, "quantity": 2}},
        {
            "type": "score_updated",
            "content": {
                "reason": "purchase", "money_delta": -14, "reputation_delta": 0,
                "balance": 86, "reputation": 0,
            },
        },
    ]
    encoded_history = [fact_adapter.validate_python(payload).model_dump_json() for payload in history]
    first = GameState()
    for raw in encoded_history:
        first.apply(fact_adapter.validate_json(raw))

    restarted = GameState()
    for raw in encoded_history:
        restarted.apply(fact_adapter.validate_json(raw))

    assert first.trucks == restarted.trucks
    for state in (first, restarted):
        assert state.stock_at(supplier.id, "rubber") == 0
        assert state.stock_at(factory.id, "rubber") == 2
    assert first.balance == restarted.balance == 86
    assert first.world_known is restarted.world_known is True
