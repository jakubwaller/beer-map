#!/bin/bash
# Build + start the beermap container, then build the dataset inside it.
set -e
cd "$(dirname "$0")"
mkdir -p data web-data   # data/ = private sqlite, web-data/ = the served export
# Stamp asset URLs with the current commit so a deploy busts any CDN-cached
# app.js/style.css (index.html is served fresh, so it always points at the new
# versioned URLs). Falls back to a timestamp outside a git checkout.
export ASSET_VERSION="$(git rev-parse --short HEAD 2>/dev/null || date +%s)"
docker compose up -d --build
echo "Building dataset (OSM + finders + curation + approved submissions)..."
docker compose exec -T beermap python -m pipeline.run
echo "beermap is up on 127.0.0.1:8011"
