# AegisQuant

**简体中文** | [English](README.en.md)

面向 **美股、港股、美国期权及期货研究** 的量化研究与模拟控制台。软件界面使用英文，串联行情、新闻、选股、风控、模拟订单执行和账户对账。

## 平台与接入范围

AegisQuant 采用证券 API 适配的接入方式，不以某一家交易平台命名。拥有合适 API 的证券平台可以作为接入目标；**当前版本附带一个 SDK / 本地网关适配器，并非填入任意平台的 API Key 就能直接使用。** 新平台需要实现并验证行情、历史数据、账户、持仓、订单、撤单及成交回报接口，还要匹配其权限、市场规则和交易日历。具体接口与现有适配器见 [证券 API 接入说明](docs/broker-adapters.md)。

| 市场 | 当前范围 |
| --- | --- |
| 美股股票 | 全市场筛选、做多波段模拟、风控与券商对账 |
| 港股股票 | 交易日历、整手及价位检查、模拟执行与对账 |
| 美国期权 | 合约研究及 SIMULATE 订单传输验收 |
| 期货 | 研究及 FUTURE 记账分类预留；没有期货执行后台、已验证通道或执行预算 |

公开源码不代表实盘已就绪、券商成交验收已完成或盈利已验证。当前控制台不提供实盘启用。

## 英文证券控制台

![AegisQuant securities console](docs/images/aegisquant-securities-console-en.png)

本机实际 Mac 证券控制台的英文界面，读取现有本地模拟账本和历史扫描记录。截图采用只读状态服务，交易工作进程和证券网关未运行；未恢复策略、未提交订单。画面中的扫描日期是历史信号日期，1,500 USD 是配置的模拟预算，不是真实账户权益，也不代表当前券商在线或验收通过。原始诊断和新闻原文保留其来源语言。

## 基本运行逻辑

```text
行情 / 历史 K 线 / 新闻
          ↓
数据完整性、时效、品种与交易时段检查
          ↓
策略信号 + 新闻方向 / 风险过滤
          ↓
预算、仓位、亏损阈值与暂停状态检查
          ↓
SIMULATE 模拟订单执行
          ↓
查询订单 → 核验成交 → 更新持仓与账本
          ↓
止损 / 止盈 / 趋势反转 / 到期退出 → 对账
```

1. **启动与恢复**：读取本机配置和持久化账本，恢复已知订单及持仓。连接账户、保存设置或恢复凭据，不等于启动新开仓。
2. **收集与检查**：读取行情、已收盘 K 线和新闻；数据过期、扫描不完整、品种身份未核实或账户状态异常时，阻止新开仓。
3. **形成决策**：策略先产生候选方向，再检查新闻、资金、交易时段、仓位和亏损限制。信号成立仍可能因风控被拒绝。
4. **执行与核对**：记录下单意图和回包，查询真实订单状态，以核验结果更新账本。超时或结果未知时先查询、保留证据，避免盲目重发。
5. **持续管理**：后台管理已有仓位、保护单、撤单及对账。暂停新开仓不等于已经平仓；关闭网页也不等于后台已经停止。

各通道使用各自的配置、账户状态与执行保护。下述规则描述当前公开源码，实际参数以对应模块和本机配置为准。

## 交易逻辑

### 美股股票

- 采用做多的短期波段框架：基于已完成交易日的数据检查基础趋势、流动性及此前 5 个交易日高点突破。
- 可选高波动趋势筛选，结合 ATR 占比、趋势效率等指标排序；完整扫描和合格历史数据是准入前提。
- 仓位按可用预算、计划止损距离、手续费及滑点预留共同计算，不能仅按余额满仓买入。
- 波段模型的初始止损距离为 `1.5 × ATR14`，目标距离为 `3 × ATR14`；止损、止盈、趋势反转，或持有达到 5 个交易日且临近收盘时触发退出判断。
- 执行前再次核对新鲜双边报价、限价、市场日历、新闻和联合账户风险；先处理到期撤单及已有持仓退出，再考虑新开仓。

