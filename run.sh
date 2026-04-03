#!/usr/bin/env bash
set -e
source .venv/bin/activate
python -X utf8 run.py "$@"
