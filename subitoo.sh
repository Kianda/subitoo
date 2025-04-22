#!/bin/bash

# Navigate to the directory where the script is located
cd "$(dirname "$0")" || exit

docker compose run --rm subitoo "$@"
