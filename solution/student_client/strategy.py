"""Strategies: decide which commands to send from the game state and the incoming facts.

`AutoPilot` is the default strategy and plays a full supply chain on its own: it picks the
most profitable recipe, fetches the ingredients from suppliers to the factory, starts the
production, delivers the products to a customer, and buys trucks when work is waiting.
Replace it (or tweak its constants) to play differently: any `Strategy` can be plugged into
`GameClient`.
"""

import logging
import math
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from student_client.models import (
    BuyResource,
    BuyTruck,
    BuyTruckContent,
    Command,
    CommandRejected,
    DispatchContent,
    DispatchTruck,
    Fact,
    LoadContent,
    LoadTruck,
    PurchaseContent,
    RecipeContent,
    StartRecipe,
    UnloadContent,
    UnloadTruck,
)
from student_client.state import GameState, Location, Recipe, Truck

logger = logging.getLogger(__name__)

DEPOT = (0, 0)
MAX_JOB_FAILURES = 5
MAX_BACKOFF_SECONDS = 10.0

# Facts that confirm a command, and the fields identifying which command they confirm.
CONFIRMING_FACT = {
    "buy_truck": "truck_purchased",
    "buy_resource": "resource_purchased",
    "load_truck": "truck_loaded",
    "dispatch_truck": "truck_dispatched",
    "unload_truck": "truck_unloaded",
    "start_recipe": "recipe_completed",  # last fact of the command
}
IDENTITY_FIELDS = ("truck_id", "supplier_id", "factory_id", "resource_code", "recipe_code")


@dataclass
class Context:
    """What a strategy can see and do."""

    state: GameState
    send: Callable[[Command], None]
    now: Callable[[], float]


class Strategy(ABC):
    @abstractmethod
    def on_live(self, ctx: Context) -> None:
        """The history of the topic has been replayed: the state is up to date, start playing."""

    @abstractmethod
    def on_facts(self, ctx: Context, facts: Sequence[Fact]) -> None:
        """Facts concerning this team (or everyone) were received and already applied to the state.

        They come in batches: the facts of one command arrive one after the other, and the state is
        only consistent once all of them have been applied.
        """

    def on_tick(self, ctx: Context) -> None:
        """Called about every second, to retry commands that were rejected or got no answer."""

    def on_reset(self, ctx: Context) -> None:
        """Clear planned actions after a live team reset; ctx.state is already reset. Do not send commands here."""


Step = Literal["go_origin", "buy", "load", "go_dest", "unload"]


@dataclass
class Job:
    """Move `quantity` of `resource` from `origin_id` (None: already on the truck) to `dest_id`."""

    id: int
    resource: str
    quantity: int
    origin_id: UUID | None
    dest_id: UUID
    truck_id: UUID | None = None
    step: Step = "go_origin"
    failures: int = 0
    retry_at: float = 0.0


@dataclass
class Pending:
    command: Command
    job_id: int | None
    sent_at: float


@dataclass
class Plan:
    factory: Location
    recipe: Recipe
    suppliers: dict[str, Location]
    customer: Location
    output_resource: str
    output_quantity: int


