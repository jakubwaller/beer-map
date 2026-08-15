# Regenerating `web/og-image.jpg`

The social preview card (LinkedIn, Slack, WhatsApp, X) is a static 1200×630 JPEG
built from a real screenshot of the map, composed with `docs/og-card.html`.
Refresh it when the look of the map changes or the numbers on it get stale.

1. Serve the app locally with a current dataset:
   `python -m pipeline.run && uvicorn api.app:app --port 8021`
2. Open it in a browser sized to **1200×630** and hide the chrome, so the map
   plate is nothing but map (the region-jumper refit reads the topbar height, so
   hiding it first makes the fit use the full frame):
   ```js
   for (const id of ["zoom-ctrl", "attribution", "topbar"])
     document.getElementById(id).style.display = "none";
   const s = document.getElementById("city-select");
   s.value = "de"; s.dispatchEvent(new Event("change", { bubbles: true }));
   ```
   Wait for the tiles, screenshot at device pixel ratio 2 → a 2400×1260 plate.
3. Drop the plate next to `docs/og-card.html` as `_og-plate.png`, serve that
   directory, and screenshot the card at 1200×630 (again at DPR 2). Adjust the
   `.plate` transform if the countries sit under the type, and update the numbers
   in `.stats` — total venues from the DB on the deploy host (the export is
   branded-only, so the big number lives in SQLite), branded from the export:
   `sqlite3 data/beer-map.sqlite "SELECT COUNT(*) FROM venues"`
   `jq '.features | length' web/data/venues.json`
4. Downscale and convert:
   `sips -s format jpeg -s formatOptions 84 -z 630 1200 card.png --out web/og-image.jpg`

Keep the OpenStreetMap attribution on the card — the plate is OSM tile imagery.

The `og:image` URL in `web/index.html` is absolute (`https://zapfkompass.de/…`)
and carries explicit `og:image:width`/`height`; LinkedIn's post inspector shows
a bare box without those.
