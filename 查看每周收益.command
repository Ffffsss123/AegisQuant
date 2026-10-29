#!/bin/zsh
cd -- "${0:A:h}"
.venv/bin/python weekly_returns.py
read '?按回车关闭窗口…'
