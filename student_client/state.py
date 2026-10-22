"""What the player knows about the game, rebuilt only from the facts it receives."""

import datetime as dt
import logging
from dataclasses import dataclass, field
from uuid import UUID

from student_client.models import (
    CommandRejected,
    Fact,
    LocationClosed,
    LocationDefinition,
    LocationOpened,
    OfferChanged,
    RecipeCompleted,
    ReputationChanged,
    ResourcePurchased,
    ScoreUpdated,
    TruckArrived,
    TruckDispatched,
    TruckLoaded,
    TruckModelDefinition,
    TruckPurchased,
    TruckUnloaded,
    WorldSnapshot,
)

logger = logging.getLogger(__name__)


@dataclass
class Recipe:
    code: str
    price: int
    inputs: dict[str, int]
    outputs: dict[str, int]


@dataclass
class Location:
    id: UUID
    code: str
    name: str
    kind: str
    x: int
    y: int
    active: bool
    reputation_delta_per_unit: int
    # Prices of the resources a supplier sells or a customer buys.
    offers: dict[str, int] = field(default_factory=dict)
    recipes: dict[str, Recipe] = field(default_factory=dict)

    @classmethod
    def from_definition(cls, definition: LocationDefinition) -> "Location":
        return cls(
            id=definition.id,
            code=definition.code,
            name=definition.name,
            kind=definition.kind,
            x=definition.x,
            y=definition.y,
            active=definition.active,
            reputation_delta_per_unit=definition.reputation_delta_per_unit,
            offers={offer.resource_code: offer.price for offer in definition.offers},
            recipes={
                recipe.code: Recipe(
                    recipe.code,
                    recipe.price,
                    {item.resource_code: item.quantity for item in recipe.inputs},
                    {item.resource_code: item.quantity for item in recipe.outputs},
                )
                for recipe in definition.recipes
            },
        )


@dataclass
class Truck:
    id: UUID
    model_code: str
    # Both None: the truck waits at the depot, it has not been dispatched yet.
    location_id: UUID | None = None
    destination_id: UUID | None = None
    arrives_at: dt.datetime | None = None
    cargo_resource: str | None = None
    cargo_quantity: int = 0

    @property
    def travelling(self) -> bool:
        return self.destination_id is not None


class GameState:
    """World description plus the team's trucks, stock at locations and scores."""

    def __init__(self) -> None:
        self.world_known = False
        self.starting_balance = 0
        self.seconds_per_distance_unit = 1
        self.truck_models: dict[str, TruckModelDefinition] = {}
        self.locations: dict[UUID, Location] = {}
        self.trucks: dict[UUID, Truck] = {}
        # Resources the team bought or deposited and left at a location: (location id, resource) -> quantity.
        self.stock: dict[tuple[UUID, str], int] = {}
        self._balance: int | None = None
        self.reputation = 0

    @property
    def balance(self) -> int:
        return self._balance if self._balance is not None else self.starting_balance

    def stock_at(self, location_id: UUID, resource_code: str) -> int:
        return self.stock.get((location_id, resource_code), 0)

    def locations_of_kind(self, kind: str) -> list[Location]:
        return [location for location in self.locations.values() if location.kind == kind]

    def apply(self, fact: Fact) -> None:
        """Update the state with one fact (facts of other teams must be filtered out by the caller)."""
        if isinstance(fact, WorldSnapshot):
            world = fact.content
            self.world_known = True
            self.starting_balance = world.starting_balance
            self.seconds_per_distance_unit = world.seconds_per_distance_unit
            self.truck_models = {model.code: model for model in world.truck_models}
            self.locations = {loc.id: Location.from_definition(loc) for loc in world.locations}
        elif isinstance(fact, LocationOpened):
            self._set_active(fact.content.location_id, True)
        elif isinstance(fact, LocationClosed):
            self._set_active(fact.content.location_id, False)
        elif isinstance(fact, OfferChanged):
            location = self.locations.get(fact.content.location_id)
            if location is not None and fact.content.resource_code in location.offers:
                location.offers[fact.content.resource_code] = fact.content.price
        elif isinstance(fact, ReputationChanged):
            location = self.locations.get(fact.content.location_id)
            if location is not None:
                location.reputation_delta_per_unit = fact.content.reputation_delta_per_unit
        elif isinstance(fact, TruckPurchased):
            self.trucks[fact.content.truck_id] = Truck(fact.content.truck_id, fact.content.model_code)
        elif isinstance(fact, ResourcePurchased):
            self._add_stock(fact.content.supplier_id, fact.content.resource_code, fact.content.quantity)
        elif isinstance(fact, TruckLoaded):
            self._apply_loaded(fact)
        elif isinstance(fact, (TruckDispatched, TruckArrived)):
            self._apply_travel(fact)
        elif isinstance(fact, TruckUnloaded):
            self._apply_unloaded(fact)
        elif isinstance(fact, RecipeCompleted):
            self._apply_recipe(fact)
        elif isinstance(fact, ScoreUpdated):
            self._balance = fact.content.balance
            self.reputation = fact.content.reputation
        elif isinstance(fact, CommandRejected):
            logger.warning("Command %s rejected: %s", fact.content.command_type, fact.content.reason)

    def _set_active(self, location_id: UUID, active: bool) -> None:
        location = self.locations.get(location_id)
        if location is not None:
            location.active = active

    def _add_stock(self, location_id: UUID, resource_code: str, quantity: int) -> None:
        key = (location_id, resource_code)
        self.stock[key] = max(0, self.stock.get(key, 0) + quantity)

    def _apply_loaded(self, fact: TruckLoaded) -> None:
        """Move the loaded quantity from the location's team stock into the truck."""
        raise NotImplementedError("TODO 4a: apply a truck_loaded fact to stock and cargo")

    def _apply_travel(self, fact: TruckDispatched | TruckArrived) -> None:
        """Update the truck's location, destination and arrival time from a confirmed departure or arrival."""
        raise NotImplementedError("TODO 4b: apply truck_dispatched and truck_arrived facts")

    def _apply_unloaded(self, fact: TruckUnloaded) -> None:
        truck = self.trucks.get(fact.content.truck_id)
        if truck is None or truck.cargo_resource is None:
            return
        location = self.locations.get(truck.location_id) if truck.location_id is not None else None
        if location is not None and location.kind == "factory":
            self._add_stock(location.id, truck.cargo_resource, fact.content.quantity)
        truck.cargo_quantity = max(0, truck.cargo_quantity - fact.content.quantity)
        if truck.cargo_quantity == 0:
            truck.cargo_resource = None

    def _apply_recipe(self, fact: RecipeCompleted) -> None:
        location = self.locations.get(fact.content.factory_id)
        recipe = location.recipes.get(fact.content.recipe_code) if location is not None else None
        if location is None or recipe is None:
            return
        for resource_code, quantity in recipe.inputs.items():
            self._add_stock(location.id, resource_code, -quantity * fact.content.quantity)
        for resource_code, quantity in recipe.outputs.items():
            self._add_stock(location.id, resource_code, quantity * fact.content.quantity)
