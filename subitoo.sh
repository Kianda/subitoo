#!/bin/bash

# Navigate to the directory where the script is located
cd "$(dirname "$0")" || exit

LAST_PULL_FILE=".last_docker_pull"
TODAY=$(date +%Y%m%d)

# Auto-pull once a day
if [ ! -f "$LAST_PULL_FILE" ] || [ "$(cat "$LAST_PULL_FILE")" -lt "$TODAY" ]; then
    docker compose pull
    echo "$TODAY" > "$LAST_PULL_FILE"
fi

docker compose run --rm subitoo "$@"
