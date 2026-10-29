#!/bin/zsh
cd -- "${0:A:h}"
.venv/bin/python backtest.py examples/SYNTHETIC_DEMO.csv
read '?按回车关闭窗口…'
