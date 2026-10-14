#!/usr/bin/env bash
# Smoke test for the app image: start it with every provider enabled and wait
# until the official installers have installed each CLI and a first collection
# has run. No sign-ins exist, so every provider must end up "signed_out" (or
# report what it says instead, printed for review).
set -euo pipefail
image="${1:?image}"
data="$(mktemp -d)"
echo '{"claude": true, "gemini": true, "codex": true, "interval_minutes": 5}' > "$data/options.json"
docker run -d --name ai-usage-smoke -v "$data:/data" -p 18080:8080 "$image" >/dev/null
trap 'docker logs ai-usage-smoke 2>&1 | tail -60; docker rm -f ai-usage-smoke >/dev/null' EXIT

for _ in $(seq 1 60); do
    sleep 10
    [ -f "$data/api_token" ] || continue
    reports="$(curl -fsS -H "Authorization: Bearer $(cat "$data/api_token")" \
        http://127.0.0.1:18080/api/v1/reports || true)"
    [ -n "$reports" ] || continue
    if echo "$reports" | jq -e '[.providers[] | select(.report == null)] | length == 0' >/dev/null; then
        echo "$reports" | jq '.providers | map_values({installed, install_error, status: .report.status, error: .report.error})'
        echo "$reports" | jq -e '[.providers[] | select(.installed | not)] | length == 0' >/dev/null \
            || { echo "A CLI failed to install"; exit 1; }
        echo "$reports" | jq -e '[.providers[] | select(.report.status == "not_installed")] | length == 0' >/dev/null \
            || { echo "A CLI is missing"; exit 1; }
        echo "$reports" | jq -e '.providers.claude.report.status == "signed_out" and .providers.codex.report.status == "signed_out"' >/dev/null \
            || { echo "Expected Claude and Codex to report signed_out"; exit 1; }
        du -sh "$data/home/.local" || true
        exit 0
    fi
done
echo "Timed out waiting for the first collection"
exit 1
