#!/usr/bin/env bash
# Baut dist/website-check.zip zum Hochladen in claude.ai (claude.ai → Einstellungen → Skills).
set -euo pipefail
cd "$(dirname "$0")/.."
python3 tools/check_skill.py
rm -rf dist && mkdir -p dist
find skills -name "__pycache__" -prune -exec rm -rf {} +
(cd skills && zip -qr ../dist/website-check.zip website-check -x "*.pyc")
unzip -l dist/website-check.zip
