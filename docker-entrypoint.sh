#!/bin/sh
set -e

echo "Waiting for PostgreSQL..."
until python -c "
import os, sys
from sqlalchemy import create_engine, text
url = os.environ.get('DATABASE_URL', '')
if not url:
    sys.exit(1)
e = create_engine(url, pool_pre_ping=True)
with e.connect() as c:
    c.execute(text('SELECT 1'))
" 2>/dev/null; do
  sleep 2
done
echo "PostgreSQL is ready."

exec "$@"
