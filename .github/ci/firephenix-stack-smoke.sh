#!/usr/bin/env bash
set -euo pipefail

backend_image="${IMAGE_UNDER_TEST:?IMAGE_UNDER_TEST is required}"
registry="${REGISTRY:-registry.lukas-roth.dev}"
registry_username="${REGISTRY_USERNAME:-registry}"
website_image="${WEBSITE_IMAGE:-${registry}/firephenix-website:latest}"
workdir="$(mktemp -d)"

cleanup() {
  status=$?
  if [ "$status" -ne 0 ] && [ -f "$workdir/docker-compose.ci.yml" ]; then
    docker compose -f "$workdir/docker-compose.ci.yml" logs --no-color >&2 || true
  fi
  docker compose -f "$workdir/docker-compose.ci.yml" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$workdir"
  exit "$status"
}
trap cleanup EXIT

if [ -n "${REGISTRY_PASSWORD:-}" ]; then
  printf '%s' "$REGISTRY_PASSWORD" | docker login "$registry" -u "$registry_username" --password-stdin
fi
if ! docker image inspect "$website_image" >/dev/null 2>&1; then
  docker pull "$website_image"
fi

cat > "$workdir/nginx.ci.conf" <<'NGINX'
events {}

http {
  server {
    listen 80;
    server_name _;

    location /api/ {
      proxy_pass http://backend:5000;
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
      proxy_pass http://website:80;
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
    }
  }
}
NGINX

cat > "$workdir/docker-compose.ci.yml" <<'YAML'
services:
  mariadb:
    image: mariadb:11
    environment:
      MARIADB_ROOT_PASSWORD: ci-root
      MARIADB_DATABASE: firephenix
      MARIADB_USER: firephenix
      MARIADB_PASSWORD: ci-password

  valkey:
    image: valkey/valkey:8-alpine

  backend:
    image: ${BACKEND_IMAGE}
    environment:
      SECRET_KEY: ci-secret
      SITE_URL: http://localhost
      DB_HOST: mariadb
      DB_PORT: "3306"
      DB_USER: firephenix
      DB_PASSWORD: ci-password
      VALKEY_HOST: valkey
      VALKEY_PORT: "6379"
      LIMITER_STORAGE_URI: valkey://valkey:6379
      CORS_ORIGINS: http://localhost
      TS3_HOST: teamspeak-disabled
      TS3_PASSWORD: ci-disabled
    depends_on:
      - mariadb
      - valkey
    ports:
      - "127.0.0.1::5000"

  website:
    image: ${WEBSITE_IMAGE}
    depends_on:
      - backend
    ports:
      - "127.0.0.1::80"

  edge:
    image: nginx:alpine
    depends_on:
      - backend
      - website
    volumes:
      - ./nginx.ci.conf:/etc/nginx/nginx.conf:ro
    ports:
      - "127.0.0.1::80"
YAML

export BACKEND_IMAGE="$backend_image"
export WEBSITE_IMAGE="$website_image"
docker compose -f "$workdir/docker-compose.ci.yml" up -d

backend_port="$(docker compose -f "$workdir/docker-compose.ci.yml" port backend 5000 | awk -F: '{print $NF}')"
edge_port="$(docker compose -f "$workdir/docker-compose.ci.yml" port edge 80 | awk -F: '{print $NF}')"

for path in /api/auth/check /api/ranking/stats /api/ranking/top "/api/user/online?platform=discord"; do
  response_file="$(mktemp)"
  error_file="$(mktemp)"
  ok=0
  echo "Waiting for backend endpoint ${path}..."
  for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:${backend_port}${path}" -o "$response_file" 2>"$error_file"; then
      if [ "$path" = "/api/auth/check" ] && ! grep -Fq '"authenticated":false' "$response_file"; then
        sleep 1
        continue
      fi
      ok=1
      break
    fi
    sleep 1
  done

  if [ "$ok" -ne 1 ]; then
    echo "Backend integration smoke failed for ${path}" >&2
    cat "$error_file" >&2 || true
    cat "$response_file" >&2 || true
    exit 1
  fi
  echo "Backend endpoint ${path} is ready."
done

echo "Running backend integration test suite against the live stack..."
docker compose -f "$workdir/docker-compose.ci.yml" exec -T \
  -e RUN_INTEGRATION_TESTS=1 \
  -e ADMIN_STEAM_IDS=76561198000000001 \
  backend python3 -m unittest discover -s tests/integration -t /app -v

for path in / /ranking /wiki /profile; do
  response_file="$(mktemp)"
  error_file="$(mktemp)"
  ok=0
  echo "Waiting for website route ${path}..."
  for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:${edge_port}${path}" -o "$response_file" 2>"$error_file" &&
      grep -Fq '<div id="app">' "$response_file"; then
      ok=1
      break
    fi
    sleep 1
  done

  if [ "$ok" -ne 1 ]; then
    echo "Website integration smoke failed for ${path}" >&2
    cat "$error_file" >&2 || true
    cat "$response_file" >&2 || true
    exit 1
  fi
  echo "Website route ${path} is ready."
done
