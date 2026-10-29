#!/bin/zsh
cd -- "${0:A:h}"
.venv/bin/python trade.py doctor
read '?按回车关闭窗口…'
