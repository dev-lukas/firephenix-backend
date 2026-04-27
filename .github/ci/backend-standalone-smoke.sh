#!/usr/bin/env bash
set -euo pipefail

image="${IMAGE_UNDER_TEST:?IMAGE_UNDER_TEST is required}"
container_id="$(docker run -d --rm \
  -p 127.0.0.1::5000 \
  -e SECRET_KEY=ci-secret \
  -e SITE_URL=http://localhost \
  -e LIMITER_STORAGE_URI=memory:// \
  "$image")"

cleanup() {
  docker stop "$container_id" >/dev/null 2>&1 || true
}
trap cleanup EXIT

port="$(docker inspect --format '{{ (index (index .NetworkSettings.Ports "5000/tcp") 0).HostPort }}' "$container_id")"
response_file="$(mktemp)"
error_file="$(mktemp)"

for _ in $(seq 1 45); do
  if curl -fsS "http://127.0.0.1:${port}/api/auth/check" -o "$response_file" 2>"$error_file"; then
    if grep -Fq '"authenticated":false' "$response_file"; then
      exit 0
    fi
  fi
  sleep 1
done

echo "Backend standalone smoke failed for /api/auth/check" >&2
cat "$error_file" >&2 || true
cat "$response_file" >&2 || true
docker logs "$container_id" >&2 || true
exit 1