def distance(a: tuple[int, int], b: tuple[int, int]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def position(location: Location) -> tuple[int, int]:
    return (location.x, location.y)


def _confirms(command: Command, fact: Fact) -> bool:
    """Whether `fact` answers `command`: same type pair and same ids on the fields both carry."""
    if CONFIRMING_FACT[command.type] != fact.type:
        return False
    shared = [f for f in IDENTITY_FIELDS if hasattr(command.content, f) and hasattr(fact.content, f)]
    return all(getattr(command.content, f) == getattr(fact.content, f) for f in shared)


class AutoPilot(Strategy):
    def __init__(
        self,
        max_trucks: int = 3,
        reputation_weight: float = 1.0,
        command_timeout_seconds: float = 30.0,
    ) -> None:
        self.max_trucks = max_trucks
        self.reputation_weight = reputation_weight
        self.command_timeout_seconds = command_timeout_seconds
        self.jobs: list[Job] = []
        self.pending: list[Pending] = []
        self._next_job_id = 1
        self._fleet_retry_at = 0.0
        self._recipe_retry_at = 0.0
        self._live = False

    # -- Strategy interface -------------------------------------------------

    def on_live(self, ctx: Context) -> None:
        self._live = True
        self._tick(ctx)

    def on_facts(self, ctx: Context, facts: Sequence[Fact]) -> None:
        if not self._live:
            return
        for fact in facts:
            self._resolve_pending(ctx, fact)
        self._tick(ctx)

    def on_tick(self, ctx: Context) -> None:
        if self._live:
            self._tick(ctx)

    def on_reset(self, ctx: Context) -> None:
        self.jobs.clear()
        self.pending.clear()
        self._next_job_id = 1
        self._fleet_retry_at = 0.0
        self._recipe_retry_at = 0.0

    # -- Main loop ------------------------------------------------------------

    def _tick(self, ctx: Context) -> None:
        state = ctx.state
        if not state.world_known:
            return
        now = ctx.now()
        self._expire_pending(ctx, now)
        plan = self.choose_plan(state)
        self._complete_jobs(state)
        self._drop_invalid_jobs(state, plan)
        self._recover_cargo(state, plan)
        if plan is not None:
            self._start_recipe(ctx, plan, now)
            self._create_jobs(state, plan)
        self._manage_fleet(ctx, plan, now)
        self._assign_jobs(state)
        self._advance_jobs(ctx, now)

    # -- Planning -------------------------------------------------------------

    def choose_plan(self, state: GameState) -> Plan | None:
        """Pick the factory/recipe/suppliers/customer chain with the best profit per distance travelled."""
        weight = self.reputation_weight
        suppliers = [loc for loc in state.locations_of_kind("supplier") if loc.active]
        customers = [loc for loc in state.locations_of_kind("customer") if loc.active]
        best: tuple[float, Plan] | None = None
        for factory in sorted(state.locations_of_kind("factory"), key=lambda loc: loc.code):
            if not factory.active:
                continue
            for recipe in sorted(factory.recipes.values(), key=lambda r: r.code):
                if len(recipe.outputs) != 1:
                    continue
                (output, out_qty), = recipe.outputs.items()
                chosen: dict[str, Location] = {}
                profit = -recipe.price + weight * factory.reputation_delta_per_unit * out_qty
                trip = 1.0
                for resource, quantity in recipe.inputs.items():
                    options = [s for s in suppliers if resource in s.offers]
                    if not options:
                        break
                    supplier = min(
                        options,
                        key=lambda s: (
                            s.offers[resource] * quantity
                            - weight * s.reputation_delta_per_unit * quantity
                            + 0.1 * distance(position(s), position(factory)),
                            s.code,
                        ),
                    )
                    chosen[resource] = supplier
                    profit -= supplier.offers[resource] * quantity
                    profit += weight * supplier.reputation_delta_per_unit * quantity
                    trip += distance(position(supplier), position(factory))
                else:
                    buyers = [c for c in customers if output in c.offers]
                    if not buyers:
                        continue
                    customer = max(
                        buyers,
                        key=lambda c: (
                            (c.offers[output] + weight * c.reputation_delta_per_unit) * out_qty
                            - 0.1 * distance(position(c), position(factory)),
                            c.code,
                        ),
                    )
                    profit += (customer.offers[output] + weight * customer.reputation_delta_per_unit) * out_qty
                    trip += distance(position(factory), position(customer))
                    if profit > 0 and (best is None or profit / trip > best[0]):
                        best = (profit / trip, Plan(factory, recipe, chosen, customer, output, out_qty))
        return best[1] if best is not None else None

    def _chunk(self, state: GameState) -> int:
        """Units a single truck can carry (smallest capacity among the owned trucks, else the best model)."""
        capacities = [
            state.truck_models[t.model_code].capacity for t in state.trucks.values() if t.model_code in state.truck_models
        ]
        if capacities:
            return min(capacities)
        model = self._best_model(state)
        return model.capacity if model is not None else 1

    @staticmethod
    def _best_model(state: GameState):
        models = list(state.truck_models.values())
        if not models:
            return None
        return max(models, key=lambda m: (m.capacity * m.speed / max(m.price, 1), m.code))

    def _runs_per_batch(self, state: GameState, plan: Plan) -> int:
        biggest = max([*plan.recipe.inputs.values(), plan.output_quantity])
        return max(1, self._chunk(state) // biggest)

    # -- Jobs -----------------------------------------------------------------

    def _new_job(self, **kwargs) -> Job:
        job = Job(id=self._next_job_id, **kwargs)
        self._next_job_id += 1
        self.jobs.append(job)
        return job

    def _create_jobs(self, state: GameState, plan: Plan) -> None:
        chunk = self._chunk(state)
        batches = 2 if len(state.trucks) >= 2 else 1
        target_runs = self._runs_per_batch(state, plan) * batches
        factory = plan.factory
        for resource, per_run in plan.recipe.inputs.items():
            supply = state.stock_at(factory.id, resource) + sum(
                job.quantity for job in self.jobs if job.resource == resource and job.dest_id == factory.id
            )
            missing = target_runs * per_run - supply
            while missing > 0:
                quantity = min(chunk, missing)
                self._new_job(
                    resource=resource, quantity=quantity,
                    origin_id=plan.suppliers[resource].id, dest_id=factory.id,
                )
                missing -= quantity

        waiting = state.stock_at(factory.id, plan.output_resource) - sum(
            job.quantity
            for job in self.jobs
            if job.resource == plan.output_resource
            and job.origin_id == factory.id
            and job.step in ("go_origin", "buy", "load")
        )
        while waiting > 0:
            quantity = min(chunk, waiting)
            self._new_job(
                resource=plan.output_resource, quantity=quantity,
                origin_id=factory.id, dest_id=plan.customer.id,
            )
            waiting -= quantity

    def _complete_jobs(self, state: GameState) -> None:
        remaining = []
        for job in self.jobs:
            truck = state.trucks.get(job.truck_id) if job.truck_id is not None else None
            if job.step == "unload" and truck is not None and truck.cargo_quantity == 0:
                logger.info("Job %d done: %d %s delivered", job.id, job.quantity, job.resource)
                continue
            remaining.append(job)
        self.jobs = remaining

    def _drop_invalid_jobs(self, state: GameState, plan: Plan | None) -> None:
        kept = []
        for job in self.jobs:
            truck = state.trucks.get(job.truck_id) if job.truck_id is not None else None
            loaded = truck is not None and truck.cargo_resource == job.resource and truck.cargo_quantity > 0
            origin = state.locations.get(job.origin_id) if job.origin_id is not None else None
            dest = state.locations.get(job.dest_id)
            if dest is None:
                continue
            if not loaded and origin is not None and not origin.active:
                logger.info("Job %d dropped: %s is closed", job.id, origin.name)
                continue
            if not dest.active:
                new_dest = self._alternative_destination(state, plan, job.resource, dest)
                if loaded and new_dest is not None:
                    job.dest_id = new_dest.id
                    if job.step == "unload":
                        job.step = "go_dest"
                elif not loaded:
                    logger.info("Job %d dropped: %s is closed", job.id, dest.name)
                    continue
            kept.append(job)
        self.jobs = kept

    def _alternative_destination(
        self, state: GameState, plan: Plan | None, resource: str, closed: Location
    ) -> Location | None:
        if plan is not None and resource in plan.recipe.inputs and plan.factory.active:
            return plan.factory
        if closed.kind == "customer":
            customers = [c for c in state.locations_of_kind("customer") if c.active and resource in c.offers]
            return max(customers, key=lambda c: (c.offers[resource] + self.reputation_weight * c.reputation_delta_per_unit, c.code), default=None)
        return None

    def _recover_cargo(self, state: GameState, plan: Plan | None) -> None:
        """Give a job to every truck that carries something without a job (after a restart or a failure)."""
        assigned = {job.truck_id for job in self.jobs}
        for truck in state.trucks.values():
            if truck.cargo_quantity == 0 or truck.id in assigned or truck.cargo_resource is None:
                continue
            resource = truck.cargo_resource
            dest: Location | None = None
            if plan is not None and resource in plan.recipe.inputs:
                dest = plan.factory
            elif plan is not None and resource == plan.output_resource:
                dest = plan.customer
            else:
                buyers = [c for c in state.locations_of_kind("customer") if c.active and resource in c.offers]
                dest = max(buyers, key=lambda c: (c.offers[resource], c.code), default=None)
            if dest is None:
                continue
            self._new_job(
                resource=resource, quantity=truck.cargo_quantity, origin_id=None,
                dest_id=dest.id, truck_id=truck.id, step="go_dest",
            )

    def _assign_jobs(self, state: GameState) -> None:
        busy = {job.truck_id for job in self.jobs if job.truck_id is not None}
        idle = [t for t in state.trucks.values() if t.id not in busy and t.cargo_quantity == 0]
        for job in self.jobs:
            if job.truck_id is not None or not idle:
                continue
            origin = state.locations.get(job.origin_id) if job.origin_id is not None else None
            target = position(origin) if origin is not None else DEPOT
            truck = min(idle, key=lambda t: (distance(self._truck_position(state, t), target), str(t.id)))
            idle.remove(truck)
            job.truck_id = truck.id

    @staticmethod
    def _truck_position(state: GameState, truck: Truck) -> tuple[int, int]:
        location_id = truck.location_id if truck.location_id is not None else truck.destination_id
        location = state.locations.get(location_id) if location_id is not None else None
        return position(location) if location is not None else DEPOT

    def _advance_jobs(self, ctx: Context, now: float) -> None:
        state = ctx.state
        awaiting_answer = {p.job_id for p in self.pending}
        for job in list(self.jobs):
            if job.truck_id is None or job.id in awaiting_answer or now < job.retry_at:
                continue
            truck = state.trucks.get(job.truck_id)
            if truck is None or truck.travelling:
                continue
            command = self._next_command(state, job, truck)
            if command is not None:
                self._send(ctx, command, job)

    def _next_command(self, state: GameState, job: Job, truck: Truck) -> Command | None:
        for _ in range(4):
            if job.step in ("go_origin", "buy", "load") and (
                truck.cargo_resource == job.resource and truck.cargo_quantity >= job.quantity
            ):
                job.step = "go_dest"
            origin = state.locations.get(job.origin_id) if job.origin_id is not None else None

            if job.step == "go_origin":
                if origin is None:
                    job.step = "go_dest"
                elif truck.location_id == origin.id:
                    job.step = "buy" if origin.kind == "supplier" else "load"
                else:
                    return DispatchTruck(
                        type="dispatch_truck", content=DispatchContent(truck_id=truck.id, destination_id=origin.id)
                    )
            elif job.step == "buy":
                assert origin is not None
                if state.stock_at(origin.id, job.resource) >= job.quantity:
                    job.step = "load"
                    continue
                price = origin.offers.get(job.resource)
                if price is None or state.balance < price * job.quantity:
                    return None
                return BuyResource(
                    type="buy_resource",
                    content=PurchaseContent(supplier_id=origin.id, resource_code=job.resource, quantity=job.quantity),
                )
            elif job.step == "load":
                assert origin is not None
                if state.stock_at(origin.id, job.resource) >= job.quantity:
                    return LoadTruck(
                        type="load_truck",
                        content=LoadContent(truck_id=truck.id, resource_code=job.resource, quantity=job.quantity),
                    )
                if origin.kind == "supplier":
                    job.step = "buy"
                    continue
                self.jobs.remove(job)
                return None
            elif job.step == "go_dest":
                if truck.location_id == job.dest_id:
                    job.step = "unload"
                else:
                    return DispatchTruck(
                        type="dispatch_truck", content=DispatchContent(truck_id=truck.id, destination_id=job.dest_id)
                    )
            else:
                if truck.cargo_quantity == 0:
                    return None
                return UnloadTruck(
                    type="unload_truck",
                    content=UnloadContent(truck_id=truck.id, quantity=truck.cargo_quantity),
                )
        return None

    # -- Production and fleet -------------------------------------------------

    def _start_recipe(self, ctx: Context, plan: Plan, now: float) -> None:
        state = ctx.state
        if now < self._recipe_retry_at or any(p.command.type == "start_recipe" for p in self.pending):
            return
        runs = min(
            (state.stock_at(plan.factory.id, resource) // quantity for resource, quantity in plan.recipe.inputs.items()),
            default=0,
        )
        batch = self._runs_per_batch(state, plan)
        inputs_on_the_way = any(
            job.dest_id == plan.factory.id and job.resource in plan.recipe.inputs for job in self.jobs
        )
        if runs < batch and inputs_on_the_way:
            return
        runs = min(runs, batch * 2)
        if plan.recipe.price > 0:
            runs = min(runs, state.balance // plan.recipe.price)
        if runs >= 1:
            self._send(
                ctx,
                StartRecipe(
                    type="start_recipe",
                    content=RecipeContent(
                        factory_id=plan.factory.id, recipe_code=plan.recipe.code, quantity=runs
                    ),
                ),
                None,
            )

    def _manage_fleet(self, ctx: Context, plan: Plan | None, now: float) -> None:
        state = ctx.state
        if (
            plan is None
            or now < self._fleet_retry_at
            or len(state.trucks) >= self.max_trucks
            or any(p.command.type == "buy_truck" for p in self.pending)
        ):
            return
        model = self._best_model(state)
        if model is None:
            return
        assigned = {job.truck_id for job in self.jobs if job.truck_id is not None}
        idle_trucks = [t for t in state.trucks.values() if t.id not in assigned and t.cargo_quantity == 0]
        waiting_jobs = [job for job in self.jobs if job.truck_id is None]
        if state.trucks and not (waiting_jobs and not idle_trucks):
            return
        run_cost = plan.recipe.price + sum(
            plan.suppliers[resource].offers[resource] * quantity for resource, quantity in plan.recipe.inputs.items()
        )
        reserve = run_cost if state.trucks else 0
        if state.balance >= model.price + reserve:
            self._send(ctx, BuyTruck(type="buy_truck", content=BuyTruckContent(model_code=model.code)), None)

    # -- Command bookkeeping --------------------------------------------------

    def _send(self, ctx: Context, command: Command, job: Job | None) -> None:
        self.pending.append(Pending(command, job.id if job is not None else None, ctx.now()))
        logger.info("Sending %s %s", command.type, command.content.model_dump(mode="json"))
        ctx.send(command)

    def _resolve_pending(self, ctx: Context, fact: Fact) -> None:
        if isinstance(fact, CommandRejected):
            for pending in self.pending:
                if pending.command.type == fact.content.command_type:
                    self.pending.remove(pending)
                    self._on_failure(pending, ctx.now())
                    return
            return
        for pending in self.pending:
            if _confirms(pending.command, fact):
                self.pending.remove(pending)
                return

    def _expire_pending(self, ctx: Context, now: float) -> None:
        for pending in list(self.pending):
            if now - pending.sent_at > self.command_timeout_seconds:
                logger.warning("No answer to %s, giving up on it", pending.command.type)
                self.pending.remove(pending)
                self._on_failure(pending, now)

    def _on_failure(self, pending: Pending, now: float) -> None:
        command_type = pending.command.type
        if command_type == "buy_truck":
            self._fleet_retry_at = now + MAX_BACKOFF_SECONDS
        elif command_type == "start_recipe":
            self._recipe_retry_at = now + MAX_BACKOFF_SECONDS / 2
        job = next((j for j in self.jobs if j.id == pending.job_id), None)
        if job is None:
            return
        job.failures += 1
        if job.failures >= MAX_JOB_FAILURES:
            logger.warning("Job %d dropped after %d failures", job.id, job.failures)
            self.jobs.remove(job)
        else:
            job.retry_at = now + min(2.0**job.failures, MAX_BACKOFF_SECONDS)
