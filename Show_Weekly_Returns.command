#!/bin/zsh
cd -- "${0:A:h}"
.venv/bin/python weekly_returns.py
read '?Press Return to close...'
