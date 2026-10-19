# SIGNIX Architecture

This document describes the active root application as of Phase 9. The root `app/` package is authoritative.

## Runtime structure

```text
Browser
  ├─ Jinja pages (`app/templates`)
  ├─ Vanilla JavaScript (`app/static/js`)
  └─ Leaflet / Three.js / Chart.js
         │ authenticated JSON + CSRF
Flask blueprints (`app/*.py`)
         │ validated domain calls
Services (`app/services`)
         ├─ rooftop design, ingestion, reconstruction and shade
         ├─ estimate persistence and report shaping
         └─ location lookup and caching
         │
Domain and persistence
  ├─ `app/solar_logic.py`
  ├─ `app/geo.py`
  ├─ `app/models.py`
  └─ SQLite or PostgreSQL through SQLAlchemy
```

`create_app()` in `app/__init__.py` initializes Flask, SQLAlchemy, authentication, CSRF, Babel, error handlers, and all blueprints. `run.py` exposes `app` for Gunicorn and runs the development server when invoked directly.

Flask's conventional `app/templates/` and `app/static/` paths are intentionally retained. They are the frontend workspace; moving them would add configuration risk without improving ownership.

## Main request flows

### Estimate creation

1. `main.js` submits the Leaflet roof and obstruction rings to `POST /api/estimate`.
2. `app/estimate.py` delegates validation and persistence to `estimation_service`.
3. `app/geo.py` validates the Leaflet `[latitude, longitude]` rings.
4. `solar_logic.run_full_estimation()` performs sizing, generation, subsidy, and financial calculations.
5. `UserLocation` persists GeoJSON in `[longitude, latitude]` order plus calculation results and provenance.
6. The response returns the saved location ID and report-safe result data.

### Rooftop design and reconstruction

1. A `RooftopDesign` revision stores the authoritative user boundary and accepted/manual inputs separately from advisory provider sources.
2. `rooftop_ingestion_service` normalizes optional provider data without silently accepting it.
3. `rooftop_reconstruction_service` clips surfaces and structures to the authoritative boundary and produces local coordinates.
4. Local 3D coordinates use east as `+X`, up as `+Y`, and north as `-Z`.
5. `rooftop_shade_service` creates non-overlapping classification metrics.
6. `roof3d.js` renders the model and proposes panels; it does not authoritatively save capacity.

### Layout acceptance and recalculation

1. The browser sends panel corners, surface IDs, layout mode, design revision, and geometry key.
2. `rooftop_layout_service` revalidates module dimensions, boundary containment, setbacks, obstructions, shade policy, collisions, ownership, and revision compatibility.
3. Server-derived panel count and DC capacity are passed through the established estimation engine.
4. Design revision, layout acceptance, generation, and financial results commit in one transaction.
5. Geometry, provider-flux, shade, or calculation-assumption changes make dependent results stale.

### Generation paths

- NASA POWER: location-wide monthly horizontal irradiance in kWh/m²/day, followed by SIGNIX PR, approximate temperature, and azimuth adjustments.
- PVGIS: monthly modeled PV production normalized to kWh/kWp/month and scaled once; provider-modeled losses are not applied again.
- Google annual flux: used directly only when normalized as annual kWh/kW with complete accepted-panel coverage.
- Offline fallback: deterministic planning profile, clearly marked low confidence.

Tilt metadata is retained but not converted into plane-of-array irradiance without defensible inputs.

## Persistence and provenance

`UserLocation` stores the saved estimate and flexible `extras`. `RooftopDesign` stores immutable revisions and separates authoritative fields from `RooftopDesignSource` advisory/provider records. Calculation assumptions and a fingerprint are saved so older results can be marked current, stale, or legacy-unversioned.

The application initializes schema compatibility in `app/database.py`; it does not use a general migration framework. Schema changes require careful SQLite and PostgreSQL testing.

## Shared frontend/backend contracts

- GeoJSON: `[longitude, latitude]`.
- Leaflet arrays: `[latitude, longitude]`.
- Local roof plane: metres in east/+X and north/-Z.
- Unsafe API methods: authenticated session plus CSRF token.
- User-drawn boundary: authoritative.
- Provider and AI results: advisory until explicitly accepted.
- Preview interactions: must not mutate saved estimates.
- Server: derives accepted panel count, capacity, generation, and financial outputs.

Changes to these contracts require coordinated frontend, backend, and regression-test updates.

## Embedded `solar_app/` checkout

`solar_app/` is an embedded Git checkout recorded by the outer repository as gitlink commit `a57048701b3bf20e0913179cd0b806d22500d54d`. It has its own `.git`, history, ignored databases, caches, and older application copy. The outer runtime, tests, Docker build, and imports do not depend on it, but deleting it would remove an independently recoverable checkout and is not a safe cleanup action without first deciding how to preserve that history.

The gitlink was introduced in the outer repository's first commit, but no root `.gitmodules` entry exists. Its nested checkout currently uses the same GitHub repository remote and has independent commits. That evidence is compatible with either an incomplete submodule setup or a historical embedded checkout; it is not sufficient to safely declare the entry accidental.

Treat it as historical reference only. Run commands from the outer repository root and modify the root `app/` implementation for current work. CI explicitly leaves submodule initialization disabled, targets `tests/` rather than bare pytest discovery, and validates only outer-repository Markdown. A separate, explicitly authorized task must decide whether to formalize the submodule, archive its history and remove the gitlink, or preserve the current arrangement.

## Deployment

The Docker image installs `requirements.txt`, copies the active outer checkout while `.dockerignore` excludes `solar_app/`, and runs `docker-entrypoint.sh`. The entrypoint initializes tables and seed rows, then executes Gunicorn. Compose offers a persistent SQLite volume and an optional PostgreSQL profile. The repository CI validates source, documentation, and tests but does not build or deploy the image. No `render.yaml` currently exists.