### 港股股票

- 使用核验后的香港交易日历及完整的上一交易日信号，检查做多信号、流动性和候选身份。
- 执行还需满足连续交易时段、整手股数、合法价位、费用、独立预算及账户对账要求。
- 已有持仓的退出管理独立于新的候选名单；缺少开仓条件时不会据此忽略持仓管理。

### 美国期权

- 当前独立期权流程主要用于 **SIMULATE 传输验收**，不是已验证盈利的期权策略。
- 按 `撤单测试 → 撤单确认 → 买入 → 买入确认 → 卖出 → 卖出确认 → 完整对账` 推进。
- 合约、报价、独立预算和持仓必须再次核验；未成交、回包不明或未知订单不能直接视为成功，也不能自动补发替代订单。

### 期货研究

期货目前仍处于研究及接入检查阶段。必须先核验合约资格、行情权限、乘数、最小价位、保证金、费用、交易日历、执行能力及独立预算。本仓库不宣称已经启用或验收美港市场的期货自动执行。

## 连接自己的证券 API

1. 从证券平台的官方开发者渠道取得 API 权限，确认支持目标市场及模拟账户。若使用网关型 API，按平台文档安装并登录本机网关；若使用 Key / Secret、OAuth 或签名型 API，须为其实现对应适配器。不要把证券账户密码或 API 密钥写入源码。
2. 检查适配器是否匹配该平台及账户实体。新增适配器应先完成只读检查，再在平台模拟环境验证下单、撤单、部分成交、未知回包恢复和完整对账。
3. 安装当前项目依赖，并对附带的本地网关适配器运行只读诊断：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python connect.py --port 11111 --quotes --accounts
.venv/bin/python trade.py --port 11111 doctor
```

这些命令针对当前网关适配器，**不是任意平台通用的连接命令**。默认连接 `127.0.0.1:11111`，不要向公网开放；`doctor` 不启动自动策略。接入细节见 [适配器说明](docs/broker-adapters.md)。

4. 使用自己的配置与 SIMULATE 账户绑定。`trading/config.example.json` 中 `live_stocks` 和 `live_options` 保持 `false`：

```sh
cp trading/config.example.json data-local-config.json
.venv/bin/python trade.py --config data-local-config.json --port 11111 doctor
```

配置须通过 `Config.validate()`。旧示例使用 NZD 口径，不代表截图已迁移的独立 USD 账本。公开克隆不含原账本、账户绑定或验收证据；示例账号 `000001`（US）、`000002`（HK）必须替换并核验，不可重置已有账本绕过检查。

## 连接自己的 AI 分析 API（可选、只读）

当前新闻监测使用规则分析。独立研究入口 `trading/ai_analysis.py` 支持兼容 Chat Completions 的模型服务，**尚无 GUI 密钥设置页，也没有将 AI 输出接入自动下单**。

从模型服务商取得 API 根地址、支持的模型名和 API Key：

```sh
.venv/bin/python -m trading.ai_analysis \
  --base-url https://provider.example/v1 \
  --model your-model-name \
  --text 'Analyze this public company announcement. Separate facts, assumptions, risks and invalidation conditions.'
