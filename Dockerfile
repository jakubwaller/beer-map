FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pipeline ./pipeline
COPY api ./api
COPY web ./web
COPY curation.yaml ./curation.yaml

# Cache-busting: stamp a version into the asset URLs (style.css?v=, app.js?v=,
# and app.js's datasource.js import) so a fresh index.html never pairs with a
# CDN-cached stale app.js/style.css after a deploy. Defaults to "dev" for local
# builds; docker-run.sh passes the git short SHA.
ARG ASSET_VERSION=dev
RUN sed -i "s/__ASSET_VERSION__/${ASSET_VERSION}/g" web/index.html web/app.js

EXPOSE 8011
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8011"]
