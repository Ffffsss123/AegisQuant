#!/bin/zsh
cd -- "${0:A:h}"
.venv/bin/python trade.py doctor
read '?Press Return to close...'
