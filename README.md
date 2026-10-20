# SIGNIX

SIGNIX is a Flask web application for preliminary rooftop-solar planning in India. A user locates a property, draws the authoritative roof boundary and obstructions, reviews a 3D planning model, and receives indicative system sizing, generation, subsidy, and financial results.

The application is a planning tool, not a structural survey, certified energy model, vendor quotation, or subsidy approval.

## Current capabilities

- Leaflet property search, satellite map, roof-boundary drawing, and manual obstructions.
- GeoJSON persistence using `[longitude, latitude]` and explicit Leaflet conversion.
- Three.js roof reconstruction with simulated flat prototype geometry and normalized provider-surface support.
- Physical-size panel packing with setbacks, obstruction avoidance, classification exclusions, and explicit layout acceptance.
- Separate planning-preview and conservative shade policies.
- NASA POWER irradiance, PVGIS modeled-yield fallback, deterministic offline fallback, and optional Google Solar data ingestion.
- Per-surface accepted-layout generation, calculation provenance, and stale-state detection.
- Indicative subsidy, tariff, savings, payback, financing, and PDF/installer reports.
- Account-scoped saved estimates, scenarios, comparisons, settings, and English/Hindi localization.

## Technology

- Python 3.11, Flask, Jinja2, SQLAlchemy, Flask-Login, Flask-WTF
- SQLite locally; PostgreSQL is supported through `DATABASE_URL`
- Vanilla JavaScript, Leaflet, Three.js, and vendored Chart.js
- WeasyPrint with ReportLab fallback for documents
- pytest and Node's built-in test runner
- Docker and Docker Compose

## Setup

### Local Python

```powershell
git clone https://github.com/saksham6541/SIGNIX.git
cd SIGNIX
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Replace SECRET_KEY and configure only the optional providers you intend to use.
python run.py
```

Open `http://127.0.0.1:5000`.

On macOS/Linux, activate with `source .venv/bin/activate` and copy the environment file with `cp .env.example .env`.

### Docker

```bash
cp .env.example .env
# Set a production-quality SECRET_KEY in .env.
docker compose up --build web
```

Open `http://127.0.0.1:8000`. The default Compose service stores SQLite data in the named `solar_data` volume. The optional PostgreSQL profile is:

```bash
docker compose --profile postgres up --build web-postgres postgres
```

No Google Solar or Gemini key is required for the default prototype workflow. Never commit `.env`.

## Validation

GitHub Actions runs the Python suite, Python compilation, all seven application JavaScript syntax checks, the three Node regression suites, Markdown link validation, and patch-whitespace validation on pushes and pull requests targeting `main`.

From the repository root with the virtual environment active, the primary checks are:

```powershell
python -m pytest tests -q
python -m compileall -q app tests
Get-ChildItem app\static\js -Filter *.js | ForEach-Object { node --check $_.FullName }
node --test tests\roof3d_geometry.test.js
node --test tests\assistant_voice.test.js tests\voice_mode.test.js
python scripts\validate_markdown_links.py
git diff --check
```

Tests mock external providers where required. CI needs no `.env`, API keys, user database, or live provider calls. See the [development guide](docs/development.md#continuous-integration) for exact CI parity commands and failure guidance.

## Repository map

```text
app/
  templates/             Jinja frontend templates
  static/css/            Shared visual system and responsive styles
  static/js/             Map, report, 3D, AI, assistant, and chart interactions
  services/              Estimation, location, rooftop, shade, and report services
  *.py                   Flask blueprints, models, configuration, calculations
tests/                    Python, Node, fixtures, and golden calculation cases
docs/                     Current architecture and team guides
scripts/                  Maintainer and verification utilities
translations/             Babel catalogs
run.py                    Flask/Gunicorn application entry point
Dockerfile                Container image
docker-compose.yml        SQLite and optional PostgreSQL services
```

The root `app/` package is the sole application workspace. The obsolete
`solar_app/` checkout was removed after its complete Git history and working
state were archived; see [Architecture](docs/architecture.md#historical-solar_app-archive).

## Team documentation

- [Architecture and data flows](docs/architecture.md)
- [Frontend guide](docs/frontend-guide.md)
- [Backend guide](docs/backend-guide.md)
- [Verified API contracts](docs/api-contracts.md)
- [Development and collaboration](docs/development.md)
- [Known limitations](docs/known-limitations.md)
- [Product principles](PRODUCT.md)
- [Design system](DESIGN.md)

`PERFORMANCE_BASELINE.md` and `PRD_TECH_STACK_MIGRATION.md` are retained as historical records and are not current setup or architecture instructions.

## Deployment

The verified container production command is:

```bash
gunicorn --bind 0.0.0.0:8000 --workers 2 run:app
```

`docker-entrypoint.sh` initializes the database and seeds reference tariff/subsidy rows before starting the command. Configure `SECRET_KEY` and `DATABASE_URL` in the deployment environment. Render can run this container, but no `render.yaml` is present; service and database configuration must be maintained in the Render dashboard.

## Important limitations

The user-drawn boundary is authoritative, but prototype roof geometry, shade coverage, orientation modeling, provider availability, financial assumptions, tariffs, and subsidies can be incomplete or approximate. Read [Known limitations](docs/known-limitations.md) before changing accuracy claims or presenting results externally.

## License

MIT. See [LICENSE](LICENSE).
