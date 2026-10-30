#!/bin/zsh
cd -- "${0:A:h}"
.venv/bin/python backtest.py examples/SYNTHETIC_DEMO.csv
read '?Press Return to close...'
