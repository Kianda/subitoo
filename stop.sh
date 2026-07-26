#!/usr/bin/env bash
# Stop the subitoo stack. Your data in ./data is always preserved.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Stopping subitoo (database in ./data is preserved)..."
docker compose down
echo "Stopped. Restart with ./start.sh — your queries and listings are kept."
