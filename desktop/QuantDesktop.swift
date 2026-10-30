import SwiftUI
import Charts
import AppKit

// Native desktop palette: system surfaces, ruled tables and standard controls.
let navy = Color(nsColor:.windowBackgroundColor)
let panel = Color(nsColor:.textBackgroundColor)
let mint = Color(red:0.05,green:0.25,blue:0.53)
let muted = Color(nsColor:.secondaryLabelColor)
let ink = Color(nsColor:.labelColor)
let rule = Color(nsColor:.separatorColor)
let warning = Color(red:0.64,green:0.23,blue:0.08)
func string(_ x: Any?) -> String { x as? String ?? "—" }
func number(_ x: Any?) -> Double { (x as? NSNumber)?.doubleValue ?? 0 }
func list(_ x: Any?) -> [[String:Any]] { x as? [[String:Any]] ?? [] }
func object(_ x: Any?) -> [String:Any] { x as? [String:Any] ?? [:] }
func yes(_ x: Any?) -> Bool { x as? Bool ?? false }
func liveStatus(_ value:Any?) -> String {
    let state=object(value)
    if yes(state["stopped"]) {return "STOP LATCHED"}
    if yes(state["loss_latched"]) {return "LOSS LIMIT LATCHED"}
    if !yes(state["enabled"]) {return "DISABLED"}
    return yes(state["paused"]) ? "ARMED · ENTRIES PAUSED" : "ARMED"
}
func money(_ x: Any?) -> String { String(format:"%.2f",number(x)) }
func dateValue(_ text: String) -> Date? {
    let f=ISO8601DateFormatter(); f.formatOptions=[.withInternetDateTime,.withFractionalSeconds]
    return f.date(from:text) ?? ISO8601DateFormatter().date(from:text)
}
func localTime(_ x: Any?) -> String {
    guard let d=dateValue(string(x)) else { return "No records" }
    let f=DateFormatter();f.dateFormat="MM/dd HH:mm:ss";return f.string(from:d)
}

enum Page: String, CaseIterable {
    case start="Overview", pause="Pause", positions="Positions", returns="Returns", news="News", live="Live Trading"
    var icon: String { switch self {case .start:return "square.grid.2x2";case .pause:return "pause.circle";case .positions:return "tray.full";case .returns:return "chart.xyaxis.line";case .news:return "newspaper";case .live:return "lock.shield"} }
    var subtitle: String { switch self {case .start:return "Track each step from preparation to simulation.";case .pause:return "Pause new entries while retaining reconciliation and exits.";case .positions:return "View strategy simulations and existing live positions separately.";case .returns:return "Weekly results calculated from recorded activity.";case .news:return "Review external news, sources and supporting evidence.";case .live:return "Separate REAL accounts, cash budgets and explicit execution controls."} }
}

@MainActor final class Model: ObservableObject {
    @Published var data:[String:Any]=[:]
    @Published var message="Connecting to local service…"
    @Published var error=""
    @Published var busy=false
    @Published var connected=false
    var fetching=false
    let readOnly = ProcessInfo.processInfo.environment["AEGIS_READ_ONLY"] == "1"
    let root:URL
    var timer:Timer?
    init() {
        let resource=Bundle.main.url(forResource:"project",withExtension:"txt")!
        let path=(try? String(contentsOf:resource,encoding:.utf8))?.trimmingCharacters(in:.whitespacesAndNewlines) ?? ""
        root=URL(fileURLWithPath:path)
        startBackend()
        timer=Timer.scheduledTimer(withTimeInterval:3,repeats:true) { [weak self] _ in Task { @MainActor in self?.refresh() } }
        DispatchQueue.main.asyncAfter(deadline:.now()+1) { self.refresh() }
    }
    func startBackend() {
        guard !readOnly else { return }
        let p=Process();p.executableURL=root.appendingPathComponent(".venv/bin/python")
        p.arguments=["-m","trading.desktop_server"];p.currentDirectoryURL=root
        let dir=root.appendingPathComponent("data/desktop");try? FileManager.default.createDirectory(at:dir,withIntermediateDirectories:true)
        let log=dir.appendingPathComponent("server.log")
        if !FileManager.default.fileExists(atPath:log.path) { FileManager.default.createFile(atPath:log.path,contents:nil) }
        if let h=try? FileHandle(forWritingTo:log) { _ = try? h.seekToEnd();p.standardOutput=h;p.standardError=h }
        do {try p.run()} catch {self.error="Local service failed to start: \(error.localizedDescription)"}
    }
    func request(_ path:String,action:String?=nil,payload:[String:Any]?=nil,completion:@escaping([String:Any]?,String?)->Void) {
        let session=root.appendingPathComponent("data/desktop/session.json")
        guard let bytes=try? Data(contentsOf:session),let info=(try? JSONSerialization.jsonObject(with:bytes)) as? [String:Any],let port=info["port"] as? Int,let token=info["token"] as? String,let url=URL(string:"http://127.0.0.1:\(port)/\(path)") else {completion(nil,"Local service is not ready");return}
        var r=URLRequest(url:url);r.timeoutInterval=12;r.setValue(token,forHTTPHeaderField:"X-Local-Token")
        if let body=payload ?? action.map({["action":$0]}) {r.httpMethod="POST";r.httpBody=try? JSONSerialization.data(withJSONObject:body);r.setValue("application/json",forHTTPHeaderField:"Content-Type")}
        URLSession.shared.dataTask(with:r) { bytes,response,err in
            let obj=bytes.flatMap { try? JSONSerialization.jsonObject(with:$0) } as? [String:Any]
            let problem=err?.localizedDescription ?? (obj?["error"] as? String)
            DispatchQueue.main.async { completion(obj,problem) }
        }.resume()
    }
    func refresh() {
        guard !fetching else {return};fetching=true
        request("status") { [weak self] obj,problem in
            guard let self=self else {return};self.fetching=false
            if let problem=problem {self.connected=false;self.error=problem;return}
            self.data=obj ?? [:];self.connected=true;self.error=""
            if self.message=="Connecting to local service…" {self.message="Local service connected"}
        }
    }
    func action(_ name:String) {
        guard !readOnly else { message="Read-only session: trading actions disabled"; return }
        busy=true
        request("action",action:name) { [weak self] obj,problem in
            guard let self=self else {return};self.busy=false
            self.message=problem ?? string(obj?["message"]);self.refresh()
        }
    }
    func liveAction(_ payload:[String:Any]) {
        guard !readOnly else {message="Read-only session: live operations disabled";return}
        busy=true
        request("live",payload:payload) { [weak self] obj,problem in
            guard let self=self else {return};self.busy=false
            self.message=problem ?? string(obj?["message"]);self.refresh()
        }
    }
    func running(_ name:String)->Bool {yes(object(data["running"])[name])}
    func openFolder(_ path:String) {NSWorkspace.shared.open(root.appendingPathComponent(path))}
}

