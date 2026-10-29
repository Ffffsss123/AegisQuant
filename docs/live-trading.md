# Live stock trading

[简体中文](live-trading.zh-CN.md) · [Project README](../README.md)

The English console now includes **Live Trading** for US and Hong Kong **cash common stocks**. Code support is not proof of a successful real-money fill. This release was tested with isolated fake brokers; it has not submitted a REAL order. Live accounts remain unbound and disabled in the developer's environment.

## Scope and strategy

- Separate REAL account bindings, native-currency budgets and SQLite ledgers under `data/trading/live/`. Simulation ledgers and permissions are not reused or reset.
- Limit orders only, regular continuous sessions, whole US shares and verified HK board lots/ticks. No shorts, margin funding, deposits or currency conversion.
- At most **two strategy-owned stock symbols across US and HK**, counting pending/unknown opening orders. Existing manual holdings are a read-only baseline and cannot be sold by this system.
- Combined configured live budgets are capped at USD 890 using the verified accounting FX reference. USD 390 is reserved; US purchases must leave it in existing settled USD cash. HK purchases require existing HKD cash. These limits do not allocate or transfer money.
- A user-selected joint loss threshold of at most USD 100 is latched. Market-specific native-currency limits also apply. Profits in one market do not offset a loss in the other for this check.
- Optional automatic entries use the original **daily long-trend engine**, with completed prior-session signals and news filtering. This is **not** the simulation's 1–5 day ATR swing engine. The ATR swing, option and futures execution paths have not been promoted to live use. The daily engine checks trend exits; it does not provide an exchange-hosted stop or ATR stop-loss order.
- Automatic entries default to **false**. With a running worker, explicit limit-order tickets are supported independently; new buys still require an eligible completed-scan long signal, common-stock identity, fresh quotes, news and all portfolio risk checks. The worker manages existing strategy-owned positions and trend exits. The original daily engine makes one decision per market session; US automatic decisions use its 09:35–09:40 New York window. This is not a continuously evaluated intraday strategy.

The screenshots are actual captures of the English application using a read-only local status service. `DISABLED` and `WORKER OFFLINE` are intentional capture states, not successful live execution evidence. Their sidebar USD 1,500 belongs to simulation and is not a live allocation.

## 1. Prepare a local configuration

Install the dependencies and verify your chosen securities adapter as described in the [adapter guide](broker-adapters.md). The included adapter requires its matching local gateway and a supported securities entity; it is not an arbitrary broker REST endpoint. Broker API permissions and market data entitlements must cover your accounts and markets.

```sh
.venv/bin/python -m trading.live_trading template
```

Alternatively, select **Live Trading → Create config draft**. Open `data/trading/live/config.json` locally and replace empty account IDs and `null` budgets/loss limits. US amounts are **USD**, HK amounts are **HKD**. Do not commit this file.

Important fields:

| Field | Meaning |
| --- | --- |
| `markets.US.account`, `markets.HK.account` | Your own numeric REAL account IDs, verified against market permissions |
| `budget_native` | Capital already held in that market's native cash currency, reserved for this strategy |
| `loss_native` | Market-specific cumulative loss threshold in the same native currency |
| `fee_reserve_native` | Per-order fee reserve; minimum USD 3 / HKD 20. HK's conservative execution-rounding upper bound must fit within the chosen reserve |
| `joint_loss_limit_usd` | Positive combined threshold, at most USD 100 |
| `automatic_entries` | `false` for explicit tickets plus owned-position management; `true` additionally permits daily-trend automatic entries after arming and starting |
| `firm`, `port` | The supported securities entity and loopback gateway port; verify these with your account provider |

The template contains no usable account or budget and cannot arm trading. Budgets and account identities become immutable after preparation. Do not edit a bound configuration or delete/reset ledgers to bypass a block. If preparation is interrupted, rerunning preparation can recover only matching, unused, disabled bindings; any activity requires inspection.

## 2. Check simulation evidence and protection tests

Each configured market must have its own natural simulation buy/sell round trip, a flat owned position and clean order/fee reconciliation. US transport probes, reset sells and manual liquidation do not qualify. HK evidence is checked against the existing submission/settlement journals, natural strategy exit events and a fresh flat risk snapshot. Source publication or a screenshot is not acceptance evidence.

```sh
.venv/bin/python -m trading.live_trading validate
.venv/bin/python -m trading.live_trading refresh-fx
.venv/bin/python -m trading.live_trading prepare
.venv/bin/python -m trading.live_trading status
```

`validate` runs the protection tests and records a fingerprint only if the tests pass and the source remains unchanged. New live submissions recheck this fingerprint and the running worker's implementation. After updating code, pause buys, inspect and reconcile any pending orders, validate the new source and perform a controlled maintenance-worker restart; never reset the ledgers. `refresh-fx` reads the ECB's public reference; this is accounting data, not an FX transaction. Refresh HK identity/classification/calendar data through the existing HK data tools as well. Missing, stale or unverified data blocks the relevant operation.

