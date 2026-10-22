"""Business classes of the game: commands sent to the admin and facts received from it.

Mirror of admin-panel/app/models/payloads.py (the server is the source of truth): keep
both in sync, admin-panel/tests/test_client_contract.py compares their JSON schemas.

Players send `Command`s inside a signed envelope. The admin answers with `Fact`s wrapped
as `{"team": str | None, "fact": Fact}` (`team` is None for facts broadcast to everyone:
world snapshot and location events).
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PurchaseContent(Payload):
    supplier_id: UUID
    resource_code: str = Field(min_length=1)
    quantity: int = Field(gt=0)


class LoadContent(Payload):
    truck_id: UUID
    resource_code: str = Field(min_length=1)
    quantity: int = Field(gt=0)


class DispatchContent(Payload):
    truck_id: UUID
    destination_id: UUID


class UnloadContent(Payload):
    truck_id: UUID
    quantity: int = Field(gt=0)


class BuyTruckContent(Payload):
    model_code: str = Field(min_length=1)


class RecipeContent(Payload):
    factory_id: UUID
    recipe_code: str = Field(min_length=1)
    quantity: int = Field(gt=0)


class PingContent(Payload):
    nonce: str = Field(min_length=1, max_length=64)


class Ping(Payload):
    """Connectivity check: always answered with a `pong`, even before the game starts."""

    type: Literal["ping"]
    content: PingContent


class BuyResource(Payload):
    type: Literal["buy_resource"]
    content: PurchaseContent


class LoadTruck(Payload):
    type: Literal["load_truck"]
    content: LoadContent


class DispatchTruck(Payload):
    type: Literal["dispatch_truck"]
    content: DispatchContent


class UnloadTruck(Payload):
    type: Literal["unload_truck"]
    content: UnloadContent


class BuyTruck(Payload):
    type: Literal["buy_truck"]
    content: BuyTruckContent


class StartRecipe(Payload):
    type: Literal["start_recipe"]
    content: RecipeContent


Command = Annotated[Ping | BuyResource | LoadTruck | DispatchTruck | UnloadTruck | BuyTruck | StartRecipe,
                    Field(discriminator="type")]
command_adapter = TypeAdapter(Command)


GameStatus = Literal["not_started", "running", "paused", "finished"]


class PongContent(Payload):
    nonce: str = Field(min_length=1, max_length=64)
    game_status: GameStatus
    server_time: datetime


class Pong(Payload):
    type: Literal["pong"]
    content: PongContent


class ResourcePurchased(Payload):
    type: Literal["resource_purchased"]
    content: PurchaseContent


class TruckLoaded(Payload):
    type: Literal["truck_loaded"]
    content: LoadContent


class DispatchedContent(DispatchContent):
    arrives_at: datetime | None = None


class TruckDispatched(Payload):
    type: Literal["truck_dispatched"]
    content: DispatchedContent


class ArrivedContent(Payload):
    truck_id: UUID
    location_id: UUID


class TruckArrived(Payload):
    type: Literal["truck_arrived"]
    content: ArrivedContent


class TruckUnloaded(Payload):
    type: Literal["truck_unloaded"]
    content: UnloadContent


class TruckPurchasedContent(Payload):
    truck_id: UUID
    model_code: str = Field(min_length=1)


class TruckPurchased(Payload):
    type: Literal["truck_purchased"]
    content: TruckPurchasedContent


class RecipeStarted(Payload):
    type: Literal["recipe_started"]
    content: RecipeContent


class RecipeCompleted(Payload):
    type: Literal["recipe_completed"]
    content: RecipeContent


class RejectionContent(Payload):
    command_type: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class CommandRejected(Payload):
    type: Literal["command_rejected"]
    content: RejectionContent


class ScoreUpdatedContent(Payload):
    reason: str = Field(min_length=1)
    money_delta: int
    reputation_delta: int
    balance: int
    reputation: int


class ScoreUpdated(Payload):
    type: Literal["score_updated"]
    content: ScoreUpdatedContent


class ResourceDefinition(Payload):
    code: str = Field(min_length=1)
    name: str


class TruckModelDefinition(Payload):
    code: str = Field(min_length=1)
    name: str
    capacity: int = Field(gt=0)
    speed: int = Field(gt=0)
    co2_per_distance_unit: int = Field(ge=0)
    price: int = Field(ge=0)


class OfferDefinition(Payload):
    resource_code: str = Field(min_length=1)
    price: int = Field(ge=0)


class RecipeItem(Payload):
    resource_code: str = Field(min_length=1)
    quantity: int = Field(gt=0)


class RecipeDefinition(Payload):
    code: str = Field(min_length=1)
    price: int = Field(ge=0)
    inputs: list[RecipeItem]
    outputs: list[RecipeItem]


class LocationDefinition(Payload):
    id: UUID
    code: str = Field(min_length=1)
    name: str
    kind: Literal["supplier", "factory", "customer"]
    x: int
    y: int
    active: bool
    reputation_delta_per_unit: int
    ethics_note: str | None = None
    offers: list[OfferDefinition] = Field(default_factory=list)
    recipes: list[RecipeDefinition] = Field(default_factory=list)


class WorldContent(Payload):
    starting_balance: int
    seconds_per_distance_unit: int = Field(gt=0)
    resources: list[ResourceDefinition]
    truck_models: list[TruckModelDefinition]
    locations: list[LocationDefinition]


class WorldSnapshot(Payload):
    """Full state of the world; replaces anything the receiver knew before."""

    type: Literal["world_snapshot"]
    content: WorldContent


class LocationRef(Payload):
    location_id: UUID
    location_code: str = Field(min_length=1)


class LocationOpened(Payload):
    type: Literal["location_open"]
    content: LocationRef


class LocationClosedContent(LocationRef):
    reopen_after_minutes: int | None = None


class LocationClosed(Payload):
    type: Literal["location_close"]
    content: LocationClosedContent


class OfferChangedContent(LocationRef):
    resource_code: str = Field(min_length=1)
    price: int = Field(ge=0)


class OfferChanged(Payload):
    type: Literal["offer_change"]
    content: OfferChangedContent


class ReputationChangedContent(LocationRef):
    reputation_delta_per_unit: int


class ReputationChanged(Payload):
    type: Literal["location_reputation_change"]
    content: ReputationChangedContent


Fact = Annotated[
    Pong | ResourcePurchased | TruckLoaded | TruckDispatched | TruckArrived |
    TruckUnloaded | TruckPurchased | RecipeStarted | RecipeCompleted | ScoreUpdated | CommandRejected |
    WorldSnapshot | LocationOpened | LocationClosed | OfferChanged | ReputationChanged,
    Field(discriminator="type"),
]
fact_adapter = TypeAdapter(Fact)


class FactMessage(Payload):
    """Payload of an admin-to-player envelope."""

    team: str | None
    fact: Fact
