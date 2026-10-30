# Repository verification — 2026-10-30

[Project README](../README.md) · [Live setup](live-trading.md) · [中文实盘说明](live-trading.zh-CN.md)

This review checks the published source, documented workflows and screenshots. It does not constitute successful real-money execution acceptance. No REAL account was configured, bound, enabled or used to submit an order during this review.

## Corrections made

- Unified the English and Chinese READMEs: simulation is the default; US/HK cash-stock live execution has a separate explicit permission gate. Simulation ATR swing exits and live daily-trend exits are described separately.
- Restricted **Reconcile once** to account/order reconciliation and due cancellations. It does not call strategy decisions or submit new orders.
- Isolated market-specific maintenance faults. A fault blocks new buying; a healthy market can still handle its own due cancellations and strictly validated owned-position exits. The faulty market's account, quote, fee and ownership checks remain enforced.
- Rechecked the exact protection fingerprint before live intent and physical submission, and bound the worker heartbeat to its loaded implementation. Updating code requires successful validation and a controlled worker restart before new submissions. Cancellation and reconciliation remain available.
- Separated the process heartbeat from slow SDK calls. Heartbeat freshness indicates process liveness; fresh quotes, valuations and reconciliation are still checked independently before admission.
- Distinguished stopped and loss-latched states from merely armed or manually paused states in the English UI.
- Kept partial maintenance failures visible as **needs attention**, rather than reporting them as successful completion.
- Removed unreferenced historical Chinese-console and capabilities-poster images. Current documentation uses captures of the actual English securities console and disabled live controls.
- Deduplicated ignore rules and checked the tracked securities source for local account bindings, machine paths, runtime databases and private-key/token patterns. Unrelated legacy tools were removed from the current distribution.

## Validation

```sh
python -m unittest discover -s tests -q
```

The securities protection suite passed **819 tests**. Cross-market fault tests cover manual and automatic owned-position exits, blocked new buying, due cancellation, strict own-market failure rejection, and a heartbeat that continues while maintenance is waiting.

Relative documentation links and screenshot references were checked. English and Chinese copyright/attribution notices remain present. The public export excludes runtime ledgers, credentials and built applications; deployment must create its own local account binding, simulation evidence and protection fingerprint.

Current screenshots show a read-only capture session with live execution **disabled** and its worker **offline**. USD 1,500 in the sidebar belongs to the historical simulation configuration. Options remain simulation-only, futures remain research-only, and the ATR swing strategy has not been enabled for live execution.

## Securities-only distribution cleanup

The public repository now contains the securities console and its supporting tools. The unrelated exchange application and its launcher were removed from the current branch; original local files and a backup remain outside the public export. Five macOS shortcuts use English names and prompts. Obsolete untracked preview images were excluded. Top-level securities Python tools are intentionally retained. GitHub displays the last commit for each individual file, so an older date on an unchanged tool is expected. Historical Git commits are preserved.


### Homepage screenshot selection

The first screenshot in all three README editions now shows the actual English Live Trading controls; the gallery also shows news monitoring and the simulation overview. The simulation Overview is no longer the homepage screenshot. All three captures retain the disabled/offline state and do not claim a connected REAL account or real fills.


### Native workspace layout review · 2026-10-31

The native SwiftUI application builds successfully with a compact graphite layout, shared status bar and a two-column Live Trading workspace. Existing action payloads and disabled guards were compared with the previous source and preserved. Screenshots were captured from the built application using a local status service with POST rejected and AEGIS_READ_ONLY=1. No execution worker or broker connection was started. News and simulation records are historical. This presentation-only change does not change the Python protection fingerprint or provide REAL execution acceptance.


### Traditional desktop terminal review · 2026-10-31

The graphite presentation has been replaced by a light, native desktop terminal. Account state, strategy-owned positions, live order records, historical candidates and news use bordered tables with alternating rows. The window has a native title bar, Workspace menu, toolbar, collapsible navigation tree and bottom status bar. The fixed order-entry pane keeps the side, symbol, quantity, limit and exact confirmation separate. Extended operating rules and original diagnostics remain accessible through Help or disclosures.

The application was rebuilt and its real Overview, Live Trading and News screens inspected and recaptured. The backend Model implementation, live action payloads and disabled guards were compared with the previous revision and are unchanged. Python protection provenance still matches; this UI-only revision does not claim a fresh business-regression run. No broker connection, account binding, live enable, order or execution worker was started for the capture. REAL account tables are unbound in the screenshot; blank tables are not evidence of an empty broker account. Local financial position records were excluded from published screenshots.


### Position status language · 2026-10-31

Generated position sources, cost labels, order/holding states and quote freshness labels are translated at the native presentation layer. The status snapshot, broker records and Python implementation remain unchanged; original status labels are accessible in an explicit disclosure. Original instrument names retain their source language. The application builds successfully, with live action payloads and disabled guards unchanged. Private position screenshots are kept outside the public export.