struct Badge: View {
    var text:String;var color:Color=mint
    var body:some View {HStack(spacing:5){Rectangle().fill(color).frame(width:5,height:5);Text(text).font(.system(size:11))}.foregroundStyle(color)}
}
struct Box<Content:View>:View {
    @ViewBuilder var content:Content
    var body:some View {VStack(alignment:.leading,spacing:8){content}.padding(10).frame(maxWidth:.infinity,alignment:.leading).background(panel).border(rule)}
}
struct ClassicSection<Content:View>:View {
    let title:String
    @ViewBuilder var content:Content
    var body:some View {VStack(alignment:.leading,spacing:0){
        HStack {Text(title).font(.system(size:11,weight:.semibold));Spacer()}.padding(.horizontal,8).frame(height:25).background(Color(nsColor:.controlBackgroundColor))
        Divider()
        VStack(alignment:.leading,spacing:8){content}.padding(8).frame(maxWidth:.infinity,alignment:.leading)
    }.background(panel).border(rule)}
}
struct Metric:View {
    var title:String;var value:String;var note:String
    var body:some View {HStack{Text(title).foregroundStyle(muted);Spacer();Text(value).monospacedDigit()}.font(.system(size:12)).padding(8).background(panel).border(rule).help(note)}
}
struct Empty:View {
    var icon:String;var title:String;var detail:String
    var body:some View {VStack(alignment:.leading,spacing:5){Text(title).font(.system(size:12));Text(detail).font(.system(size:11)).foregroundStyle(muted)}.frame(maxWidth:.infinity,alignment:.leading).padding(10)}
}
struct DesktopRecord:Identifiable {
    let id:Int
    let values:[String:Any]
}
func records(_ values:[[String:Any]])->[DesktopRecord] {values.enumerated().map {DesktopRecord(id:$0.offset,values:$0.element)}}
func optionalMoney(_ value:Any?)->String {value is NSNumber ? money(value):"—"}

