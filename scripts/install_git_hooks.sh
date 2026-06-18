#!/usr/bin/env zsh
set -euo pipefail

git config core.hooksPath .githooks
chmod +x .githooks/pre-commit

echo "✅ Git hooks installed."
echo "   core.hooksPath=$(git config --get core.hooksPath)"
