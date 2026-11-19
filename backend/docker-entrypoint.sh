#!/bin/sh
# Applies migrations and seeds reference data before starting the given process.
set -e
alembic upgrade head
python -m app.cli seed >/dev/null
exec "$@"
