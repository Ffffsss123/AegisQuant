#!/bin/zsh
cd -- "${0:A:h}"
.venv/bin/python connect.py --quotes --accounts
read '?Press Return to close...'