struct MainView: View {
    @StateObject var m=Model()
    @State var page:Page = .start
    @State var securitiesExpanded=true
    @State var executionExpanded=true
    @State var researchExpanded=true
    @State var showHelp=false
    @State var showDiagnostics=false
    @State var query=""
    @State var newsSource="All"
    @State var pauseConfirm=false
    @State var liveConfirmation=""
    @State var orderConfirmation=""
    @State var liveMarket="US"
    @State var liveSide="BUY"
    @State var liveCode=""
    @State var liveQuantity=""
    @State var liveLimit=""
    var scan:[String:Any] {object(m.data["scan"])}
    var probe:[String:Any] {object(m.data["probe"])}
    var body: some View {
        VStack(spacing:0){
            HStack(spacing:8){
                Button {m.refresh()} label:{Label("Refresh",systemImage:"arrow.clockwise")}.help("Refresh local records; no order is submitted")
                Divider().frame(height:20)
                ForEach([Page.start,.positions,.returns,.news,.live,.pause],id:\.self){p in
                    Button {page=p} label:{Label(p.rawValue,systemImage:p.icon)}
                }
                Spacer()
                Button("Help"){showHelp.toggle()}.popover(isPresented:$showHelp,arrowEdge:.bottom){
                    VStack(alignment:.leading,spacing:12){
                        Text("AegisQuant Help").font(.headline)
                        Text(page.subtitle)
                        Text("Live stocks require verified accounts, a matching natural simulation round trip, current protection checks and clean reconciliation. Enabling and starting the worker are separate operations. New buys also require a later trading session, complete scans, valid stock identity, fresh bid/ask, news, cash and risk checks.")
                        Text("Pause retains exits, cancellations and reconciliation. Stop blocks all new orders, including automatic exits. Neither guarantees an empty account. Unknown responses require reconciliation before any retry. Fixed limits; cancellation due after 60 seconds. Manual holdings cannot be sold here.")
                        Text("Only US/HK common stocks support live execution. Options and futures remain disabled. No borrowing, deposits or currency conversion. USD 390 reserve remains excluded. Closing the window does not stop existing workers.")
                        Button("Open live trading guide"){m.openFolder("docs/live-trading.md")}
                    }.font(.system(size:12)).padding(18).frame(width:480)
                }
            }.buttonStyle(.bordered).controlSize(.small).padding(.horizontal,8).frame(height:37).background(navy)
            Divider()
            HStack(spacing:0){
                navigator.frame(width:172)
                Divider()
                VStack(spacing:0){
                    HStack {Text(page.rawValue).font(.system(size:12,weight:.semibold));Spacer();Text("Records: "+localTime(m.data["updated_at"])).font(.system(size:11)).foregroundStyle(muted)}.padding(.horizontal,10).frame(height:29).background(Color(nsColor:.controlBackgroundColor))
                    Divider()
                    if !m.error.isEmpty {HStack {Label(m.error,systemImage:"exclamationmark.triangle").foregroundStyle(warning);Spacer();Button("Reconnect"){m.startBackend();m.refresh()}}.font(.system(size:11)).padding(8).background(Color.yellow.opacity(0.12));Divider()}
                    ScrollView {VStack(alignment:.leading,spacing:10){
                        switch page {case .start:startPage;case .pause:pausePage;case .positions:positionsPage;case .returns:returnsPage;case .news:newsPage;case .live:livePage}
                    }.padding(10)}.background(panel)
                }
            }
            Divider()
            HStack(spacing:10){Badge(text:m.connected ? "Local service connected":"Local service offline",color:m.connected ? Color(red:0.1,green:0.4,blue:0.15):warning);Divider().frame(height:13);Text(m.readOnly ? "Read-only session":"Live: "+liveStatus(m.data["live"]));Divider().frame(height:13);Text(m.message).lineLimit(1);Spacer();Button("Project folder"){m.openFolder("")}.buttonStyle(.plain)}.font(.system(size:10)).padding(.horizontal,8).frame(height:24).background(navy)
        }.background(navy).foregroundStyle(ink).font(.system(size:12)).controlSize(.small).frame(minWidth:1120,minHeight:740).preferredColorScheme(.light)
        .onReceive(NotificationCenter.default.publisher(for:Notification.Name("AegisNavigate"))){notification in
            if let name=notification.object as? String,let destination=Page(rawValue:name) {page=destination}
        }
        .onReceive(NotificationCenter.default.publisher(for:Notification.Name("AegisRefresh"))){_ in m.refresh()}
    }
    var navigator:some View {
        VStack(alignment:.leading,spacing:0){
            Text("Navigator").font(.system(size:11,weight:.semibold)).padding(.horizontal,8).frame(height:29)
            Divider()
            ScrollView {VStack(alignment:.leading,spacing:9){
                Label("Local workspace",systemImage:"desktopcomputer").font(.system(size:11,weight:.semibold))
                DisclosureGroup("Securities",isExpanded:$securitiesExpanded) {
                    navItem(.start);navItem(.positions);navItem(.returns)
                }
                DisclosureGroup("Execution",isExpanded:$executionExpanded) {navItem(.live);navItem(.pause)}
                DisclosureGroup("Research",isExpanded:$researchExpanded) {navItem(.news)}
                Divider()
                Text("REAL accounts").font(.system(size:11,weight:.semibold))
                Text(yes(object(m.data["live"])["configured"]) ? "Local configuration saved":"Not configured").font(.system(size:11)).foregroundStyle(muted)
                ForEach(records(list(object(m.data["live"])["markets"]))) {r in
                    Label(string(r.values["market"])+" · "+string(r.values["currency"]),systemImage:"person.text.rectangle").font(.system(size:11))
                }
            }.padding(8)}
            Divider()
            VStack(alignment:.leading,spacing:6){Text("Simulation budget").foregroundStyle(muted);Text(money(m.data["simulation_total_budget_usd"] ?? m.data["budget_usd"])+" USD").monospacedDigit();Text("Reserve: "+money(object(m.data["funding"])["reserve_usd"])+" USD").foregroundStyle(muted)}.font(.system(size:11)).padding(8)
        }.frame(maxHeight:.infinity).background(navy)
    }
    func navItem(_ p:Page)->some View {
        Button {page=p} label:{HStack(spacing:6){Image(systemName:p.icon).frame(width:14);Text(p.rawValue);Spacer()}.font(.system(size:11)).padding(.horizontal,5).frame(height:23).foregroundStyle(page==p ? Color.white:ink).background(page==p ? mint:.clear)}.buttonStyle(.plain)
    }
    func actionButton(_ title:String,_ action:String,primary:Bool=false)->some View {
        Button(title){m.action(action)}.buttonStyle(.bordered).disabled(m.readOnly || m.busy || !m.connected)
    }
    var startPage:some View {
        VStack(alignment:.leading,spacing:10){
            ClassicSection(title:"Simulation control"){
                HStack {actionButton("Supplemental scan","prepare");actionButton("Transport acceptance","probe");Spacer();Text("Signal date: "+string(scan["date"])).foregroundStyle(muted)}
                HStack(spacing:18){Badge(text:scanActivityLabel,color:m.running("automation") || m.running("scan") ? mint:muted);Badge(text:m.running("news") ? "News running":"News offline",color:m.running("news") ? mint:muted);Text("Strategy: "+strategyStatus(string(object(m.data["strategy"])["status"]))).foregroundStyle(muted)}.font(.system(size:11))
            }
            ClassicSection(title:"Market scan summary"){
                HStack{Text("History coverage");Text("\(Int(number(scan["history_count"])))").monospacedDigit();Divider().frame(height:15);Text("Evaluated");Text("\(Int(number(scan["processed_count"] ?? scan["history_count"]))) / \(Int(number(scan["eligible_count"])))").monospacedDigit();Divider().frame(height:15);Text("Candidates");Text("\(Int(number(scan["candidate_count"])))").monospacedDigit();Spacer()}.font(.system(size:11))
                Text("Historical scan records. Candidates are not order instructions; no current bid/ask is inferred from this list.").font(.system(size:11)).foregroundStyle(muted)
            }
            ClassicSection(title:"Candidate list — historical signals"){
                Table(records(list(m.data["candidates"]).prefix(50).map{$0})) {
                    TableColumn("No."){r in Text(String(r.id+1)).monospacedDigit()}.width(36)
                    TableColumn("Symbol"){r in Text(string(r.values["code"])).font(.system(size:11,design:.monospaced))}.width(min:90,ideal:130)
                    TableColumn("20-day change"){r in Text(r.values["strength"] is NSNumber ? String(format:"%+.2f%%",number(r.values["strength"])*100):"—").monospacedDigit()}.width(min:95,ideal:125)
                    TableColumn("Signal date"){_ in Text(string(scan["date"]))}.width(min:90,ideal:120)
                }.tableStyle(.bordered(alternatesRowBackgrounds:true)).frame(height:250)
                if list(m.data["candidates"]).isEmpty {Text("No candidates loaded; an absent record is not a buy signal.").foregroundStyle(muted)}
            }
            ClassicSection(title:"Diagnostics and acceptance"){
                Text("Stock transport: "+probeStatus+"   |   Options: "+optionTransportStatus(string(object(m.data["option_transport"])["status"]))).font(.system(size:11))
                let unresolved=list(m.data["alert_records"]).filter{!yes($0["resolved"])}
                Text("Unresolved recorded issues: \(unresolved.count). Pause and fault records remain retained.").font(.system(size:11)).foregroundStyle(unresolved.isEmpty ? muted:warning)
                DisclosureGroup("Show full simulation diagnostics",isExpanded:$showDiagnostics){detailedSimulationPage.padding(.top,8)}
            }
        }
    }
    var detailedSimulationPage:some View {
        VStack(spacing:20){
            HStack(spacing:14){Metric(title:"Simulation budget",value:money(m.data["simulation_total_budget_usd"] ?? m.data["budget_usd"]),note:yes(m.data["independent_budget_display"]) ? "US stocks / US options / HK stocks: USD 500 each" : "USD · subject to ledger allocation");Metric(title:"Daily history coverage",value:"\(Int(number(scan["history_count"])))",note:"At least 60 completed daily bars");Metric(title:"Current candidates",value:"\(Int(number(scan["candidate_count"] ?? list(m.data["candidates"]).count)))",note:"Candidates are not buy instructions")}
            Box {
                HStack{VStack(alignment:.leading,spacing:7){Text("Simulation operations").font(.system(size:15,weight:.semibold));Text("Market scans, external news and simulation acceptance run separately. Live stock controls are separate in Live Trading.") .font(.callout).foregroundStyle(muted)};Spacer()}
                HStack(spacing:12){actionButton(m.running("scan") ? "Scan in progress":"Run supplemental scan","prepare",primary:true);actionButton(string(probe["status"]) == "transport_passed" ? "Transport acceptance passed":m.running("probe") ? "Simulation acceptance running":"Start / resume simulation acceptance","probe");Spacer()}
                HStack(spacing:8){Badge(text:scanActivityLabel,color:m.running("automation") || m.running("scan") ? mint:muted);Badge(text:m.running("news") ? "News monitoring":"News offline",color:m.running("news") ? mint:muted);Badge(text:m.running("strategy") ? "Strategy simulation running":m.running("probe") ? "Transport test running":string(probe["status"]) == "transport_passed" ? "Transport acceptance passed":"Transport test offline",color:(m.running("strategy") || m.running("probe")) ? mint:muted)}
                if m.running("automation") {
                    Text("Automatic scan enabled · runs 5 minutes after each trading close. Next: "+localTime(object(m.data["scan_schedule"])["next_run_at"])).font(.caption).foregroundStyle(muted)
                } else {Text("Scheduler is offline; checking service status.").font(.caption).foregroundStyle(warning)}
            }
            HStack(alignment:.top,spacing:18){
                Box {Text("Market coverage").font(.headline);HStack{Text("Symbols evaluated").foregroundStyle(muted);Spacer();Text("\(Int(number(scan["processed_count"] ?? scan["history_count"]))) / \(Int(number(scan["eligible_count"])))").monospacedDigit()};ProgressView(value:min(number(scan["processed_count"] ?? scan["history_count"])/max(number(scan["eligible_count"]),1),1)).tint(mint);Text("Valid daily history \(Int(number(scan["history_count"]))) · Excluded \(Int(number(scan["excluded_count"]))) · Pending \(Int(number(scan["pending_count"])))").font(.caption).foregroundStyle(muted);Text(yes(scan["complete"]) ? "Market scan complete. Exclusions include low prices, suspensions and insufficient history.":"Collecting completed daily bars. Incomplete scans cannot admit new entries.") .font(.caption).foregroundStyle(muted);Text("Signal date "+string(scan["date"])).font(.caption).foregroundStyle(muted)}
                Box {Text("Simulation acceptance").font(.headline);Badge(text:probeStatus,color:yes(m.data["probe_paused"]) || ["waiting_quote","quote_expired","waiting_spread"].contains(string(probe["status"])) ? warning:mint);Text("Cancel → buy one unit → sell → reconcile").font(.callout);Text("SIMULATE accounts only. Transport acceptance does not establish profitability or enable live trading.").font(.caption).foregroundStyle(muted);if let wait=probe["quote_wait"] as? [String:Any] {Text("Quote wait \(Int(number(wait["elapsed_seconds"]))) / 120 s · checked every 5 s; cancellation and reconciliation continue").font(.caption).foregroundStyle(muted)};Text("Record "+localTime(probe["updated_at"])).font(.caption).foregroundStyle(muted)}
            }
            Box {
                let schedule=object(m.data["scan_schedule"])
                let automation=object(m.data["automation"])
                let strategy=object(m.data["strategy"])
                Text("Daily scans and strategy simulation").font(.headline)
                Text("Target trading date "+string(schedule["target_date"])+" · Next closing scan "+localTime(schedule["next_run_at"])).font(.caption).foregroundStyle(muted)
                Text("Scan status: "+scheduleStatus(string(schedule["status"]))).font(.callout)
                if let retry=schedule["retry_at"] as? String {Text("Retry at "+localTime(retry)).font(.caption).foregroundStyle(warning)}
                if let reason=schedule["reason"] as? String {Text(reason).font(.caption).foregroundStyle(warning).textSelection(.enabled)}
                Text("Simulation handoff: "+handoffStatus(string(automation["handoff"]))).font(.callout)
                if let reason=automation["reason"] as? String {Text(reason).font(.caption).foregroundStyle(warning)}
                if let reason=automation["scan_error"] as? String {Text(reason).font(.caption).foregroundStyle(warning)}
                Text("Strategy status: "+strategyStatus(string(strategy["status"]))).font(.callout)
                Text("Verified adverse news may trigger an early exit before the loss threshold; news collection runs every 2 minutes.").font(.caption).foregroundStyle(muted)
                if string(strategy["strategy_version"]) == "swing_1_5_v1" {
                    Text("Swing simulation · 1–5 trading days · overnight positions allowed").font(.headline).foregroundStyle(mint)
                    Text("5-day breakout entry; 1.5 ATR stop, 3 ATR target. Attempt exit 15 minutes before the fifth trading day's close. Planned risk per trade: \(money(m.data["single_risk_usd"])) USD, including fee reserve; gaps may exceed this limit.").font(.caption).foregroundStyle(muted)
                    Text("Local stops require the background service, network and broker gateway. Exits depend on quotes, limit fills and cancellation results.").font(.caption).foregroundStyle(muted)
                }
                let temporary=object(strategy["temporary_entry_window"])
                if string(strategy["entry_timing"]) == "regular_session" {Text("Simulation entries: from 5 minutes after open until regular close; duplicate entries are prevented").font(.caption).foregroundStyle(mint)} else if let expiry=dateValue(string(temporary["expires_at"])),expiry>Date() {Text("Temporary simulation entry window ends at "+localTime(temporary["expires_at"])).font(.caption).foregroundStyle(mint)} else if let day=strategy["entry_after_session"] as? String {Text("Default entry 09:35–09:40 New York; first new buy must be after "+day).font(.caption).foregroundStyle(muted)}
                let urgent=object(strategy["emergency_exits"])
                if !urgent.isEmpty {Text("Adverse-news exit pending: "+urgent.keys.sorted().joined(separator:", ")).font(.caption).foregroundStyle(warning)}
                if yes(strategy["candidate_fallback_enabled"]) {Text("Candidate substitution enabled · check up to 10 symbols daily · hold up to 2").font(.caption).foregroundStyle(muted)}
                if let block=strategy["entry_global_block"] as? [String:Any] {Text("Global news block: "+(block["reasons"] as? [String] ?? []).joined(separator:"；")).font(.caption).foregroundStyle(warning)}
                if let reason=strategy["data_block"] as? String {Text(reason).font(.caption).foregroundStyle(warning)}
                if let reason=strategy["reason"] as? String {Text(reason).font(.caption).foregroundStyle(warning)}
                if let wait=strategy["quote_wait"] as? [String:Any] {Text("Waiting for "+string(wait["code"])+" valid quotes: \(Int(number(wait["elapsed_seconds"]))) / 120 s").font(.caption).foregroundStyle(warning)}
                Text("After transport acceptance, reconcile the simulation account before strategy simulation. Simulation acceptance does not arm live trading.").font(.caption).foregroundStyle(muted)
            }
            Box {
                let options=object(m.data["options"])
                Text("Options · independent quote research").font(.headline)
                Text(optionsStatus(string(options["status"]))).font(.callout)
                Text("Real quotes with assumed bid/ask fills, not broker execution acceptance. Research up to 20 eligible trend leaders. Research allocation: \(money(options["budget_usd"])) USD; broker execution budget is zero. Actual option simulation uses a separate ledger.").font(.caption).foregroundStyle(muted)
                if let error=options["error"] as? String {Text(error).font(.caption).foregroundStyle(warning)}
                if let retry=options["retry_at"] as? String {Text("Next retry "+localTime(retry)).font(.caption).foregroundStyle(muted)}
            }
            Box {
                let transport=object(m.data["option_transport"])
                Text("Options · broker simulation acceptance").font(.headline)
                Text(optionTransportStatus(string(transport["status"]))).font(.callout)
                Text("Phase: "+string(transport["phase"])+" · Broker orders: \(list(transport["orders"]).count)").font(.caption)
                if let code=transport["code"] as? String {Text(code).font(.caption)}
                if let reason=transport["reason"] as? String {Text(reason).font(.caption).foregroundStyle(warning)}
                ForEach(transport["alerts"] as? [String] ?? [],id:\.self){message in Text(message).font(.caption).foregroundStyle(warning)}
                Text("Cancellation, buy fills, sell fills and reconciliation are recorded separately. Passing verifies the transport flow, not strategy performance or live readiness.").font(.caption).foregroundStyle(muted)
            }
            let allAlertRows=m.data["alert_records"] as? [[String:Any]] ?? []
            let alertRows=allAlertRows.filter { ($0["resolved"] as? Bool) != true }
            let resolvedRows=allAlertRows.filter { ($0["resolved"] as? Bool) == true }
            let taskErrors=m.data["errors"] as? [String] ?? []
            if !alertRows.isEmpty || !taskErrors.isEmpty {Box {
                Label("Attention required · alerts",systemImage:"exclamationmark.triangle").foregroundStyle(warning)
                Text("Issues without confirmed recovery. Times are local.").font(.caption2).foregroundStyle(muted)
                ForEach(Array(alertRows.enumerated()),id:\.offset){_,row in
                    VStack(alignment:.leading,spacing:5) {
                        HStack {Text(string(row["message"])).font(.callout);Spacer();Text(string(row["source"])).font(.caption2).foregroundStyle(muted)}
                        if row["last_seen"] is String {
                            Text("First "+localTime(row["first_seen"])+" · Latest "+localTime(row["last_seen"])+" · Recorded \(Int(number(row["count"]))) times").font(.caption).foregroundStyle(muted)
                        } else {Text("Timestamp unavailable").font(.caption).foregroundStyle(muted)}
                        DisclosureGroup("View original record") {ForEach(row["details"] as? [String] ?? [],id:\.self){Text($0).font(.caption2).foregroundStyle(muted).textSelection(.enabled)}}.font(.caption)
                    }.padding(.vertical,5)
                    Divider().opacity(0.2)
                }
                ForEach(taskErrors,id:\.self){Text($0+" · Timestamp unavailable").font(.caption).foregroundStyle(warning)}
            }}
            if !resolvedRows.isEmpty {Box {
                DisclosureGroup("Resolved · \(resolvedRows.count) issues (view records)") {
                    ForEach(Array(resolvedRows.enumerated()),id:\.offset){_,row in
                        Text(string(row["message"])+" · Resolved · "+localTime(row["resolved_at"])).font(.caption).foregroundStyle(muted).padding(.vertical,3)
                    }
                    Text("Recovered faults are archived here; pause and risk states remain separately retained.").font(.caption2).foregroundStyle(muted)
                }
            }}
            Box {HStack{Text("Candidate watchlist").font(.headline);Spacer();Text("Ranked by strategy · up to 50 shown").font(.caption).foregroundStyle(muted)};if list(m.data["candidates"]).isEmpty {Empty(icon:"list.bullet.rectangle",title:"No candidates yet",detail:"Candidates appear after a valid daily scan. Stay in cash when none qualify.") } else {ForEach(Array(list(m.data["candidates"]).prefix(6).enumerated()),id:\.offset){i,c in HStack{Text(String(format:"%02d",i+1)).foregroundStyle(muted).frame(width:28);Text(string(c["code"])).font(.system(.body,design:.monospaced));Spacer();Text(String(format:"20-day change %+.2f%%",number(c["strength"])*100)).foregroundStyle(mint)}.padding(.vertical,5)}}}
        }
    }
    var scanActivityLabel:String {
        if m.running("scan") {return "Market scan running"}
        if !m.running("automation") {return "Scheduler offline"}
        if object(m.data["automation"])["scan_error"] is String {return "Scan scheduler fault · view details"}
        switch string(object(m.data["scan_schedule"])["status"]) {
        case "complete":return "Cycle complete · awaiting scheduled scan"
        case "retry_wait":return "Scan awaiting automatic retry"
        case "needs_attention":return "Scan fault · attention required"
        default:return "Automatic scan pending"
        }
    }
    var probeStatus:String {switch string(probe["status"]) {case "waiting_spread":return "Waiting for spread ≤ 0.2%";case "query_retry":return "Read-only query timed out · retrying";case "waiting_regular_session_0935_ET":return "Awaiting regular market session";case "transport_passed":return "Simulation transport accepted";case "waiting_quote":return "Awaiting valid quotes";case "quote_expired":return "Quote expired · awaiting update";case "quote_wait_timeout":return "Quote wait timed out · paused";case "connection_interrupted":return "Connection interrupted · paused";case "needs_attention":return "Paused · review required";case "running":return "Simulation acceptance in progress";default:return "Acceptance incomplete"}}
    func scheduleStatus(_ value:String)->String {switch value {case "complete":return "Trading day complete";case "running":return "Scanning";case "retry_wait":return "Awaiting automatic retry";case "needs_attention":return "Retries exhausted · attention required";default:return "Awaiting schedule"}}
    func optionTransportStatus(_ value:String)->String {switch value {
        case "transport_passed":return "Option simulation transport accepted"
        case "waiting_qualified_contract":return "Awaiting a contract within budget and liquidity limits"
        case "candidate_validation_wait":return "Awaiting candidate data validation"
        case "waiting_regular_session", "waiting_regular_session_0935_ET":return "Awaiting regular market session"
        case "needs_attention":return "Acceptance paused · review required"
        case "running":return "Broker simulation acceptance in progress"
        case "waiting_quote", "quote_expired", "waiting_spread":return "Awaiting valid bid and ask"
        case "query_retry":return "Retrying read-only query"
        default:return "Option broker acceptance incomplete"
    }}
    func optionExclusion(_ value:String)->String {[
        "nonstandard_suspended_or_other_direction":"Nonstandard, suspended or wrong direction",
        "delta":"Delta outside limits","open_interest":"Insufficient open interest","volume":"Insufficient daily volume",
        "stale_snapshot":"Snapshot expired","missing_invalid_data":"Missing or invalid data",
        "nonstandard_multiplier":"Invalid contract multiplier","budget_including_roundtrip_reserve":"Premium and round-trip fees exceed budget",
        "option_data_unverified":"Option data unverified","snapshot_book_missing":"Bid or ask missing",
        "qualified_before_final_quote_news_budget_checks":"Final quote, news and budget checks pending",
        "valid_books_last_check":"Recent valid bid/ask sizes","subscription_capacity":"Insufficient subscription capacity"
    ][value] ?? value}
    func optionsStatus(_ value:String)->String {switch value {
        case "waiting_regular_session":return "Awaiting US regular market session"
        case "waiting_daily_scan":return "Awaiting complete daily signal"
        case "scanning":return "Scanning option chains and real quotes"
        case "round_complete_no_entry":return "Research cycle complete; awaiting next cycle"
        case "position_monitoring":return "Monitoring paper positions"
        case "connection_or_data_error":return "Connection or data fault; awaiting retry"
        case "needs_attention":return "Retries exhausted; review required"
        default:return "Preparing option quote research"
    }}
    func handoffStatus(_ value:String)->String {switch value {case "waiting_probe_exit":return "Awaiting transport test completion";case "waiting_interface_acceptance":return "Awaiting transport acceptance";case "strategy_running":return "Strategy simulation running";case "checking_account_and_handoff":return "Reconciling account and ledger";case "blocked":return "Attention required";default:return "Awaiting review"}}
    func strategyStatus(_ value:String)->String {switch value {case "strategy_running":return "Strategy simulation running; acceptance incomplete";case "strategy_passed":return "Strategy acceptance passed; simulation continues";case "paused":return "New buys paused; reconciliation and exits continue";case "stopped":return "Trading stopped; reconciliation and cancellations continue";case "needs_attention":return "Attention required";default:return "Not started"}}
    var pausePage:some View {
        VStack(spacing:20){Box{HStack{Image(systemName:"pause.circle").font(.system(size:42,weight:.light)).foregroundStyle(warning);VStack(alignment:.leading,spacing:8){Text("Pause simulation entries").font(.title2.weight(.semibold));Text("Save the pause state first, then attempt to cancel outstanding simulation buy orders.") .foregroundStyle(muted)}};Text("Pausing does not confirm cancellation or automatically close positions. These controls do not manage existing live positions.").font(.callout).foregroundStyle(muted);HStack {actionButton("Pause simulation entries","pause");actionButton("Stop simulation trading","stop")};Badge(text:(yes(m.data["probe_paused"]) || yes(m.data["paper_paused"])) ? "Simulation ledger paused":"See the latest task status",color:warning)};Box{Text("Operating limits").font(.headline);Text("• Simulation budget \(money(m.data["simulation_total_budget_usd"] ?? m.data["budget_usd"])) USD; reserve \(money(object(m.data["funding"])["reserve_usd"])) USD\n• Cumulative strategy loss \(money(m.data["loss_limit_usd"])) USD pauses new buys\n• Simulation controls do not enable live trading\n• Closing this window does not stop background tasks\n• Keep the computer online and broker gateway signed in") .font(.callout).foregroundStyle(muted).lineSpacing(9);Button("View operating guide"){m.openFolder("两项准备任务.md")}}}
    }
    var positionsPage:some View {
        VStack(alignment:.leading,spacing:10){
            ClassicSection(title:"Position records — read only"){
                HStack{actionButton("Refresh account positions","account");Spacer();Text("Snapshot: "+localTime(m.data["account_updated"])).foregroundStyle(muted)}
                Text("Simulation, research and external holdings retain their source labels. Manual positions are not strategy-owned; this page cannot submit orders.").font(.system(size:11)).foregroundStyle(muted)
                if let error=m.data["account_error"] as? String {Text(error).foregroundStyle(warning)}
            }
            Table(records(list(m.data["positions"]))) {
                TableColumn("Symbol"){r in Text(string(r.values["code"]))}.width(min:85,ideal:105)
                TableColumn("Source"){r in Text(string(r.values["source"]))}.width(min:115,ideal:160)
                TableColumn("Qty"){r in Text(optionalMoney(r.values["qty"])).monospacedDigit()}.width(70)
                TableColumn("CCY"){r in Text(string(r.values["currency"]))}.width(45)
                TableColumn("Cost"){r in Text(optionalMoney(r.values["buy_price"])).monospacedDigit()}.width(80)
                TableColumn("Last"){r in Text(optionalMoney(r.values["current_price"])).monospacedDigit()}.width(80)
                TableColumn("Quote status"){r in Text(string(r.values["price_status"]))}
            }.tableStyle(.bordered(alternatesRowBackgrounds:true)).frame(height:280).border(rule)
            if list(m.data["positions"]).isEmpty {Text("No positions loaded. Missing data does not establish an empty account.").foregroundStyle(muted)}
            DisclosureGroup("Position details, valuation and original source records"){positionDetailsPage.padding(.top,8)}
        }
    }
    var positionDetailsPage:some View {
        VStack(spacing:20){Box{HStack{VStack(alignment:.leading,spacing:6){Text("Existing live positions · read-only").font(.headline);Text("Manual positions are not attributed to the strategy. This page does not submit orders.").font(.caption).foregroundStyle(muted)};Spacer();actionButton("Refresh account positions","account")};Text("Account snapshot: "+localTime(m.data["account_updated"])).font(.caption).foregroundStyle(muted);if let err=m.data["account_error"] as? String {Text(err).foregroundStyle(warning).font(.caption)}}
            Box{if list(m.data["positions"]).isEmpty {Empty(icon:"tray",title:"No loaded positions",detail:"Refresh above to fetch an account snapshot. Missing data does not establish an empty account.") } else {ForEach(Array(list(m.data["positions"]).enumerated()),id:\.offset){_,p in VStack(alignment:.leading,spacing:10){HStack{Text(string(p["code"])).font(.headline);Badge(text:string(p["source"]),color:warning);Spacer();Text("\(Int(number(p["qty"]))) units").font(.title3).monospacedDigit()};Text(string(p["name"])).font(.callout).foregroundStyle(muted)
                HStack {Text(string(p["buy_price_label"]));Spacer();Text(p["buy_price"] is NSNumber ? money(p["buy_price"])+" "+string(p["currency"]) : "No valid records").monospacedDigit()}.font(.callout)
                HStack {Text("Last price");Spacer();Text(p["current_price"] is NSNumber ? money(p["current_price"])+" "+string(p["currency"]) : "Awaiting valid quotes").monospacedDigit().foregroundStyle(mint)}.font(.callout)
                Text(string(p["price_status"])+(p["price_time"] is String ? " · "+localTime(p["price_time"]) : "")).font(.caption2).foregroundStyle(muted)
                Text("Current status: "+string(p["position_status"])).font(.caption).foregroundStyle(warning)
                if p["position_updated_at"] is String {Text("Account snapshot: "+localTime(p["position_updated_at"])).font(.caption2).foregroundStyle(muted)}
                if p["swing_stop"] != nil {Text("Holding day \(Int(number(p["holding_sessions"]))) · Exit reference USD "+money(p["swing_stop"])+" / "+money(p["swing_target"])).font(.caption).foregroundStyle(muted)};if p["market_val"] != nil {HStack{Text("Market value "+money(p["market_val"])+" "+string(p["currency"]));Spacer();Text("Position P&L "+money(p["pl_val"])+" "+string(p["currency"])).foregroundStyle(number(p["pl_val"])>=0 ? mint:warning)}.font(.caption)};Divider()}.padding(.vertical,8)}}}
        }
    }
    var returnsPage:some View {
        VStack(spacing:20){Box{HStack{Text("Strategy weekly returns").font(.headline);Spacer();Badge(text:"USD · original dollar values")};Text("Strategy ledger only; excludes existing live positions and transport tests. Includes fees, excludes budget transfers, taxes and dividends.").font(.caption).foregroundStyle(muted)
            if list(m.data["marks"]).isEmpty {Empty(icon:"chart.xyaxis.line",title:"No strategy return records",detail:"No recorded strategy performance is available yet. Equity and weekly returns appear after strategy simulation produces valuations.") } else {Chart {ForEach(Array(list(m.data["marks"]).enumerated()),id:\.offset){i,r in LineMark(x:.value("Record",i),y:.value("Equity USD",number(r["equity"]))).foregroundStyle(mint)}}.frame(height:230)}}
            if !list(m.data["weeks"]).isEmpty {Box{ForEach(Array(list(m.data["weeks"]).enumerated()),id:\.offset){_,r in HStack{Text(string(r["week"]));Spacer();Text(String(format:"%+.2f USD",number(r["pnl"]))).foregroundStyle(number(r["pnl"])>=0 ? mint:warning);Text("Equity "+money(r["equity"])).frame(width:130,alignment:.trailing)}.padding(.vertical,7)}}}
            if !object(m.data["swing_research"]).isEmpty {Box {
                let research=object(m.data["swing_research"])
                Text("Historical daily comparison · separate research").font(.headline)
                Text("Historical comparison uses an initial USD 600; subject to survivorship bias, without historical news replay. Excluded from simulation returns above.").font(.caption).foregroundStyle(muted)
                ForEach(Array(list(research["results"]).enumerated()),id:\.offset){_,r in
                    Text((string(r["mode"]) == "swing" ? "Swing version":"Original strategy")+String(format:" · One-way friction %.2f%% · P&L %+.2f USD · Max drawdown %.2f%%",number(r["friction_per_side"])*100,number(r["ending_equity_usd"])-number(r["initial_usd"]),number(r["max_drawdown"])*100)).font(.caption).foregroundStyle(number(r["ending_equity_usd"])-number(r["initial_usd"])>=0 ? mint:warning)
                }
                Text("The swing version has not established profitability. Historical comparisons cannot replace full strategy simulation acceptance.").font(.caption).foregroundStyle(muted)
            }}
            Box {
                let options=object(m.data["options"])
                let cycle=object(options["cycle"])
                Text("Option quote simulation · separate from stock returns").font(.headline)
                Text(optionsStatus(string(options["status"]))).font(.callout)
                Text("Research: top 20 trend leaders · Symbols completed: \(Int(number(cycle["scanned"]))) · Contracts read: \(Int(number(cycle["contracts"])))").font(.caption).foregroundStyle(muted)
                if let code=options["current_underlying"] as? String {Text("Researching "+code).font(.caption).foregroundStyle(muted)}
                Text("Paper entry/exit orders: \(Int(number(options["trades"]))) · Completed round trips: \(Int(number(options["closed_trades"]))) times").font(.callout)
                if options["pnl_usd"] is NSNumber {Text(String(format:"Research P&L %+.2f USD",number(options["pnl_usd"]))).foregroundStyle(number(options["pnl_usd"])>=0 ? mint:warning)} else {Text("Valuation awaiting valid quotes").foregroundStyle(warning)}
                Text("\(string(options["note"]))").font(.caption).foregroundStyle(muted)
                let reasons=object(cycle["counts"])
                ForEach(reasons.keys.sorted(),id:\.self){key in Text(optionExclusion(key)+"：\(Int(number(reasons[key])))").font(.caption).foregroundStyle(muted)}
                if let warningMessage=options["warning"] as? String {Text(warningMessage).font(.caption).foregroundStyle(warning)}
                ForEach(options["alerts"] as? [String] ?? [],id:\.self){message in Text(message).font(.caption).foregroundStyle(warning)}
                ForEach(Array(list(options["weeks"]).enumerated()),id:\.offset){_,r in Text(string(r["week"])+String(format:" · %+.2f USD",number(r["pnl_usd"]))).font(.caption)}
            }
            Box{Text("Weekly targets are observation metrics").font(.headline);Text("Positions roll continuously; no forced weekly sale or withdrawal. No fixed weekly return is guaranteed. Missing records are not filled with zero returns.").font(.callout).foregroundStyle(muted);Button("Open reports folder"){m.openFolder("data/trading")}}
        }
    }
    var filteredEvents:[[String:Any]] {list(m.data["events"]).filter {e in (newsSource=="All" || string(e["provider"])==newsSource || string(e["feed"])==newsSource) && (query.isEmpty || (string(e["title"])+string(e["source"])).localizedCaseInsensitiveContains(query))}}
    var newsPage:some View {
        VStack(alignment:.leading,spacing:10){
            ClassicSection(title:"News monitor"){
                HStack{TextField("Search headline or source",text:$query);Picker("Source",selection:$newsSource){Text("All").tag("All");ForEach(list(m.data["sources"]).compactMap{$0["name"] as? String},id:\.self){name in Text(sourceName(name)).tag(name)}}.frame(width:200)}.textFieldStyle(.squareBorder)
                HStack {Badge(text:m.running("news") ? "Collector running":"Collector offline",color:m.running("news") ? mint:muted);Text("Last snapshot: "+localTime(m.data["news_updated"])).foregroundStyle(muted);Spacer();Text("\(filteredEvents.count) records")}.font(.system(size:11))
            }
            ClassicSection(title:"Headlines — original publication times"){
                Table(records(filteredEvents)) {
                    TableColumn("Source"){r in Text(string(r.values["source"]))}.width(145)
                    TableColumn("Headline"){r in Text(string(r.values["title"])).help(string(r.values["title"]))}
                    TableColumn("Published"){r in Text(localTime(r.values["published_at"]))}.width(115)
                    TableColumn("Rule state"){r in Text(actionName(string(r.values["action"]))) }.width(120)
                    TableColumn("Link"){r in
                        if let raw=r.values["url"] as? String,let url=URL(string:raw),url.scheme=="https" {Link("Source",destination:url)} else {Text("—")}
                    }.width(50)
                }.tableStyle(.bordered(alternatesRowBackgrounds:true)).frame(height:280)
                Text("Historical / unverified records are retained. A headline is not an executable order signal.").font(.system(size:11)).foregroundStyle(muted)
            }
            ClassicSection(title:"Feed health"){
                Table(records(list(m.data["sources"]))) {
                    TableColumn("Feed"){r in Text(sourceName(string(r.values["name"]))) }
                    TableColumn("Status"){r in
                        let date=dateValue(string(r.values["checked_at"]))
                        let fresh=date.map {Date().timeIntervalSince($0)>=0 && Date().timeIntervalSince($0)<=number(m.data["news_max_age_seconds"] ?? 300)} ?? false
                        Text(string(r.values["status"])=="disabled_by_configuration" ? "Disabled":(!fresh ? "Expired":(yes(r.values["ok"]) ? "Healthy":"Review pending")))
                    }.width(110)
                    TableColumn("Checked"){r in Text(localTime(r.values["checked_at"]))}.width(130)
                }.tableStyle(.bordered(alternatesRowBackgrounds:true)).frame(height:135)
            }
            DisclosureGroup("Full news analysis and source details"){newsDetailsPage.padding(.top,8)}
        }
    }
    var newsDetailsPage:some View {
        VStack(spacing:20){Box{HStack{Text("External news monitoring").font(.headline);Spacer();Badge(text:m.running("news") ? "Monitoring automatically":"Connecting automatically",color:m.running("news") ? mint:warning)};Text("Collection and rule analysis run every 2 minutes after startup; source limits defer retries. Pausing trading does not pause news.").font(.caption).foregroundStyle(muted);Text("Next collection "+localTime(object(m.data["news_schedule"])["next_run_at"])).font(.caption).foregroundStyle(muted);Text("Snapshot "+localTime(m.data["news_updated"])+" · Publication time, source verification and market impact assessed separately").font(.caption).foregroundStyle(muted)
            LazyVGrid(columns:[GridItem(.adaptive(minimum:180))],alignment:.leading,spacing:10){ForEach(Array(list(m.data["sources"]).enumerated()),id:\.offset){_,s in let date=dateValue(string(s["checked_at"]));let fresh=date.map {Date().timeIntervalSince($0)>=0 && Date().timeIntervalSince($0)<=number(m.data["news_max_age_seconds"] ?? 300)} ?? false;HStack{Circle().fill(yes(s["ok"]) && fresh ? mint:warning).frame(width:6,height:6);Text(sourceName(string(s["name"]))).font(.caption);Spacer();Text(string(s["status"])=="disabled_by_configuration" ? "Disabled":(!fresh ? "Expired":(yes(s["ok"]) ? "Healthy":"Review pending"))).font(.caption2).foregroundStyle(muted)}.padding(10).background(navy).border(rule)}}
            HStack{TextField("Search headline or source",text:$query).textFieldStyle(.squareBorder);Picker("Source",selection:$newsSource){Text("All").tag("All");ForEach(list(m.data["sources"]).compactMap{$0["name"] as? String},id:\.self){name in Text(sourceName(name)).tag(name)}}.frame(width:220)}}
            Box {
                Text("News opportunities · separate simulation budgets").font(.headline)
                Text("Verified positive news can link eligible US/HK symbols outside the watchlist. History, strategy, quotes and account risk checks still apply. First simulation buy requires valid news evidence; USD 390 reserve remains untouched. News integration does not establish execution acceptance.").font(.caption).foregroundStyle(muted)
            }
            if filteredEvents.isEmpty {Box{Empty(icon:"newspaper",title:"No matching news",detail:"News updates in the background. Review source health or change filters.")}} else {ForEach(Array(filteredEvents.enumerated()),id:\.offset){_,e in Box{HStack{Text(string(e["source"])).font(.caption.weight(.medium)).foregroundStyle(mint);Spacer();Badge(text:actionName(string(e["action"])),color:string(e["action"])=="block" ? warning:muted)};Text(string(e["title"])).font(.system(size:16,weight:.medium)).textSelection(.enabled);HStack{Text("Published "+localTime(e["published_at"]));Spacer();Text("Collected "+localTime(e["observed_at"]))}.font(.caption2).foregroundStyle(muted);let analysis=object(e["analysis"]);ForEach(analysis["hypotheses"] as? [String] ?? [],id:\.self){x in Text(x).font(.callout).foregroundStyle(muted)};Text(string(e["reason"])).font(.caption).foregroundStyle(muted);if let raw=e["url"] as? String,let u=URL(string:raw),u.scheme=="https" {Link("Open source ↗",destination:u).font(.caption).foregroundStyle(mint)}}}
        }
    }
    }
    func liveButton(_ title:String,_ action:String)->some View {
        Button(title){m.liveAction(["action":action])}.buttonStyle(.bordered)
            .disabled(m.readOnly || !m.connected || m.busy)
    }
    var liveMarketRows:[DesktopRecord] {records(list(object(m.data["live"])["markets"]))}
    var liveOrderRows:[DesktopRecord] {
        records(list(object(m.data["live"])["markets"]).flatMap {market in list(market["orders"]).map {order in var row=order;row["market"]=market["market"];row["currency"]=market["currency"];return row}})
    }
    var livePositionRows:[DesktopRecord] {
        records(list(object(m.data["live"])["markets"]).flatMap {market in list(market["positions"]).map {position in var row=position;row["market"]=market["market"];return row}})
    }
    var livePage:some View {
        let live=object(m.data["live"])
        return HStack(alignment:.top,spacing:10){
            VStack(alignment:.leading,spacing:10){
                ClassicSection(title:"Account / execution status"){
                    HStack {Text("Execution:");Text(liveStatus(live)).fontWeight(.semibold);Spacer();Text(m.running("live_runner") ? "Worker: running":"Worker: offline").foregroundStyle(muted)}
                    if yes(live["loss_latched"]) {Text("Loss limit latched. New buys blocked.").foregroundStyle(warning)}
                    Table(liveMarketRows) {
                        TableColumn("Market"){r in Text(string(r.values["market"]))}.width(60)
                        TableColumn("Budget"){r in Text(optionalMoney(r.values["budget_native"])).monospacedDigit()}.width(min:65,ideal:90)
                        TableColumn("CCY"){r in Text(string(r.values["currency"]))}.width(45)
                        TableColumn("Pending"){r in Text(String(Int(number(r.values["pending"]))))}.width(60)
                        TableColumn("Alerts"){r in Text(String(Int(number(r.values["alerts"]))))}.width(50)
                    }.tableStyle(.bordered(alternatesRowBackgrounds:true)).frame(height:100)
                    if liveMarketRows.isEmpty {Text("No bound REAL account records. Configure and verify accounts before enabling.").font(.system(size:11)).foregroundStyle(muted)}
                    HStack {liveButton("New config","template");Button("Edit config"){m.openFolder("data/trading/live/config.json")};liveButton("HK FX","refresh_fx");Spacer()}
                    HStack {liveButton("Protection checks","validate");liveButton("Verify & bind","prepare");Spacer()}
                }
                ClassicSection(title:"Strategy-owned live positions"){
                    Table(livePositionRows) {
                        TableColumn("Market"){r in Text(string(r.values["market"]))}.width(60)
                        TableColumn("Symbol"){r in Text(string(r.values["code"]))}
                        TableColumn("Quantity"){r in Text(optionalMoney(r.values["qty"])).monospacedDigit()}.width(95)
                    }.tableStyle(.bordered(alternatesRowBackgrounds:true)).frame(height:90)
                    if livePositionRows.isEmpty {Text(liveMarketRows.isEmpty ? "Positions not loaded; account is not bound.":"No strategy-owned positions in the local record. Verify reconciliation before trading.").font(.system(size:11)).foregroundStyle(muted)}
                }
                ClassicSection(title:"Order records — latest 10 per market"){
                    Table(liveOrderRows) {
                        TableColumn("Side"){r in Text(string(r.values["side"]))}.width(38)
                        TableColumn("Symbol"){r in Text(string(r.values["code"]))}.width(min:85,ideal:105)
                        TableColumn("Filled / Qty"){r in Text(optionalMoney(r.values["filled"])+" / "+optionalMoney(r.values["qty"]))}.width(min:85,ideal:95)
                        TableColumn("Limit / CCY"){r in Text(optionalMoney(r.values["limit"])+" "+string(r.values["currency"]))}.width(min:80,ideal:100)
                        TableColumn("Status"){r in Text(string(r.values["status"])+(yes(r.values["cancel_requested"]) ? " · CANCEL REQUESTED":"")+(yes(r.values["fee_final"]) ? "":" · FEES PENDING"))}
                    }.tableStyle(.bordered(alternatesRowBackgrounds:true)).frame(height:120)
                    Text("Timeout / unknown result: inspect and reconcile before retrying.").font(.system(size:11)).foregroundStyle(warning)
                }
            }.frame(maxWidth:.infinity)
            VStack(alignment:.leading,spacing:10){
                ClassicSection(title:"Order entry — REAL cash stocks"){
                    HStack {Text("Market").frame(width:72,alignment:.leading);Picker("",selection:$liveMarket){Text("US").tag("US");Text("HK").tag("HK")}.labelsHidden();Spacer()}
                    Picker("Side",selection:$liveSide){Text("Buy").tag("BUY");Text("Sell").tag("SELL")}.pickerStyle(.segmented)
                    HStack {Text("Symbol").frame(width:72,alignment:.leading);TextField("US.AAPL / HK.00700",text:$liveCode)}
                    HStack {Text("Quantity").frame(width:72,alignment:.leading);TextField("Whole shares / lots",text:$liveQuantity)}
                    HStack {Text("Limit price").frame(width:72,alignment:.leading);TextField(liveMarket=="US" ? "USD":"HKD",text:$liveLimit)}
                    Divider()
                    Text("Confirmation").foregroundStyle(muted)
                    TextField("Type PLACE LIVE ORDER",text:$orderConfirmation)
                    Button("Submit REAL limit order") {
                        m.liveAction(["action":"order","market":liveMarket,"side":liveSide,"code":liveCode.trimmingCharacters(in:.whitespacesAndNewlines),"qty":liveQuantity,"limit":liveLimit,"confirmation":orderConfirmation,"request_id":UUID().uuidString.replacingOccurrences(of:"-",with:"").lowercased()]);orderConfirmation=""
                    }.buttonStyle(.bordered).disabled(m.readOnly || !m.connected || m.busy || orderConfirmation != "PLACE LIVE ORDER")
                    Text("Fixed limit · cancellation due in 60 s").font(.system(size:10)).foregroundStyle(muted)
                    Text("Requires an enabled portfolio, running worker and all admission checks. Manual holdings cannot be sold here.").font(.system(size:10)).foregroundStyle(muted)
                }
                ClassicSection(title:"Execution control"){
                    TextField("Type ENABLE LIVE STOCKS",text:$liveConfirmation)
                    Button("Enable live stocks") {m.liveAction(["action":"enable","confirmation":liveConfirmation]);liveConfirmation=""}
                        .disabled(m.readOnly || !m.connected || m.busy || liveConfirmation != "ENABLE LIVE STOCKS")
                    HStack {liveButton("Start worker","start");liveButton("Reconcile once","maintain")}
                    HStack {
                        Button("Pause live buys"){m.liveAction(["action":"pause"])}
                        Button("Latch live stop"){m.liveAction(["action":"stop"])}
                    }.buttonStyle(.bordered).disabled(m.readOnly || !m.connected)
                    Text("Pause retains exits. Stop blocks new orders, including automatic exits. Holdings may remain.").font(.system(size:10)).foregroundStyle(warning)
                }
                ClassicSection(title:"Last operation"){
                    let report=object(live["report"])
                    Text(string(report["status"])).fontWeight(.semibold)
                    Text(string(report["message"])).font(.system(size:11))
                    Text(localTime(report["checked_at"])).font(.system(size:10)).foregroundStyle(muted)
                    Button("Help / trading rules"){showHelp=true}
                }
            }.frame(width:286).textFieldStyle(.squareBorder)
        }.font(.system(size:11)).controlSize(.small)
    }

    func sourceName(_ s:String)->String { ["fed_policy":"Federal Reserve · policy","fed_speech":"Federal Reserve · speeches","whitehouse":"White House","bbc_business":"BBC Business","reuters_index":"Reuters · public index","x":"X" ][s] ?? s }
    func actionName(_ s:String)->String { ["observe":"Unverified / watch","reduce":"Reduce new-entry allocation","block":"Buy cooldown rules"][s] ?? "Watch" }
}

@main struct QuantDesktop:App {
    var body:some Scene {
        WindowGroup("AegisQuant — Securities Terminal") {MainView()}
            .defaultSize(width:1300,height:820)
            .commands {
                CommandMenu("Workspace") {
                    ForEach(Page.allCases,id:\.self){page in
                        Button(page.rawValue){NotificationCenter.default.post(name:Notification.Name("AegisNavigate"),object:page.rawValue)}
                    }
                    Divider()
                    Button("Refresh records"){NotificationCenter.default.post(name:Notification.Name("AegisRefresh"),object:nil)}.keyboardShortcut("r",modifiers:.command)
                }
            }
    }
}