`prepare` queries the explicitly chosen REAL accounts, checks existing orders and settled native cash, and creates **paused, disabled** live ledgers. It does not place orders, start a worker or enable products. An existing manual position remains outside strategy ownership.

## 3. Enable explicitly, then start a worker

In the local gateway, unlock trading manually when required by the provider. This application does not collect, store or send your trading password. The bundled SDK's real-order interface requires an unlocked trading session; see the [official order API documentation](https://openapi.moomoo.com/moomoo-api-doc/en/trade/place-order.html).

In **Live Trading**, type `ENABLE LIVE STOCKS` and select **Enable live stocks**. Then select **Start live execution worker** separately. CLI equivalents:

```sh
.venv/bin/python -m trading.live_trading enable --confirmation 'ENABLE LIVE STOCKS'
.venv/bin/python -m trading.live_trading run
```

Enabling requires the current protection fingerprint, simulation evidence and fresh live reconciliation/valuation. It does not start a worker. The first live submission must wait until a later local trading date and must fall within an official regular session. Reopening the desktop application does not automatically restart the live worker. Do not start another worker; a process lock rejects duplicates.

If you enable `automatic_entries`, the running worker may create REAL orders when the strategy and all guards allow them. Keep it false when you want to approve entries through individual tickets.

## 4. Submit and inspect a limit-order ticket

![Live limit-order ticket, disabled](images/aegisquant-live-ticket-en.png)

With an armed portfolio and a running worker, enter the market, stock code, BUY/SELL, whole shares/board-lot quantity and native-currency limit price. Type `PLACE LIVE ORDER` and submit. A SELL can close only a position owned by the live strategy; it cannot sell a manual baseline holding or create a short.

A fresh, timestamped bid and ask no older than 30 seconds and a spread no wider than 0.2% are required for REAL stock submissions. An order retains its fixed limit; a quote moving beyond the limit causes a block, not price chasing. Outside executable sessions, valuation can use a verified last trade from the latest completed session; this never relaxes the executable-order quote rules. Broker fees must eventually be verified; unresolved fees block further orders.

Every ticket has an immutable request ID, and every order has a durable intent and broker tag. A lost acknowledgment is recorded as UNKNOWN. Query and reconcile it; do not submit a replacement because a request timed out. The UI acknowledgment that a local operation was queued is not a broker acknowledgment or fill.

## 5. Pause, stop and recover

```sh
.venv/bin/python -m trading.live_trading pause
.venv/bin/python -m trading.live_trading stop
.venv/bin/python -m trading.live_trading status
```

- **Pause live buys** immediately saves a local pause latch without waiting for the gateway. A running worker continues owned-position exits, cancellation and reconciliation. A later explicit enable can resume a clean manual pause; it cannot silently erase unresolved alerts or a loss/stop latch.
- **Latch live stop** blocks all new order submissions, including automatic exits. The running worker still attempts cancellation and reconciliation. Remaining positions require manual attention; this does not promise liquidation.
- Outstanding strategy orders become due for cancellation after **60 seconds**. The worker checks every 5 seconds, prioritizing reconciliation/cancellation before quote-dependent decisions. Network outages or provider limits can delay acknowledgment; a local cancellation request does not establish a canceled order.
- **Reconcile once** (CLI: `.venv/bin/python -m trading.live_trading maintain`) queries and reconciles known orders and services due cancellations. It does not run strategy decisions or place new orders.
- A market-specific reconciliation or valuation failure blocks new buys but does not prevent another healthy market from servicing its own due cancellations and strictly validated owned-position exits. An unhealthy market cannot bypass its own account, quote, fee or ownership checks.
- The process-liveness heartbeat runs separately from slow SDK calls. Fresh valuations, quotes and account reconciliation remain independent admission requirements; a live heartbeat alone never authorizes an order.
- Stale HK accounting FX blocks new entries while native-currency maintenance continues. A loss latch, unknown order, configuration mismatch, failed reconciliation or outstanding alert must be investigated; there is no generic “clear all errors” bypass.
- Trend exits depend on the running computer, network and gateway. Closing the console window does not stop an already running worker. A stopped computer cannot manage orders or positions; inspect the broker account directly when service is unavailable.

Inspect `data/trading/live/{us,hk}.sqlite`, `report.json`, and the private `data/desktop/live_*.log` files. Raw broker responses and state transitions are retained locally. Do not publish these records or credentials. The desktop status projection omits account IDs and raw broker replies.

The included adapter's fund fields and fee calls follow the official [account funds](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-funds.html) and [order fee](https://openapi.moomoo.com/moomoo-api-doc/en/trade/order-fee-query.html) interfaces. Other platforms need independently tested adapters before using the same workflow.
