# client-student

Autonomous player for the Conf Techno logistics game. 

- It replays `game.facts` (admin → players), verifies the admin's signature on every
  message and rebuilds the state of the game from the facts: the world (locations with
  their UUIDs, offers, recipes, truck models), its trucks, its stock and its scores.
- A strategy decides what to do and sends signed commands on `game.commands`
  (players → admin): buy resources from suppliers, produce at the factory, sell to
  customers, with trucks doing every transport, and buy more trucks when work is waiting.

The default strategy, `AutoPilot` in [student_client/strategy.py](student_client/strategy.py),
plays on its own. Any `Strategy` subclass can replace it in
[student_client/__main__.py](student_client/__main__.py).

## Run

1. Register your team on the admin-panel (`/register`). The page shows the password, your
   key pair, the admin public key and a ready-to-use `.env`.
2. Save that `.env` in this directory (see [.env.example](.env.example)). PEM keys can be
   written on one line with `\n` for the line breaks.
3. Start the player:

```bash
uv sync
uv run python -m student_client
```

To only check that the admin-panel can be reached (exit code 0 on success, 1 otherwise):

```
uv run python -m student_client ping
```

The player also sends this ping at every startup and logs `Ping OK` or `Ping FAILED`.

At startup the player checks that `TEAM_USERNAME`, `TEAM_PRIVATE_KEY`, `TEAM_PUBLIC_KEY`
and `ADMIN_PUBLIC_KEY` are set, are valid Ed25519 PEM keys, and that the private key matches
the public key. It refuses to start (exit code 1) and lists every problem otherwise.

| Variable | Meaning |
|---|---|
| `TEAM_USERNAME`, `TEAM_PRIVATE_KEY`, `TEAM_PUBLIC_KEY` | Team identity from the registration page |
| `ADMIN_PUBLIC_KEY`, `ADMIN_USERNAME` | Admin identity whose signature is required on every fact (default name `admin`) |
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka listener (default `localhost:9092`) |
| `GAME_COMMANDS_TOPIC`, `GAME_FACTS_TOPIC` | Topics (defaults `game.commands`, `game.facts`) |
| `MAX_TRUCKS`, `REPUTATION_WEIGHT`, `COMMAND_TIMEOUT_SECONDS`, `LOG_LEVEL` | Autopilot tuning and logging |
| `PING_TIMEOUT_SECONDS` | How long to wait for the pong (default 10) |

## Messages

Both topics carry the same signed envelope, `{"username", "seq", "payload", "signature"}`;
the signature (Ed25519, base64) covers the canonical JSON of `{"seq", "payload"}`.

- **Commands** (`game.commands`): payload `{"type": ..., "content": ...}`, signed with the team
  key, among `buy_resource`, `load_truck`, `dispatch_truck`, `unload_truck`, `buy_truck`,
  `start_recipe`. Game commands are rejected (`command_rejected`) until the admin has started
  the game, and while it is paused or over. `ping` (`{"nonce": str}`) is always accepted and
  answered by a `pong` fact addressed to the team: `{"nonce", "game_status", "server_time"}`
  (`game_status` is `not_started`, `running`, `paused` or `finished`).
- **Facts** (`game.facts`): payload `{"team": str | null, "fact": {"type": ..., "content": ...}}`,
  signed with the admin key. `team` is `null` for broadcasts (`world_snapshot`,
  `location_open`, `location_close`, `offer_change`, `location_reputation_change`). A message
  that is not JSON, not signed by the admin, replayed (`seq` not increasing), or whose payload
  is unknown is logged and ignored (poison pill). Facts of other teams are ignored.

The business classes (commands and facts) are in [student_client/models.py](student_client/models.py),
a mirror of `admin-panel/app/models/payloads.py`; a test in the admin-panel checks that both
stay identical. The state is rebuilt by [student_client/state.py](student_client/state.py).

Because the state only comes from the facts, the player always replays `game.facts` from its
first message when it starts, then plays once it has caught up.

An organizer's team reset emits `score_updated` with `content.reset: true`.
It clears team trucks and stocks, restores the supplied scores and preserves
the world. The full Kafka history stays available; replay applies the same reset.
Missing `reset` means `false`, so ordinary score updates do not remove anything.

During live play, `Strategy.on_reset(ctx)` is called after the state is cleared.
Use it to discard pending commands and planned jobs, without sending commands.
The client discards pre-reset notifications in its current batch, then resumes
`on_facts` with the reset and subsequent facts. During replay, only the state is
updated; the strategy starts with `on_live` once the whole history is read.
Global resets and user deletion do not emit reset facts.

## Read-only HTTP routes (admin-panel)

To look at your team from outside Kafka (the world itself is never exposed over HTTP):

- `GET /teams/{username}/account` — money balance and reputation.
- `GET /teams/{username}/trucks` — each truck with its id, position or destination, cargo.

## Tests

```bash
uv run pytest
```

This is the instructor's regression suite for the complete reference client.
The student-facing exercise tests live separately in
[client-student-skeleton/tests](../client-student-skeleton/tests).

The end-to-end test that makes the autopilot play against the real game engine lives in
`admin-panel/tests/test_client_contract.py`.
