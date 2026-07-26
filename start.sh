#!/usr/bin/env bash
# Start the whole subitoo stack and wait until it's healthy.
#
#   ./start.sh          # pull the published images and start (normal users)
#   ./start.sh --dev    # build the images from local source instead (contributors)
#
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
    echo "==> No .env found; creating it from .env.example (fill in Pushover to get alerts)."
    cp .env.example .env
fi

COMPOSE=(docker compose)
if [[ "${1:-}" == "--dev" ]]; then
    echo "==> Dev mode: building images from local source (docker-compose.dev.yml)."
    COMPOSE+=(-f docker-compose.dev.yml)
    "${COMPOSE[@]}" up -d --build
else
    echo "==> Pulling published images (pin a version with SUBITOO_VERSION in .env)..."
    "${COMPOSE[@]}" pull
    echo "==> Starting subitoo..."
    "${COMPOSE[@]}" up -d
fi

echo "==> Waiting for services to become healthy..."
for i in $(seq 1 30); do
    if "${COMPOSE[@]}" exec -T subitoo subitoo doctor >/dev/null 2>&1; then
        echo
        "${COMPOSE[@]}" exec -T subitoo subitoo doctor || true
        echo
        echo "✅ subitoo is up. Manage it with the ./subitoo wrapper, e.g.:"
        echo "     ./subitoo query add        # create a search (interactive)"
        echo "     ./subitoo query list       # see your queries"
        echo "     ./subitoo --help           # all commands"
        echo "   Stop it with:  ./stop.sh"
        exit 0
    fi
    sleep 2
done

echo "⚠️  Started, but health checks didn't pass in time."
echo "    Inspect logs:  docker compose logs subitoo_browser   /   docker compose logs subitoo"
exit 1
