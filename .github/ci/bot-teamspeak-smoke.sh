#!/usr/bin/env bash
# End-to-end smoke: the bot must connect to a real TeamSpeak ServerQuery.
#
# This is the check that would have caught the Python 3.14 regression: 3.13+
# removed the stdlib `telnetlib` that the `ts3` library imports, so the bot
# crashed on startup while the backend and unit tests stayed green. Here we
# stand up the exact server the bot talks to in production (teamspeak:3.13,
# raw/telnet ServerQuery) and require the bot to actually connect and run its
# initial `clientlist` scan against it.
set -euo pipefail

bot_image="${IMAGE_UNDER_TEST:?IMAGE_UNDER_TEST is required}"
workdir="$(mktemp -d)"
compose="docker compose -f ${workdir}/docker-compose.yml"

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "=== bot-teamspeak smoke failed; dumping logs ===" >&2
    $compose logs --no-color >&2 2>&1 || true
  fi
  $compose down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$workdir"
  exit "$status"
}
trap cleanup EXIT

# Widen the ServerQuery IP allowlist so the bot container (a different IP on the
# compose network) is accepted. The default only permits loopback. This network
# is ephemeral and isolated, so 0.0.0.0/0 is fine for the test only.
printf '127.0.0.1\n::1\n0.0.0.0/0\n' > "$workdir/query_ip_allowlist.txt"

cat > "$workdir/docker-compose.yml" <<YAML
name: fp-bot-teamspeak-smoke
networks:
  itnet:
services:
  valkey:
    image: valkey/valkey:8-alpine
    networks: [itnet]
    healthcheck:
      test: ["CMD", "valkey-cli", "ping"]
      interval: 2s
      timeout: 5s
      retries: 15
  mariadb:
    image: mariadb:11
    environment:
      MARIADB_ROOT_PASSWORD: ci-root
      MARIADB_DATABASE: firephenix
      MARIADB_USER: firephenix
      MARIADB_PASSWORD: ci-password
    networks: [itnet]
    healthcheck:
      test: ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"]
      interval: 2s
      timeout: 5s
      retries: 30
  teamspeak:
    image: teamspeak:3.13
    environment:
      TS3SERVER_LICENSE: accept
      TS3SERVER_QUERY_PROTOCOLS: raw
    volumes:
      - ./query_ip_allowlist.txt:/var/ts3server/query_ip_allowlist.txt
    networks: [itnet]
    healthcheck:
      test: ["CMD-SHELL", "echo quit | nc localhost 10011 | grep -q TS3"]
      interval: 5s
      timeout: 5s
      retries: 24
  bot:
    image: ${bot_image}
    command: ["python", "bot_runner.py"]
    environment:
      SECRET_KEY: ci-secret
      DB_HOST: mariadb
      DB_PORT: "3306"
      DB_USER: firephenix
      DB_PASSWORD: ci-password
      DB_NAME: firephenix
      VALKEY_HOST: valkey
      VALKEY_PORT: "6379"
      LIMITER_STORAGE_URI: valkey://valkey:6379
      TS3_HOST: teamspeak
      TS3_PORT: "10011"
      TS3_PASSWORD: "\${TS3_PASSWORD:-}"
      BOT_RUNNER_PID_FILE: /tmp/bot_runner.pid
    networks: [itnet]
    depends_on:
      valkey: { condition: service_healthy }
      mariadb: { condition: service_healthy }
      teamspeak: { condition: service_healthy }
YAML

echo "Starting valkey, mariadb and teamspeak..."
$compose up -d valkey mariadb teamspeak

echo "Waiting for teamspeak ServerQuery to become healthy..."
for _ in $(seq 1 40); do
  status="$($compose ps teamspeak --format '{{.Health}}' 2>/dev/null || true)"
  [ "$status" = "healthy" ] && break
  sleep 3
done
if [ "${status:-}" != "healthy" ]; then
  echo "teamspeak did not become healthy in time" >&2
  exit 1
fi

# The teamspeak image generates the serveradmin ServerQuery password on first
# boot and logs it once; capture it to authenticate the bot.
echo "Reading generated serveradmin ServerQuery password..."
ts3_password="$($compose logs teamspeak 2>&1 \
  | grep -oE 'loginname= "serveradmin", password= "[^"]+"' \
  | grep -oE 'password= "[^"]+"' | grep -oE '"[^"]+"$' | tr -d '"' | head -1)"
if [ -z "$ts3_password" ]; then
  echo "Could not parse serveradmin password from teamspeak logs" >&2
  exit 1
fi

echo "Starting bot against the live teamspeak server..."
TS3_PASSWORD="$ts3_password" $compose up -d bot

# "Initial client scan complete." is emitted (INFO) only after the bot imports
# ts3, opens the telnet ServerQuery connection, selects the virtual server and
# runs its first clientlist query -- i.e. it proves a working connection.
success_marker="Initial client scan complete."
failure_marker="TS3 connection error"
echo "Waiting for the bot to connect (marker: '${success_marker}')..."
connected=0
for _ in $(seq 1 30); do
  logs="$($compose logs bot 2>&1 || true)"
  if printf '%s' "$logs" | grep -qF "$success_marker"; then
    connected=1
    break
  fi
  state="$($compose ps bot --format '{{.State}}' 2>/dev/null || true)"
  if [ "$state" = "exited" ]; then
    echo "Bot container exited during startup" >&2
    break
  fi
  sleep 3
done

if [ "$connected" -ne 1 ]; then
  echo "Bot did not connect to teamspeak in time." >&2
  if printf '%s' "${logs:-}" | grep -qF "$failure_marker"; then
    echo "(bot reported '${failure_marker}')" >&2
  fi
  exit 1
fi

echo "Bot successfully connected to the teamspeak ServerQuery. Smoke passed."
