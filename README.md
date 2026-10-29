# Quant Trading

Quantitative trading research, simulation, and macOS desktop tools for US/HK stocks, US options, and Binance.

## Project layout

- `trading/`: market data, screening, simulation, broker transport, reconciliation, and risk controls.
- `binance_trader/`: Binance desktop backend and browser interface; see its README for usage.
- `desktop/`: native macOS console source and build script.
- `tests/` and `binance_trader/tests/`: regression tests.
- `examples/`: synthetic demonstration data.
- Top-level Python scripts: connection diagnostics, backtesting, validation, and reports.

## Python setup

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Broker access requires the official software, an appropriate account, and separately configured permissions. Review `trading/config.example.json` before configuring a local environment. Run diagnostics and simulation before enabling any execution.

## Public source snapshot

This repository contains a sanitized source snapshot. Runtime databases, account balances, order history, credentials, local reports, dependencies, and built application bundles are excluded. Local simulated account bindings in deployment code and its regression tests were replaced with the example IDs `000001` (US) and `000002` (HK). Configure and validate your own bindings before attempting a deployment.

The source snapshot does not establish live-trading readiness or successful broker execution acceptance. Keep real trading disabled during setup and testing. No license grant has been added.
