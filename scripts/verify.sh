#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
check="${1:-all}"

run_contracts() {
  pnpm --dir "${repo_root}" --filter @ai-platform/contracts validate
  uv run --project "${repo_root}/apps/api" --extra dev \
    python "${repo_root}/scripts/check_contract_alignment.py"
}

run_web() {
  pnpm --dir "${repo_root}" --filter @ai-platform/web typecheck
  pnpm --dir "${repo_root}" --filter @ai-platform/web test:run
  pnpm --dir "${repo_root}" --filter @ai-platform/web build
}

run_api() {
  (
    cd "${repo_root}/apps/api"
    uv run --extra dev pytest
  )
}

run_integration() {
  uv run --project "${repo_root}/apps/api" --extra dev \
    pytest -q "${repo_root}/tests/integration"
}

case "${check}" in
  contracts) run_contracts ;;
  web) run_web ;;
  api) run_api ;;
  integration) run_integration ;;
  all)
    run_contracts
    run_web
    run_api
    run_integration
    ;;
  *)
    echo "usage: scripts/verify.sh [all|contracts|web|api|integration]" >&2
    exit 2
    ;;
esac
