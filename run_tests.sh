#!/bin/bash
# Run tests inside the backend Docker container
# Usage: ./run_tests.sh [pytest args]
#
# Prerequisites:
#   1. docker compose up -d db redis floci floci-init backend
#   2. Database seeded with 03_seed.sql (runs automatically on first start)

set -e

# If in WSL2 or Git Bash, forward to Docker Desktop on Windows
if grep -qEi "(microsoft|wsl)" /proc/version 2>/dev/null; then
  DOCKER="docker"
else
  DOCKER="docker"
fi

echo "=== Starting test run on backend container ==="
echo "Prerequisites:"
echo "  - DB seeded: docker compose up -d db (init script runs on first start)"
echo "  - Backend healthy: docker compose ps backend (should be healthy)"
echo ""

$DOCKER compose exec backend pytest /app/tests/test_asignacion_cu22_cu26.py -v --tb=short "$@"
