#!/bin/sh
# Entrypoint of the application container.
#
#   gunicorn        (default) apply database migrations, then serve the app
#   anything else   run it as is, e.g. "python3 manage.py createsuperuser"
set -e

if [ "$1" = "gunicorn" ] && [ "$#" -eq 1 ]; then
    if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
        python3 manage.py migrate --noinput
    fi
    # Cached export pages may predate a database restore or an upgrade.
    rm -rf "${CACHE_DIR:?}"/* 2>/dev/null || true

    exec gunicorn aprs2netportal.wsgi:application \
        --bind 0.0.0.0:8000 \
        --workers "${GUNICORN_WORKERS:-2}" \
        --threads "${GUNICORN_THREADS:-4}" \
        --access-logfile - \
        --no-control-socket \
        --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}"
fi

exec "$@"
