#!/usr/bin/env bash
# tour.sh — run every regression scenario back-to-back.
#
# Run from inside examples/regression-tour after recording the baseline:
#
#     agentprdiff init
#     agentprdiff record suite.py
#     ./tour.sh
#
# Each scenario should print non-zero exit code except the happy path.

set -u
cd "$(dirname "$0")"

# Use `agentprdiff` if it's on PATH, otherwise fall back to `python3 -m`.
if command -v agentprdiff >/dev/null 2>&1; then
    APD="agentprdiff"
else
    APD="python3 -m agentprdiff.cli"
fi

PASS=0
FAIL=0

run() {
    local label="$1"
    local expect="$2"
    shift 2
    echo
    echo "──────────────────────────────────────────────────────────────"
    echo "  $label"
    echo "  expect: $expect"
    echo "──────────────────────────────────────────────────────────────"
    "$@"
    local code=$?
    echo
    echo "  exit code: $code"
    if [[ "$expect" == "exit 0" && "$code" -eq 0 ]] || \
       [[ "$expect" == "exit non-zero" && "$code" -ne 0 ]]; then
        PASS=$((PASS + 1))
        echo "  ✓ scenario behaved as expected"
    else
        FAIL=$((FAIL + 1))
        echo "  ✗ scenario did NOT behave as expected"
    fi
}

run "1. Happy path" "exit 0" \
    $APD check suite.py

run "2. Output text drifted" "exit non-zero" \
    env MODE=output_changed agentprdiff check suite.py

run "3. Extra tool call" "exit non-zero" \
    env MODE=tool_added agentprdiff check suite.py

run "4. Missing tool call" "exit non-zero" \
    env MODE=tool_removed agentprdiff check suite.py

run "5. Tool order swapped" "exit non-zero" \
    env MODE=tool_reordered agentprdiff check suite.py

run "6. Latency regression" "exit non-zero" \
    env MODE=latency_regressed agentprdiff check suite.py

run "7. Cost regression" "exit non-zero" \
    env MODE=cost_regressed agentprdiff check suite.py

echo
echo "══════════════════════════════════════════════════════════════"
echo "  Tour summary: $PASS scenarios behaved as expected, $FAIL did not"
echo "══════════════════════════════════════════════════════════════"
exit $FAIL
