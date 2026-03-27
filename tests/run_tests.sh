#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════════
# tests/run_tests.sh — Run the full test suite
#
# Usage:
#   ./tests/run_tests.sh            # all tests
#   ./tests/run_tests.sh unit       # unit tests only
#   ./tests/run_tests.sh integration # integration tests only
#   ./tests/run_tests.sh coverage   # all tests + HTML coverage report
# ════════════════════════════════════════════════════════════════════════════════

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ── Activate venv if present ─────────────────────────────────────────────────
if [ -f "venv/Scripts/activate" ]; then
    source venv/Scripts/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

# ── Install test dependencies if needed ──────────────────────────────────────
python -m pip install -q pytest pytest-asyncio pytest-cov httpx anyio 2>/dev/null || true

MODE="${1:-all}"

echo ""
echo "══════════════════════════════════════════════════════"
echo "  LiveKit Call Center — Test Suite"
echo "  Mode: $MODE"
echo "══════════════════════════════════════════════════════"
echo ""

case "$MODE" in
    unit)
        python -m pytest tests/unit/ -v --tb=short --asyncio-mode=auto
        ;;
    integration)
        python -m pytest tests/integration/ -v --tb=short --asyncio-mode=auto
        ;;
    coverage)
        python -m pytest tests/ -v --tb=short --asyncio-mode=auto \
            --cov=livekit \
            --cov-report=term-missing \
            --cov-report=html:tests/htmlcov \
            --cov-config=tests/.coveragerc
        echo ""
        echo "Coverage report: tests/htmlcov/index.html"
        ;;
    all|*)
        python -m pytest tests/ -v --tb=short --asyncio-mode=auto
        ;;
esac

echo ""
echo "══════════════════════════════════════════════════════"
echo "  Done."
echo "══════════════════════════════════════════════════════"
