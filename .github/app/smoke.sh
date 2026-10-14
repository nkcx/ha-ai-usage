#!/usr/bin/env bash
# Smoke test for the app image: start it with every provider enabled and wait
# until the official installers have installed each CLI and a first collection
# has run. No sign-ins exist, so every provider must report "signed_out".
set -euo pipefail
image="${1:?image}"
data="$(mktemp -d)"
echo '{"claude": true, "gemini": true, "codex": true, "interval_minutes": 5}' > "$data/options.json"
docker run -d --name ai-usage-smoke -v "$data:/data" -p 18080:8080 "$image" >/dev/null
on_exit() {
    status=$?
    docker logs ai-usage-smoke 2>&1 | tail -60
    if [ "$status" -ne 0 ]; then
        echo "--- agy /usage through the app's runner, in the container:"
        docker exec -i ai-usage-smoke python3 - < "$(dirname "$0")/debug_agy.py" 2>&1 | tail -120
        echo "--- agy /usage run directly (stdin closed), in the container:"
        docker exec ai-usage-smoke sh -c \
            'cd /data/work && s=$(date +%s); timeout 90 agy -p /usage --output-format json </dev/null; echo "exit $? after $(( $(date +%s) - s ))s"' 2>&1 | tail -30
    fi
    docker rm -f ai-usage-smoke >/dev/null
}
trap on_exit EXIT

for _ in $(seq 1 60); do
    sleep 10
    # The token file is root-only; read it inside the container.
    token="$(docker exec ai-usage-smoke cat /data/api_token 2>/dev/null)" || continue
    reports="$(curl -fsS -H "Authorization: Bearer $token" \
        http://127.0.0.1:18080/api/v1/reports || true)"
    [ -n "$reports" ] || continue
    if echo "$reports" | jq -e '[.providers[] | select(.report == null)] | length == 0' >/dev/null; then
        echo "$reports" | jq '.providers | map_values({installed, install_error, status: .report.status, error: .report.error})'
        echo "$reports" | jq -e '[.providers[] | select(.installed | not)] | length == 0' >/dev/null \
            || { echo "A CLI failed to install"; exit 1; }
        echo "$reports" | jq -e '[.providers[] | select(.report.status == "not_installed")] | length == 0' >/dev/null \
            || { echo "A CLI is missing"; exit 1; }
        echo "$reports" | jq -e '[.providers[] | select(.report.status != "signed_out")] | length == 0' >/dev/null \
            || { echo "Expected every provider to report signed_out"; exit 1; }
        sudo du -sh "$data/home/.local" "$data/home/.local/bin"/* || true
        exit 0
    fi
done
echo "Timed out waiting for the first collection"
exit 1
