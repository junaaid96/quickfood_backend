#!/usr/bin/env bash
# Vercel build step (see pyproject.toml). Only production deploys touch the shared database:
# preview builds of a PR must never migrate the live schema before it is merged.
set -o errexit
if [ "$VERCEL_ENV" = "production" ]; then
    python manage.py migrate --no-input
    python manage.py seed_demo   # idempotent: keeps demo logins and live demo orders fresh
else
    echo "Skipping migrations for VERCEL_ENV=${VERCEL_ENV:-local}"
fi
