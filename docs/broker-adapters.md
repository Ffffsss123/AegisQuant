# Securities API adapters / 证券 API 适配

## Current implementation / 当前实现

The included adapter is `trading/moomoo.py`, using the SDK pinned in `requirements.txt` and a local gateway (default `127.0.0.1:11111`). It is a specific integration, not a generic REST endpoint. Its trading context currently selects `SecurityFirm.FUTUAU`; other account entities require verification and adaptation. Install and sign in to the matching official API gateway using the SDK provider's documentation. A regular trading client session alone is insufficient. This integration uses the gateway session rather than an arbitrary API key/secret pair. Keep the gateway bound to loopback.

当前适配器使用 `requirements.txt` 固定的 SDK，通过本机网关连接。交易上下文选择 `SecurityFirm.FUTUAU`，其他账户实体需核验并适配。按照 SDK 提供方的官方文档安装并登录匹配的 API 网关；普通证券客户端登录不能替代网关会话。它不能接收任意平台的 API Key / Secret。

```sh
.venv/bin/python connect.py --port 11111 --quotes --accounts
.venv/bin/python trade.py --port 11111 doctor
```

These are read-only diagnostics for this adapter. Check market-data permissions, trading-session health and your own SIMULATE accounts. Never commit credentials or account snapshots. Broker-reported nominal cash is not an automatic strategy budget allocation.

以上为此适配器的只读诊断。检查行情权限、交易连接及自己的 SIMULATE 账户，凭据和账户快照不可提交。券商名义现金不自动等于策略获配预算。

## Adding another platform / 新平台接入

This is an integration checklist, not an implemented automatic plugin loader. Implement the equivalent calls consumed by `trading/` and wire them into the relevant execution modules. Existing execution paths are coupled to the included SDK; adding a URL in configuration is insufficient.

这是接入检查清单，不是已经实现的自动插件加载功能。现有执行模块仍调用附带 SDK，需要实现等效接口并接入相关执行路径，不能只修改 URL。

| Capability / 能力 | Required validation / 必须核验 |
| --- | --- |
| Authentication / 身份验证 | Official credentials, environment separation, permissions, expiry and reconnect; never log secrets / 官方凭据、环境隔离、权限、过期与重连，不记录密钥 |
| Market data / 行情 | Instrument identity, completed history, bid/ask sizes and timestamps, freshness and subscriptions / 品种身份、完整历史、买卖盘量价、时效及订阅 |
| Accounts / 账户 | Market and SIMULATE binding, currencies, cash, positions and full reconciliation / 市场及模拟账户绑定、币种、现金、持仓及完整对账 |
| Orders / 订单 | Limit-price validation, durable intent, IDs, partial fills, cancel deadlines and unknown-response recovery / 限价、持久下单意图、编号、部分成交、撤单期限及未知回包恢复 |
| Risk / 风控 | Independent budgets, pending-order slots, joint loss limits, manual pause and exit priority / 独立预算、挂单占槽、联合亏损、手动暂停及退出优先 |
| Markets / 市场 | Official calendars, lots, tick sizes, multipliers and fees / 官方日历、整手、价位、乘数及费用 |

On a timeout, query the existing order before considering another submission. An unknown result is not a rejection or permission to resend. Preserve ledgers and raw evidence. Verify all of this in simulation before considering further scope. Futures remain research-only; implementing a stock adapter does not enable futures.

超时先查原订单，未知结果不等于拒单，也不授权重发。保留账本和原始证据，先在模拟环境逐项验证。期货仍仅供研究，股票适配完成不代表期货已接通。

US/HK cash-stock live controls are separately implemented in `trading/live_trading.py` and `trading/live_broker.py`, disabled until explicit enable. See the [live guide](live-trading.md). Simulation permissions and ledgers do not authorize REAL orders.
