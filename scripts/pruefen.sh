#!/usr/bin/env bash
# Prüfung vor dem Deploy (ersetzt seit 26.11.2026 den GitHub-Workflow ci.yml, Actions abgeschaltet):
# ruff (check + format --check), mypy, pytest gegen Postgres 16 (Docker), gitleaks, Docker-Bau.
#   scripts/pruefen.sh                 # alles
#   scripts/pruefen.sh --ohne-docker-bau
# Auch als "make pruefen". venv unter ~/.cache/brightbean-pruefen (PRUEF_PYTHON, Standard python3.12).
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO"
CACHE=${PRUEF_CACHE:-$HOME/.cache/brightbean-pruefen}
OHNE_BAU=0; [ "${1:-}" = "--ohne-docker-bau" ] && OHNE_BAU=1
[ -x "$CACHE/venv/bin/python" ] || "${PRUEF_PYTHON:-python3.12}" -m venv "$CACHE/venv"
"$CACHE/venv/bin/pip" install -q -r requirements.txt "ruff==0.15.9"
export PATH="$CACHE/venv/bin:$PATH"
# Alle Stufen laufen durch, am Ende stehen die gescheiterten (Exit 1, wenn eine scheitert).
FEHLER=()
stufe() { local name=$1; shift; echo "== $name"; "$@" || FEHLER+=("$name"); }

stufe "ruff check" ruff check .
stufe "ruff format" ruff format --check .
stufe "mypy" env SECRET_KEY=test-secret-key-not-for-production mypy apps/ config/ providers/ tests/ --ignore-missing-imports

echo "== Postgres 16 im Container"
PG=brightbean-pruefen-pg-$$
docker run -d --rm --name "$PG" -e POSTGRES_DB=brightbean_test -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres \
  -p 127.0.0.1::5432 public.ecr.aws/docker/library/postgres:16-alpine > /dev/null
trap 'docker stop "$PG" > /dev/null 2>&1 || true' EXIT
PORT=$(docker port "$PG" 5432/tcp | head -1 | cut -d: -f2)
until docker exec "$PG" pg_isready -U postgres -h 127.0.0.1 > /dev/null 2>&1; do sleep 1; done
stufe "pytest" env DJANGO_SETTINGS_MODULE=config.settings.test SECRET_KEY=test-secret-key-not-for-production \
  DATABASE_URL="postgres://postgres:postgres@127.0.0.1:$PORT/brightbean_test" DB_HOST=127.0.0.1 DB_PORT="$PORT" \
  DB_USER=postgres DB_PASSWORD=postgres pytest --cov=apps --cov-report=term-missing:skip-covered -q
stufe "gitleaks" docker run --rm -v "$REPO:/repo" zricethezav/gitleaks:v8.24.3 git /repo --redact --no-banner
if [ "$OHNE_BAU" = 0 ]; then
  stufe "Docker-Bau" bash -c 'docker build -q -t brightbean-pruefen:lokal . > /dev/null && docker rmi brightbean-pruefen:lokal > /dev/null'
fi
if [ ${#FEHLER[@]} -gt 0 ]; then
  echo "PRÜFUNG NICHT BESTANDEN: ${FEHLER[*]}"
  exit 1
fi
echo "PRÜFUNG BESTANDEN"
