#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
"$ROOT/app/environment/scripts/launch.sh"
