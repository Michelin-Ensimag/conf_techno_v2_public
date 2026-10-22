"""Step 4: update stock and trucks from confirmed facts, not from sent commands."""

from datetime import datetime, timezone

import pytest

from student_client.models import LoadContent, TruckArrived, TruckDispatched, TruckLoaded

pytestmark = pytest.mark.step4


def test_loading_transfers_units_from_stock_to_cargo(state, truck, supplier):
    fact = TruckLoaded(
        type="truck_loaded",
        content=LoadContent(truck_id=truck.id, resource_code="rubber", quantity=2),
    )

    state.apply(fact)

    assert state.stock_at(supplier.id, "rubber") == 3, "Two units left the location's stock."
    assert (truck.cargo_resource, truck.cargo_quantity) == ("rubber", 2), (
        "The same two units are now on the truck."
    )


def test_departure_marks_the_truck_as_travelling(state, truck, factory):
    truck.cargo_resource, truck.cargo_quantity = "rubber", 2
    arrival_time = datetime(2030, 1, 1, tzinfo=timezone.utc)
    fact = TruckDispatched.model_validate({
        "type": "truck_dispatched",
        "content": {
            "truck_id": truck.id, "destination_id": factory.id, "arrives_at": arrival_time,
        },
    })

    state.apply(fact)

    assert truck.location_id is None, "A travelling truck is not parked at its origin."
    assert truck.destination_id == factory.id and truck.arrives_at == arrival_time
    assert truck.travelling is True
    assert (truck.cargo_resource, truck.cargo_quantity) == ("rubber", 2), "Travel preserves cargo."


def test_arrival_parks_the_truck_and_clears_its_trip(state, truck, factory):
    truck.location_id = None
    truck.destination_id = factory.id
    truck.arrives_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
    fact = TruckArrived.model_validate({
        "type": "truck_arrived",
        "content": {"truck_id": truck.id, "location_id": factory.id},
    })

    state.apply(fact)

    assert truck.location_id == factory.id, "The confirmed arrival determines the truck's position."
    assert truck.destination_id is None and truck.arrives_at is None, "The trip is over."
    assert truck.travelling is False
