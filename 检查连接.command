#!/bin/zsh
cd -- "${0:A:h}"
.venv/bin/python connect.py --quotes --accounts
read '?按回车关闭窗口…'
