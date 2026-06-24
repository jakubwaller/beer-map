#!/bin/bash
# Build + start the beermap container, then build the dataset inside it.
set -e
cd "$(dirname "$0")"
mkdir -p data
docker compose up -d --build
echo "Building dataset (OSM + finders + curation + approved submissions)..."
docker compose exec -T beermap python -m pipeline.run
echo "beermap is up on 127.0.0.1:8011"