```

将示例地址和模型替换为自己的。程序隐藏输入地询问密钥，不保存；也支持 `AEGIS_AI_API_KEY` 环境变量。公网接口必须 HTTPS，仅回环地址允许 HTTP；接口需支持 `/chat/completions`。证券 API 和 AI API 使用不同权限及凭据。

仅发送你通过 `--text` 明确提供的材料，不自动读取账户、持仓、账本或证券凭据。默认英文输出，可在研究文本中指定语言。服务商可能收费；45 秒超时，不自动重试。只做隔离假服务测试，未用真实密钥进行付费联调。

## 目录与 Mac 启动

| 路径 | 用途 |
| --- | --- |
| `desktop/QuantDesktop.swift` | 英文 Mac 证券控制台 |
| `trading/` | 行情、扫描、模拟执行、对账、风控及只读 AI 研究 |
| `tests/` | 回归测试 |
| `examples/` | 人工合成演示数据 |
| 根目录 Python 脚本 | 连接诊断、回测与统计 |

需要兼容的 macOS SDK 与 SwiftUI 工具链：

```sh
zsh desktop/build.sh
open AegisQuant.app
```

构建生成 `AegisQuant.app`。正常启动会连接本机后台并按本地配置管理任务；先完成模拟环境配置和权限检查。`AEGIS_READ_ONLY=1` 启动应用可禁止其启动后台和发送操作请求，但需要已有状态服务才能显示数据。关闭窗口不会自动停止已运行的后台。

这是脱敏源码快照，不包含运行数据库、余额、成交历史、凭据、依赖目录或已构建 App。仓库保留的其他独立工具不参与证券控制台的连接和执行。

## 版权、商业使用与来源标注 / Copyright, commercial use, and attribution

### English

**Copyright © 2026 Ffffsss123. All rights reserved except as expressly granted by the repository [LICENSE](LICENSE).** This notice covers original material the author has the right to license. Third-party components remain subject to their own licenses.

Personal use, study, modification, derivative development, commercial use, and profit-generating use are permitted only with clear attribution to the original author and this repository. Paid products, services, trading for profit, personal rewrites, republication, copying, and redistribution must retain the source and must not present the author's work as wholly independent original work.

Retain this notice and an accessible original repository link:

> Based on or uses AegisQuant by Ffffsss123.
>
> Original source: https://github.com/Ffffsss123/AegisQuant
>
> Copyright © 2026 Ffffsss123. Subject to the original repository LICENSE.

When providing software or services to others, include attribution in the README / documentation and a user-accessible About, Copyright, or Credits page. Source distributions must also retain the copyright and LICENSE. Private use or trading solely for your own profit requires retaining the notice in the used copy's README or equivalent project documentation, without publishing trading records.

Identify modified versions as modified and do not imply the author's endorsement. **Use that fails to satisfy attribution or other license conditions is outside the permission granted; the author reserves the right to seek cessation, pursue liability, and bring legal proceedings under applicable law.** See [LICENSE](LICENSE) for the complete terms.

### 简体中文

**Copyright © 2026 Ffffsss123。保留所有权利；使用许可仅按本仓库 [LICENSE](LICENSE) 授予。** 本声明适用于作者有权授权的原创部分，第三方组件仍遵循各自许可。

允许个人使用、学习、修改、二次开发、商业使用及盈利，但上述使用均必须明确注明原作者和本仓库来源。商业产品、收费服务、量化交易盈利使用、个人改写、转载、复制或分发均不得删除或隐瞒来源，也不得把原作者的作品冒充为完全独立原创。

请保留以下声明及可访问的原仓库链接：

> 本项目基于 Ffffsss123 的 AegisQuant 项目开发或使用。
>
> 原始仓库：https://github.com/Ffffsss123/AegisQuant
>
> Copyright © 2026 Ffffsss123。使用须遵守原仓库 LICENSE。

对外提供软件或服务时，请在 README / 使用文档及用户可查看的“关于 / 版权 / 致谢”页面保留该声明；分发源码时，还须保留版权和 LICENSE。个人自用或仅用于自身交易盈利且不对外分发时，应在所使用副本的 README 或等效项目说明中保留声明，不要求公开交易记录。

修改版本须注明已经修改，不得暗示原作者为其背书。**不履行来源标注或其他许可条件的行为不在本许可授权范围内；作者保留依法要求停止使用、追究责任及提起诉讼的权利。** 完整条款以 [LICENSE](LICENSE) 为准。
