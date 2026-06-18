#!/usr/bin/env zsh
set -euo pipefail

GITLEAKS_BIN="${GITLEAKS_BIN:-}"

if [[ -z "$GITLEAKS_BIN" ]]; then
  GITLEAKS_BIN="$(command -v gitleaks 2>/dev/null || true)"
fi

# Fallback for setups where gitleaks is only available in interactive zsh PATH.
if [[ -z "$GITLEAKS_BIN" ]]; then
  GITLEAKS_BIN="$(zsh -i -c 'command -v gitleaks' 2>/dev/null || true)"
fi

if [[ -z "$GITLEAKS_BIN" && -x "/opt/homebrew/bin/gitleaks" ]]; then
  GITLEAKS_BIN="/opt/homebrew/bin/gitleaks"
fi

if [[ -z "$GITLEAKS_BIN" ]]; then
  echo "gitleaks is not installed."
  echo "Install:"
  echo "  brew install gitleaks"
  echo "or"
  echo "  go install github.com/gitleaks/gitleaks/v8@latest"
  exit 1
fi

echo "Running gitleaks secret scan (verbose mode)..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

FILES_TO_SCAN="$(git ls-files -co --exclude-standard)"
if [[ -z "${FILES_TO_SCAN}" ]]; then
  echo "No tracked/unignored files to scan."
  exit 0
fi

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

while IFS= read -r file; do
  [[ -z "${file}" ]] && continue
  [[ -f "${file}" ]] || continue
  mkdir -p "${TMP_DIR}/$(dirname "${file}")"
  cp "${file}" "${TMP_DIR}/${file}"
done <<< "${FILES_TO_SCAN}"

if "$GITLEAKS_BIN" detect --source "${TMP_DIR}" --no-git --redact -v; then
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "✅ No secrets detected."
else
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "❌ Secrets detected! Review the output above and redact them."
  exit 1
fi
