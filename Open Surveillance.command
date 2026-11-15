#!/bin/zsh
set -e
cd -- "$(dirname -- "$0")"
if [[ ! -x .venv/bin/python ]]; then
  print -u2 'The project Python environment (.venv) is missing. Follow the one-time installation instructions in README.md.'
  exit 1
fi
if [[ ! -f walkout.multi.yaml ]]; then
  cp -- walkout.multi.example.yaml walkout.multi.yaml
fi
exec .venv/bin/python -m survai.main --web
