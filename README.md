# FirePhenix Backend

[![CI/CD](https://github.com/dev-lukas/firephenix-backend/actions/workflows/deploy.yml/badge.svg)](https://github.com/dev-lukas/firephenix-backend/actions/workflows/deploy.yml)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](pyproject.toml)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Website](https://img.shields.io/website?url=https%3A%2F%2Ffirephenix.de&label=firephenix.de)](https://firephenix.de)

The backend of the [FirePhenix](https://firephenix.de) gaming community: a
Flask API and a single-process bot that tracks voice activity on **TeamSpeak**
([atsq](https://github.com/dev-lukas/atsq), SSH ServerQuery) and **Discord**
(discord.py) to drive a ranking system — levels, seasonal divisions,
achievements, owned channels — plus Steam-verified account linking. The bot
runs everything on one asyncio event loop with a natively async database layer
(asyncmy); the API talks to the same MariaDB via PyMySQL and to the bot via a
Valkey command bus.

## Install

```
uv sync
cp .env.example .env   # then fill in the secrets
```

Requires Python 3.12+, a MariaDB and a Valkey instance, a TeamSpeak server
with SSH ServerQuery enabled (`TS3SERVER_QUERY_PROTOCOLS=raw,ssh`, port
10022), and a Discord bot token. The database schema is created automatically
on first connect.

## Run

```
uv run flask run                    # website API (dev)
uv run python bot_runner.py         # ranking bot (TeamSpeak + Discord)
```

For production, serve the API with [Gunicorn](https://gunicorn.org/):
`gunicorn --bind 0.0.0.0:5000 run:app`. Authenticated write requests need the
`X-CSRF-Token` header, returned by `/api/auth/check` after Steam login.

<details>
<summary><b>Example .env</b></summary>

The full annotated template lives in [`.env.example`](.env.example):

```ini
# Flask / security
SECRET_KEY=                       # randomized cookie secret
SITE_URL=https://firephenix.de
CORS_ORIGINS=https://firephenix.de  # comma-separated allowed browser origins
ADMIN_STEAM_IDS=                  # comma-separated steamID64s with admin API access

# Database (MariaDB, database name "firephenix")
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=firephenix
DB_PASSWORD=

# Valkey (command bus, online-user cache, rate limiter)
VALKEY_HOST=localhost
VALKEY_PORT=6379
VALKEY_USERNAME=                  # optional ACL user
VALKEY_PASSWORD=                  # optional ACL password
LIMITER_STORAGE_URI=valkey://localhost:6379   # memory:// for local tests
LIMITER_KEY_PREFIX=firephenix:limiter

# TeamSpeak (SSH ServerQuery)
TS3_HOST=127.0.0.1
TS3_PORT=10022
TS3_PASSWORD=                     # serveradmin query password

# Discord
DISCORD_TOKEN=

# External services (optional features)
OPENROUTER_API_KEY=               # Ember AI chat on Discord
VPNAPI_API_KEY=                   # VPN/Tor kick for low-level TS users
TTT_STATUS_HOST=firephenix.de     # TTT gameserver status probe
TTT_STATUS_PORT=27015
```

Non-secret settings (guild/channel/group ids, rank thresholds, ports) live in
the `Config` class in `app/config.py`.
</details>

<details>
<summary><b>Deploy (Docker)</b></summary>

The production image serves the API by default; the bot runs from the same
image with a different command:

```
docker build -t firephenix-backend .
docker run --env-file .env -p 5000:5000 firephenix-backend
docker run --env-file .env firephenix-backend python bot_runner.py
```

CI (`.github/workflows/deploy.yml`) builds the image, runs the unit suite
inside it, boots a full compose stack (MariaDB, Valkey, website) and a real
TeamSpeak server against the bot, and publishes to the private registry on
main. Production deploys are manual:
`gh workflow run deploy.yml --ref main -f deploy_production=true -f deploy_services="backend bot"`.
</details>

<details>
<summary><b>Architecture</b></summary>

- **`run.py` / `app/api/`** — Flask API (sync, gunicorn): profiles, rankings,
  verification, admin. Database via `app/utils/database.py` (PyMySQL).
- **`bot_runner.py` / `app/rankingsystem/`** — one asyncio event loop running
  the Discord bot, the TeamSpeak bot (atsq), the Valkey pubsub command
  listener, the TTT achievement stream consumer and the minute ranking tick
  as sibling tasks. Database via `app/utils/async_database.py` (asyncmy).
- **API ↔ bot**: the API publishes commands (`create_owned_channel`,
  `send_verification`, …) on Valkey pubsub and reads results from short-lived
  keys; the bot publishes online users the same way.
- Placeholder style is `%s` everywhere (PyMySQL family); no C build
  dependencies — the image is pure wheels.
</details>

## Testing

```
uv run python -m unittest discover -s tests   # unit tests, no infrastructure
scripts/run-integration-tests.sh              # + real MariaDB/Valkey (docker)
```

Integration tests live in `tests/integration/` and are skipped unless
`RUN_INTEGRATION_TESTS=1` is set. CI additionally smoke-tests the built image
against a real TeamSpeak server (`.github/ci/bot-teamspeak-smoke.sh`).
