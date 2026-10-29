import SwiftUI
import Charts
import AppKit

let navy = Color(red:0.035,green:0.065,blue:0.105)
let panel = Color(red:0.075,green:0.115,blue:0.17)
let mint = Color(red:0.38,green:0.88,blue:0.72)
let muted = Color(red:0.60,green:0.67,blue:0.76)
func string(_ x: Any?) -> String { x as? String ?? "—" }
func number(_ x: Any?) -> Double { (x as? NSNumber)?.doubleValue ?? 0 }
func list(_ x: Any?) -> [[String:Any]] { x as? [[String:Any]] ?? [] }
func object(_ x: Any?) -> [String:Any] { x as? [String:Any] ?? [:] }
func yes(_ x: Any?) -> Bool { x as? Bool ?? false }
func money(_ x: Any?) -> String { String(format:"%.2f",number(x)) }
func dateValue(_ text: String) -> Date? {
    let f=ISO8601DateFormatter(); f.formatOptions=[.withInternetDateTime,.withFractionalSeconds]
    return f.date(from:text) ?? ISO8601DateFormatter().date(from:text)
}
func localTime(_ x: Any?) -> String {
    guard let d=dateValue(string(x)) else { return "暂无记录" }
    let f=DateFormatter();f.dateFormat="MM/dd HH:mm:ss";return f.string(from:d)
}

enum Page: String, CaseIterable {
    case start="启动", pause="暂停", positions="持仓", returns="收益", news="新闻"
    var icon: String { switch self {case .start:return "square.grid.2x2";case .pause:return "pause.circle";case .positions:return "tray.full";case .returns:return "chart.xyaxis.line";case .news:return "newspaper"} }
    var subtitle: String { switch self {case .start:return "从准备到模拟，每一步都有状态。";case .pause:return "暂停新开仓，保留订单核对与退出能力。";case .positions:return "分清策略模拟与已有实盘持仓。";case .returns:return "按周观察，按实际记录计算。";case .news:return "外部消息、来源与判断依据，集中查看。"} }
}

@MainActor final class Model: ObservableObject {
    @Published var data:[String:Any]=[:]
    @Published var message="正在连接本地服务…"
    @Published var error=""
    @Published var busy=false
    @Published var connected=false
    var fetching=false
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
        let p=Process();p.executableURL=root.appendingPathComponent(".venv/bin/python")
        p.arguments=["-m","trading.desktop_server"];p.currentDirectoryURL=root
        let dir=root.appendingPathComponent("data/desktop");try? FileManager.default.createDirectory(at:dir,withIntermediateDirectories:true)
        let log=dir.appendingPathComponent("server.log")
        if !FileManager.default.fileExists(atPath:log.path) { FileManager.default.createFile(atPath:log.path,contents:nil) }
        if let h=try? FileHandle(forWritingTo:log) { _ = try? h.seekToEnd();p.standardOutput=h;p.standardError=h }
        do {try p.run()} catch {self.error="本地服务启动失败：\(error.localizedDescription)"}
    }
    func request(_ path:String,action:String?=nil,completion:@escaping([String:Any]?,String?)->Void) {
        let session=root.appendingPathComponent("data/desktop/session.json")
        guard let bytes=try? Data(contentsOf:session),let info=(try? JSONSerialization.jsonObject(with:bytes)) as? [String:Any],let port=info["port"] as? Int,let token=info["token"] as? String,let url=URL(string:"http://127.0.0.1:\(port)/\(path)") else {completion(nil,"本地服务尚未就绪");return}
        var r=URLRequest(url:url);r.timeoutInterval=12;r.setValue(token,forHTTPHeaderField:"X-Local-Token")
        if let action=action {r.httpMethod="POST";r.httpBody=try? JSONSerialization.data(withJSONObject:["action":action]);r.setValue("application/json",forHTTPHeaderField:"Content-Type")}
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
            if self.message=="正在连接本地服务…" {self.message="本地服务已连接"}
        }
    }
    func action(_ name:String) {
        busy=true
        request("action",action:name) { [weak self] obj,problem in
            guard let self=self else {return};self.busy=false
            self.message=problem ?? string(obj?["message"]);self.refresh()
        }
    }
    func running(_ name:String)->Bool {yes(object(data["running"])[name])}
    func openFolder(_ path:String) {NSWorkspace.shared.open(root.appendingPathComponent(path))}
}

struct Badge: View {
    var text:String;var color:Color=mint
    var body:some View {Text(text).font(.system(size:11,weight:.semibold)).foregroundStyle(color).padding(.horizontal,10).padding(.vertical,5).background(color.opacity(0.10),in:Capsule())}
}
struct Box<Content:View>:View {
    @ViewBuilder var content:Content
    var body:some View {VStack(alignment:.leading,spacing:16){content}.padding(22).frame(maxWidth:.infinity,alignment:.leading).background(panel,in:RoundedRectangle(cornerRadius:16)).overlay(RoundedRectangle(cornerRadius:16).stroke(.white.opacity(0.055)))}
}
struct Metric:View {
    var title:String;var value:String;var note:String
    var body:some View {Box {Text(title).font(.system(size:12)).foregroundStyle(muted);Text(value).font(.system(size:29,weight:.semibold,design:.rounded)).monospacedDigit();Text(note).font(.system(size:11)).foregroundStyle(muted)}}
}
struct Empty:View {
    var icon:String;var title:String;var detail:String
    var body:some View {VStack(spacing:14){Image(systemName:icon).font(.system(size:32,weight:.light)).foregroundStyle(mint);Text(title).font(.title3.weight(.medium));Text(detail).font(.callout).foregroundStyle(muted).multilineTextAlignment(.center).frame(maxWidth:460)}.frame(maxWidth:.infinity).padding(.vertical,48)}
}

struct MainView: View {
    @StateObject var m=Model()
    @State var page:Page = .start
    @State var query=""
    @State var newsSource="全部"
    @State var pauseConfirm=false
    var scan:[String:Any] {object(m.data["scan"])}
    var probe:[String:Any] {object(m.data["probe"])}
    var body: some View {
        HStack(spacing:0){
            VStack(alignment:.leading,spacing:28){
                HStack(spacing:11){Image(systemName:"waveform.path").font(.title).foregroundStyle(mint);VStack(alignment:.leading,spacing:3){Text("量化控制台").font(.system(size:17,weight:.bold));Text("LOCAL WORKSPACE").font(.system(size:8,weight:.medium)).tracking(2).foregroundStyle(muted)}}.padding(.top,22)
                VStack(spacing:8){ForEach(Page.allCases,id:\.self){p in Button {page=p} label:{HStack(spacing:12){Image(systemName:p.icon).frame(width:20);Text(p.rawValue).font(.system(size:14,weight:.medium));Spacer();if page==p {Circle().fill(mint).frame(width:5,height:5)}}.padding(.horizontal,15).padding(.vertical,13).foregroundStyle(page==p ? mint:muted).background(page==p ? mint.opacity(0.09):.clear,in:RoundedRectangle(cornerRadius:10))}.buttonStyle(.plain)}}
                Spacer()
                VStack(alignment:.leading,spacing:12){Badge(text:"实盘未开启",color:.orange);Text(yes(object(m.data["funding"])["shared_capital"]) ? "股票／期权共享资金":"资金计划").font(.caption).foregroundStyle(muted);Text(money(m.data["simulation_total_budget_usd"] ?? m.data["budget_usd"])+" USD 模拟预算").font(.system(size:13,weight:.medium));Text("保留备用金 "+money(object(m.data["funding"])["reserve_usd"])+" USD").font(.caption).foregroundStyle(muted);Divider();HStack{Circle().fill(m.connected ? mint:.orange).frame(width:6,height:6);Text(m.connected ? "本地服务已连接":"本地服务连接中").font(.caption2).foregroundStyle(muted)}}.padding(.bottom,20)
            }.padding(.horizontal,20).frame(width:210).background(Color.black.opacity(0.20))
            VStack(spacing:0){
                HStack{VStack(alignment:.leading,spacing:7){Text(page.rawValue).font(.system(size:29,weight:.semibold));Text(page.subtitle).font(.system(size:13)).foregroundStyle(muted)};Spacer();VStack(alignment:.trailing,spacing:8){Button {m.refresh()} label:{Label("刷新",systemImage:"arrow.clockwise")}.buttonStyle(.bordered);Text("更新 "+localTime(m.data["updated_at"])).font(.caption2).foregroundStyle(muted)}}.padding(30)
                ScrollView {VStack(alignment:.leading,spacing:20){
                    if !m.error.isEmpty {Box{Label(m.error,systemImage:"exclamationmark.triangle").foregroundStyle(.orange);Button("重连本地服务"){m.startBackend();m.refresh()}}}
                    switch page {case .start:startPage;case .pause:pausePage;case .positions:positionsPage;case .returns:returnsPage;case .news:newsPage}
                }.padding(.horizontal,30).padding(.bottom,30)}
                HStack{Image(systemName:"info.circle").foregroundStyle(mint);Text(m.message).font(.caption).lineLimit(2);Spacer();Button("打开项目"){m.openFolder("")}.buttonStyle(.plain).foregroundStyle(muted)}.padding(.horizontal,30).padding(.vertical,12).background(.black.opacity(0.14))
            }
        }.background(navy).foregroundStyle(.white.opacity(0.92)).frame(minWidth:1060,minHeight:740).preferredColorScheme(.dark)
    }
    func actionButton(_ title:String,_ action:String,primary:Bool=false)->some View {
        Button {m.action(action)} label:{Text(title).font(.system(size:13,weight:.semibold)).padding(.horizontal,16).padding(.vertical,11).background(primary ? mint:Color.white.opacity(0.07),in:RoundedRectangle(cornerRadius:9)).foregroundStyle(primary ? navy:.white)}.buttonStyle(.plain).disabled(m.busy || !m.connected)
    }
    var startPage:some View {
        VStack(spacing:20){
            HStack(spacing:14){Metric(title:"当前模拟预算",value:money(m.data["simulation_total_budget_usd"] ?? m.data["budget_usd"]),note:yes(m.data["independent_budget_display"]) ? "US股票／期权／HK股票各500 USD" : "USD · 以账本模式为准");Metric(title:"有效日线标的",value:"\(Int(number(scan["history_count"])))",note:"至少 60 根已完成日线");Metric(title:"当前候选",value:"\(Int(number(scan["candidate_count"] ?? list(m.data["candidates"]).count)))",note:"候选不是买入指令")}
            Box {
                HStack{VStack(alignment:.leading,spacing:7){Text("先准备，再模拟").font(.title2.weight(.semibold));Text("行情扫描、外部新闻与模拟验收分别运行。此控制台不提供实盘启用。") .font(.callout).foregroundStyle(muted)};Spacer();Image(systemName:"arrow.up.right.circle").font(.system(size:42,weight:.ultraLight)).foregroundStyle(mint)}
                HStack(spacing:12){actionButton(m.running("scan") ? "扫描进行中":"手动补扫（可选）","prepare",primary:true);actionButton(string(probe["status"]) == "transport_passed" ? "接口验收已通过":m.running("probe") ? "模拟验收运行中":"启动 / 恢复模拟验收","probe");Spacer()}
                HStack(spacing:8){Badge(text:scanActivityLabel,color:m.running("automation") || m.running("scan") ? mint:muted);Badge(text:m.running("news") ? "新闻监测中":"新闻未运行",color:m.running("news") ? mint:muted);Badge(text:m.running("strategy") ? "策略模拟运行中":m.running("probe") ? "接口测试运行中":string(probe["status"]) == "transport_passed" ? "接口验收已通过":"接口测试未运行",color:(m.running("strategy") || m.running("probe")) ? mint:muted)}
                if m.running("automation") {
                    Text("自动扫描已启用 · 每个交易日收盘后 5 分钟运行，无需点击。下次："+localTime(object(m.data["scan_schedule"])["next_run_at"])).font(.caption).foregroundStyle(muted)
                } else {Text("自动调度当前未运行，正在检查服务状态。").font(.caption).foregroundStyle(.orange)}
            }
            HStack(alignment:.top,spacing:18){
                Box {Text("行情覆盖").font(.headline);HStack{Text("已评估标的").foregroundStyle(muted);Spacer();Text("\(Int(number(scan["processed_count"] ?? scan["history_count"]))) / \(Int(number(scan["eligible_count"])))").monospacedDigit()};ProgressView(value:min(number(scan["processed_count"] ?? scan["history_count"])/max(number(scan["eligible_count"]),1),1)).tint(mint);Text("有效日线 \(Int(number(scan["history_count"]))) · 已排除 \(Int(number(scan["excluded_count"]))) · 待处理 \(Int(number(scan["pending_count"])))").font(.caption).foregroundStyle(muted);Text(yes(scan["complete"]) ? "全市场评估完成；低价、停牌和数据不足等排除原因已记录。":"正在补齐已完成日线；未完成的扫描不会用于新开仓。") .font(.caption).foregroundStyle(muted);Text("信号日期 "+string(scan["date"])).font(.caption).foregroundStyle(muted)}
                Box {Text("模拟验收").font(.headline);Badge(text:probeStatus,color:yes(m.data["probe_paused"]) || ["waiting_quote","quote_expired","waiting_spread"].contains(string(probe["status"])) ? .orange:mint);Text("撤单 → 买入一份 → 卖出 → 对账").font(.callout);Text("仅 SIMULATE 账户。接口验收不等于策略盈利验证，也不会自动开启实盘。").font(.caption).foregroundStyle(muted);if let wait=probe["quote_wait"] as? [String:Any] {Text("报价等待 \(Int(number(wait["elapsed_seconds"]))) / 120 秒 · 每 5 秒检查，期间继续撤单与对账").font(.caption).foregroundStyle(muted)};Text("记录 "+localTime(probe["updated_at"])).font(.caption).foregroundStyle(muted)}
            }
            Box {
                let schedule=object(m.data["scan_schedule"])
                let automation=object(m.data["automation"])
                let strategy=object(m.data["strategy"])
                Text("每日扫描与策略模拟").font(.headline)
                Text("目标交易日 "+string(schedule["target_date"])+" · 下次收盘扫描 "+localTime(schedule["next_run_at"])).font(.caption).foregroundStyle(muted)
                Text("扫描状态："+scheduleStatus(string(schedule["status"]))).font(.callout)
                if let retry=schedule["retry_at"] as? String {Text("重试时间 "+localTime(retry)).font(.caption).foregroundStyle(.orange)}
                if let reason=schedule["reason"] as? String {Text(reason).font(.caption).foregroundStyle(.orange).textSelection(.enabled)}
                Text("模拟衔接："+handoffStatus(string(automation["handoff"]))).font(.callout)
                if let reason=automation["reason"] as? String {Text(reason).font(.caption).foregroundStyle(.orange)}
                if let reason=automation["scan_error"] as? String {Text(reason).font(.caption).foregroundStyle(.orange)}
                Text("策略状态："+strategyStatus(string(strategy["status"]))).font(.callout)
                Text("重大利空提前退出：核验原始来源后触发，不必等到累计亏损阈值；新闻每 2 分钟采集。").font(.caption).foregroundStyle(muted)
                if string(strategy["strategy_version"]) == "swing_1_5_v1" {
                    Text("短线模拟 · 1–5 个交易日 · 允许隔夜").font(.headline).foregroundStyle(mint)
                    Text("5 日突破入场；1.5 ATR 止损、3 ATR 止盈，第五个交易日收盘前 15 分钟尝试退出。单笔计划风险上限 \(money(m.data["single_risk_usd"])) USD，包含费用预留；跳空可能超出上限。").font(.caption).foregroundStyle(muted)
                    Text("止损由本机监测，需要后台、网络及 OpenD 正常；退出仍受报价、限价成交和撤单结果约束。").font(.caption).foregroundStyle(muted)
                }
                let temporary=object(strategy["temporary_entry_window"])
                if string(strategy["entry_timing"]) == "regular_session" {Text("模拟入场：开盘 5 分钟后至正常收盘，条件满足时检查；同日防重复").font(.caption).foregroundStyle(mint)} else if let expiry=dateValue(string(temporary["expires_at"])),expiry>Date() {Text("临时模拟入场：正常交易时段内有效，截止 "+localTime(temporary["expires_at"])).font(.caption).foregroundStyle(mint)} else if let day=strategy["entry_after_session"] as? String {Text("默认入场 09:35–09:40（纽约）；首次新买入须晚于 "+day).font(.caption).foregroundStyle(muted)}
                let urgent=object(strategy["emergency_exits"])
                if !urgent.isEmpty {Text("重大利空退出待处理："+urgent.keys.sorted().joined(separator:", ")).font(.caption).foregroundStyle(.orange)}
                if yes(strategy["candidate_fallback_enabled"]) {Text("个股顺位替补已开启 · 每日最多检查前 10 只 · 最多持有 2 只").font(.caption).foregroundStyle(muted)}
                if let block=strategy["entry_global_block"] as? [String:Any] {Text("全局新闻阻断："+(block["reasons"] as? [String] ?? []).joined(separator:"；")).font(.caption).foregroundStyle(.orange)}
                if let reason=strategy["data_block"] as? String {Text(reason).font(.caption).foregroundStyle(.orange)}
                if let reason=strategy["reason"] as? String {Text(reason).font(.caption).foregroundStyle(.orange)}
                if let wait=strategy["quote_wait"] as? [String:Any] {Text("等待 "+string(wait["code"])+" 有效报价：\(Int(number(wait["elapsed_seconds"]))) / 120 秒").font(.caption).foregroundStyle(.orange)}
                Text("接口通过后自动核对模拟账户，进入策略模拟；实盘始终关闭。").font(.caption).foregroundStyle(muted)
            }
            Box {
                let options=object(m.data["options"])
                Text("期权 · 独立纸面模拟").font(.headline)
                Text(optionsStatus(string(options["status"]))).font(.callout)
                Text("真实报价记录，假设按买卖盘成交；不是券商模拟成交验收。研究趋势排名前 20 个合格标的，当前额度 \(money(options["budget_usd"])) USD，仅作研究参考，券商执行预算为 0；实际期权模拟使用独立账本。").font(.caption).foregroundStyle(muted)
                if let error=options["error"] as? String {Text(error).font(.caption).foregroundStyle(.orange)}
                if let retry=options["retry_at"] as? String {Text("下次重试 "+localTime(retry)).font(.caption).foregroundStyle(muted)}
            }
            Box {
                let transport=object(m.data["option_transport"])
                Text("期权 · 券商模拟接口验收").font(.headline)
                Text(optionTransportStatus(string(transport["status"]))).font(.callout)
                Text("阶段："+string(transport["phase"])+" · 券商订单 \(list(transport["orders"]).count) 笔").font(.caption)
                if let code=transport["code"] as? String {Text(code).font(.caption)}
                if let reason=transport["reason"] as? String {Text(reason).font(.caption).foregroundStyle(.orange)}
                ForEach(transport["alerts"] as? [String] ?? [],id:\.self){message in Text(message).font(.caption).foregroundStyle(.orange)}
                Text("独立记录撤单、买入成交、卖出成交及对账。通过仅证明接口流程，不代表策略通过或已开启实盘。").font(.caption).foregroundStyle(muted)
            }
            let allAlertRows=m.data["alert_records"] as? [[String:Any]] ?? []
            let alertRows=allAlertRows.filter { ($0["resolved"] as? Bool) != true }
            let resolvedRows=allAlertRows.filter { ($0["resolved"] as? Bool) == true }
            let taskErrors=m.data["errors"] as? [String] ?? []
            if !alertRows.isEmpty || !taskErrors.isEmpty {Box {
                Label("需要关注 · 告警记录",systemImage:"exclamationmark.triangle").foregroundStyle(.orange)
                Text("仅显示尚未确认恢复的问题；时间为本地时间。").font(.caption2).foregroundStyle(muted)
                ForEach(Array(alertRows.enumerated()),id:\.offset){_,row in
                    VStack(alignment:.leading,spacing:5) {
                        HStack {Text(string(row["message"])).font(.callout);Spacer();Text(string(row["source"])).font(.caption2).foregroundStyle(muted)}
                        if row["last_seen"] is String {
                            Text("首次 "+localTime(row["first_seen"])+" · 最近 "+localTime(row["last_seen"])+" · 记录 \(Int(number(row["count"]))) 次").font(.caption).foregroundStyle(muted)
                        } else {Text("发生时间未记录").font(.caption).foregroundStyle(muted)}
                        DisclosureGroup("查看原始记录") {ForEach(row["details"] as? [String] ?? [],id:\.self){Text($0).font(.caption2).foregroundStyle(muted).textSelection(.enabled)}}.font(.caption)
                    }.padding(.vertical,5)
                    Divider().opacity(0.2)
                }
                ForEach(taskErrors,id:\.self){Text($0+" · 发生时间未记录").font(.caption).foregroundStyle(.orange)}
            }}
            if !resolvedRows.isEmpty {Box {
                DisclosureGroup("已解决 · \(resolvedRows.count) 项（查看记录）") {
                    ForEach(Array(resolvedRows.enumerated()),id:\.offset){_,row in
                        Text(string(row["message"])+" · 已解决 · "+localTime(row["resolved_at"])).font(.caption).foregroundStyle(muted).padding(.vertical,3)
                    }
                    Text("仅归档显示已恢复的故障；暂停和风控状态仍单独保留。").font(.caption2).foregroundStyle(muted)
                }
            }}
            Box {HStack{Text("候选观察名单").font(.headline);Spacer();Text("按策略排名 · 最多展示 50 个").font(.caption).foregroundStyle(muted)};if list(m.data["candidates"]).isEmpty {Empty(icon:"list.bullet.rectangle",title:"暂时没有候选",detail:"完成有效日线扫描后显示；没有候选时保持现金。") } else {ForEach(Array(list(m.data["candidates"]).prefix(6).enumerated()),id:\.offset){i,c in HStack{Text(String(format:"%02d",i+1)).foregroundStyle(muted).frame(width:28);Text(string(c["code"])).font(.system(.body,design:.monospaced));Spacer();Text(String(format:"20 日涨幅 %+.2f%%",number(c["strength"])*100)).foregroundStyle(mint)}.padding(.vertical,5)}}}
        }
    }
    var scanActivityLabel:String {
        if m.running("scan") {return "全市场扫描中"}
        if !m.running("automation") {return "自动调度未运行"}
        if object(m.data["automation"])["scan_error"] is String {return "扫描调度异常 · 查看详情"}
        switch string(object(m.data["scan_schedule"])["status"]) {
        case "complete":return "本轮已完成 · 等待定时扫描"
        case "retry_wait":return "扫描等待自动重试"
        case "needs_attention":return "扫描异常 · 需要处理"
        default:return "自动扫描待调度"
        }
    }
    var probeStatus:String {switch string(probe["status"]) {case "waiting_spread":return "等待价差收窄 · 上限 0.2%";case "query_retry":return "只读查询超时 · 自动重试中";case "waiting_regular_session_0935_ET":return "等待正常交易时段";case "transport_passed":return "模拟接口验收通过";case "waiting_quote":return "等待有效报价";case "quote_expired":return "报价过期 · 等待更新";case "quote_wait_timeout":return "报价等待超时 · 已暂停";case "connection_interrupted":return "连接中断 · 已暂停";case "needs_attention":return "已暂停 · 需要检查";case "running":return "模拟验收进行中";default:return "尚未完成验收"}}
    func scheduleStatus(_ value:String)->String {switch value {case "complete":return "本交易日已完成";case "running":return "扫描中";case "retry_wait":return "等待自动重试";case "needs_attention":return "重试耗尽，需要处理";default:return "等待调度"}}
    func optionTransportStatus(_ value:String)->String {switch value {
        case "transport_passed":return "期权模拟接口验收通过"
        case "waiting_qualified_contract":return "等待符合预算与流动性条件的合约"
        case "candidate_validation_wait":return "正在等待候选数据验证"
        case "waiting_regular_session", "waiting_regular_session_0935_ET":return "等待正常交易时段"
        case "needs_attention":return "验收暂停 · 需要检查"
        case "running":return "券商模拟订单验收进行中"
        case "waiting_quote", "quote_expired", "waiting_spread":return "等待有效双边报价"
        case "query_retry":return "只读查询重试中"
        default:return "尚未完成期权券商接口验收"
    }}
    func optionExclusion(_ value:String)->String {[
        "nonstandard_suspended_or_other_direction":"非标准、停牌或方向不符",
        "delta":"Delta 不合格","open_interest":"未平仓量不足","volume":"当日成交量不足",
        "stale_snapshot":"快照过期","missing_invalid_data":"数据缺失或无效",
        "nonstandard_multiplier":"合约乘数不合格","budget_including_roundtrip_reserve":"权利金与双边费用超预算",
        "option_data_unverified":"期权数据未验证","snapshot_book_missing":"双边报价缺失",
        "qualified_before_final_quote_news_budget_checks":"待最终报价、新闻及预算检查",
        "valid_books_last_check":"最近有效买卖盘数量","subscription_capacity":"订阅容量不足"
    ][value] ?? value}
    func optionsStatus(_ value:String)->String {switch value {
        case "waiting_regular_session":return "等待美股正常交易时段"
        case "waiting_daily_scan":return "等待完整日线信号"
        case "scanning":return "自动扫描期权链及真实报价"
        case "round_complete_no_entry":return "本轮研究完成，等待下一轮"
        case "position_monitoring":return "纸面持仓监测中"
        case "connection_or_data_error":return "连接或数据异常，等待重试"
        case "needs_attention":return "重试耗尽，需要检查"
        default:return "准备期权报价研究"
    }}
    func handoffStatus(_ value:String)->String {switch value {case "waiting_probe_exit":return "等待接口测试结束";case "waiting_interface_acceptance":return "等待接口验收通过";case "strategy_running":return "策略模拟运行中";case "checking_account_and_handoff":return "核对账户与账本";case "blocked":return "需要处理";default:return "等待检查"}}
    func strategyStatus(_ value:String)->String {switch value {case "strategy_running":return "策略模拟运行中，尚未验收通过";case "strategy_passed":return "策略模拟验收通过，继续模拟";case "paused":return "暂停新买入，继续核对与退出";case "stopped":return "已停止交易，继续核对撤单";case "needs_attention":return "需要处理";default:return "尚未开始"}}
    var pausePage:some View {
        VStack(spacing:20){Box{HStack{Image(systemName:"pause.circle").font(.system(size:42,weight:.light)).foregroundStyle(.orange);VStack(alignment:.leading,spacing:8){Text("暂停模拟开仓").font(.title2.weight(.semibold));Text("先保存暂停状态，再尝试撤销模拟账户的未完成买单。") .foregroundStyle(muted)}};Text("暂停不代表撤单已经成功，也不会自动清仓。已有真实持仓不受这里的按钮控制。").font(.callout).foregroundStyle(muted);HStack {actionButton("暂停模拟开仓","pause");actionButton("停止模拟交易","stop")};Badge(text:(yes(m.data["probe_paused"]) || yes(m.data["paper_paused"])) ? "模拟账本已暂停":"以最新任务状态为准",color:.orange)};Box{Text("运行边界").font(.headline);Text("• 当前模拟预算 \(money(m.data["simulation_total_budget_usd"] ?? m.data["budget_usd"])) USD；保留备用金 \(money(object(m.data["funding"])["reserve_usd"])) USD\n• 累计策略亏损 \(money(m.data["loss_limit_usd"])) USD 暂停新买入\n• 股票和期权实盘均未开启\n• 退出本界面不会自动停止后台任务\n• 需要保持电脑联网、OpenD 登录") .font(.callout).foregroundStyle(muted).lineSpacing(9);Button("查看运行说明"){m.openFolder("两项准备任务.md")}}}
    }
    var positionsPage:some View {
        VStack(spacing:20){Box{HStack{VStack(alignment:.leading,spacing:6){Text("已有实盘持仓 · 只读").font(.headline);Text("不会将手工持仓归入策略，也不会通过此页面下单。").font(.caption).foregroundStyle(muted)};Spacer();actionButton("刷新账户持仓","account")};Text("账户快照："+localTime(m.data["account_updated"])).font(.caption).foregroundStyle(muted);if let err=m.data["account_error"] as? String {Text(err).foregroundStyle(.orange).font(.caption)}}
            Box{if list(m.data["positions"]).isEmpty {Empty(icon:"tray",title:"暂无已载入持仓",detail:"点击上方刷新获取账户快照。没有快照不代表账户空仓。") } else {ForEach(Array(list(m.data["positions"]).enumerated()),id:\.offset){_,p in VStack(alignment:.leading,spacing:10){HStack{Text(string(p["code"])).font(.headline);Badge(text:string(p["source"]),color:.orange);Spacer();Text("\(Int(number(p["qty"]))) 份").font(.title3).monospacedDigit()};Text(string(p["name"])).font(.callout).foregroundStyle(muted)
                HStack {Text(string(p["buy_price_label"]));Spacer();Text(p["buy_price"] is NSNumber ? money(p["buy_price"])+" "+string(p["currency"]) : "暂无有效记录").monospacedDigit()}.font(.callout)
                HStack {Text("现价");Spacer();Text(p["current_price"] is NSNumber ? money(p["current_price"])+" "+string(p["currency"]) : "等待有效报价").monospacedDigit().foregroundStyle(mint)}.font(.callout)
                Text(string(p["price_status"])+(p["price_time"] is String ? " · "+localTime(p["price_time"]) : "")).font(.caption2).foregroundStyle(muted)
                Text("当前状态："+string(p["position_status"])).font(.caption).foregroundStyle(.orange)
                if p["position_updated_at"] is String {Text("账户快照："+localTime(p["position_updated_at"])).font(.caption2).foregroundStyle(muted)}
                if p["swing_stop"] != nil {Text("持有第 \(Int(number(p["holding_sessions"]))) 个交易日 · 退出参考 USD "+money(p["swing_stop"])+" / "+money(p["swing_target"])).font(.caption).foregroundStyle(muted)};if p["market_val"] != nil {HStack{Text("市值 "+money(p["market_val"])+" "+string(p["currency"]));Spacer();Text("持仓盈亏 "+money(p["pl_val"])+" "+string(p["currency"])).foregroundStyle(number(p["pl_val"])>=0 ? mint:.orange)}.font(.caption)};Divider()}.padding(.vertical,8)}}}
        }
    }
    var returnsPage:some View {
        VStack(spacing:20){Box{HStack{Text("策略周收益").font(.headline);Spacer();Badge(text:"USD · 美元原值")};Text("只计算策略账本，不混入已有实盘持仓或接口测试。包含费用，预算划拨不计作收益，不计算税和分红。").font(.caption).foregroundStyle(muted)
            if list(m.data["marks"]).isEmpty {Empty(icon:"chart.xyaxis.line",title:"尚无策略收益记录",detail:"目前没有可展示的真实策略表现。完成策略模拟并产生估值后，这里会显示权益曲线和周收益。") } else {Chart {ForEach(Array(list(m.data["marks"]).enumerated()),id:\.offset){i,r in LineMark(x:.value("记录",i),y:.value("权益 USD",number(r["equity"]))).foregroundStyle(mint)}}.frame(height:230)}}
            if !list(m.data["weeks"]).isEmpty {Box{ForEach(Array(list(m.data["weeks"]).enumerated()),id:\.offset){_,r in HStack{Text(string(r["week"]));Spacer();Text(String(format:"%+.2f USD",number(r["pnl"]))).foregroundStyle(number(r["pnl"])>=0 ? mint:.orange);Text("权益 "+money(r["equity"])).frame(width:130,alignment:.trailing)}.padding(.vertical,7)}}}
            if !object(m.data["swing_research"]).isEmpty {Box {
                let research=object(m.data["swing_research"])
                Text("历史日线对照 · 独立研究结果").font(.headline)
                Text("原历史对照本金为 600 USD；存在幸存者偏差，未重放历史新闻，不计入上方模拟收益。").font(.caption).foregroundStyle(muted)
                ForEach(Array(list(research["results"]).enumerated()),id:\.offset){_,r in
                    Text((string(r["mode"]) == "swing" ? "短线版":"原策略")+String(format:" · 单边摩擦 %.2f%% · 盈亏 %+.2f USD · 最大回撤 %.2f%%",number(r["friction_per_side"])*100,number(r["ending_equity_usd"])-number(r["initial_usd"]),number(r["max_drawdown"])*100)).font(.caption).foregroundStyle(number(r["ending_equity_usd"])-number(r["initial_usd"])>=0 ? mint:.orange)
                }
                Text("短线版尚未证明盈利；历史对照不能代替完整策略模拟验收。").font(.caption).foregroundStyle(muted)
            }}
            Box {
                let options=object(m.data["options"])
                let cycle=object(options["cycle"])
                Text("期权报价模拟 · 与股票收益分开").font(.headline)
                Text(optionsStatus(string(options["status"]))).font(.callout)
                Text("研究范围：趋势前 20 名 · 已完成 \(Int(number(cycle["scanned"]))) 个标的 · 已读取 \(Int(number(cycle["contracts"]))) 个合约").font(.caption).foregroundStyle(muted)
                if let code=options["current_underlying"] as? String {Text("正在研究 "+code).font(.caption).foregroundStyle(muted)}
                Text("纸面进出场 \(Int(number(options["trades"]))) 笔 · 完整闭环 \(Int(number(options["closed_trades"]))) 次").font(.callout)
                if options["pnl_usd"] is NSNumber {Text(String(format:"研究盈亏 %+.2f USD",number(options["pnl_usd"]))).foregroundStyle(number(options["pnl_usd"])>=0 ? mint:.orange)} else {Text("估值等待有效报价").foregroundStyle(.orange)}
                Text("\(string(options["note"]))").font(.caption).foregroundStyle(muted)
                let reasons=object(cycle["counts"])
                ForEach(reasons.keys.sorted(),id:\.self){key in Text(optionExclusion(key)+"：\(Int(number(reasons[key])))").font(.caption).foregroundStyle(muted)}
                if let warning=options["warning"] as? String {Text(warning).font(.caption).foregroundStyle(.orange)}
                ForEach(options["alerts"] as? [String] ?? [],id:\.self){message in Text(message).font(.caption).foregroundStyle(.orange)}
                ForEach(Array(list(options["weeks"]).enumerated()),id:\.offset){_,r in Text(string(r["week"])+String(format:" · %+.2f USD",number(r["pnl_usd"]))).font(.caption)}
            }
            Box{Text("周目标只是观察指标").font(.headline);Text("持仓连续滚动，不按周强制卖出或提现；没有固定周收益保证。缺失记录不会填成零收益。").font(.callout).foregroundStyle(muted);Button("打开报告文件夹"){m.openFolder("data/trading")}}
        }
    }
    var filteredEvents:[[String:Any]] {list(m.data["events"]).filter {e in (newsSource=="全部" || string(e["provider"])==newsSource || string(e["feed"])==newsSource) && (query.isEmpty || (string(e["title"])+string(e["source"])).localizedCaseInsensitiveContains(query))}}
    var newsPage:some View {
        VStack(spacing:20){Box{HStack{Text("外部新闻监测").font(.headline);Spacer();Badge(text:m.running("news") ? "自动监测中":"自动连接中",color:m.running("news") ? mint:.orange)};Text("启动软件后自动采集与规则分析，每 2 分钟启动一轮；来源限流时自动延后重试。暂停交易不暂停新闻。").font(.caption).foregroundStyle(muted);Text("下次采集 "+localTime(object(m.data["news_schedule"])["next_run_at"])).font(.caption).foregroundStyle(muted);Text("快照 "+localTime(m.data["news_updated"])+" · 原文时间、来源核验与市场影响分别判断").font(.caption).foregroundStyle(muted)
            LazyVGrid(columns:[GridItem(.adaptive(minimum:180))],alignment:.leading,spacing:10){ForEach(Array(list(m.data["sources"]).enumerated()),id:\.offset){_,s in let date=dateValue(string(s["checked_at"]));let fresh=date.map {Date().timeIntervalSince($0)>=0 && Date().timeIntervalSince($0)<=number(m.data["news_max_age_seconds"] ?? 300)} ?? false;HStack{Circle().fill(yes(s["ok"]) && fresh ? mint:Color.orange).frame(width:6,height:6);Text(sourceName(string(s["name"]))).font(.caption);Spacer();Text(string(s["status"])=="disabled_by_configuration" ? "未启用":(!fresh ? "已过期":(yes(s["ok"]) ? "正常":"待检查"))).font(.caption2).foregroundStyle(muted)}.padding(10).background(.white.opacity(0.035),in:RoundedRectangle(cornerRadius:8))}}
            HStack{TextField("搜索标题或来源",text:$query).textFieldStyle(.roundedBorder);Picker("来源",selection:$newsSource){Text("全部").tag("全部");ForEach(list(m.data["sources"]).compactMap{$0["name"] as? String},id:\.self){name in Text(sourceName(name)).tag(name)}}.frame(width:220)}}
            Box {
                Text("新闻机会 · 纳入各自模拟预算").font(.headline)
                Text("已核验利好可关联名单外港美股，仍须通过历史、策略、报价和账户风控。首笔模拟买入要求有效新闻证据；390 USD 备用金不动。新闻接线不代表成交验收通过。").font(.caption).foregroundStyle(muted)
            }
            if filteredEvents.isEmpty {Box{Empty(icon:"newspaper",title:"暂无匹配消息",detail:"新闻在后台自动更新；可查看来源状态或更改筛选条件。")}} else {ForEach(Array(filteredEvents.enumerated()),id:\.offset){_,e in Box{HStack{Text(string(e["source"])).font(.caption.weight(.medium)).foregroundStyle(mint);Spacer();Badge(text:actionName(string(e["action"])),color:string(e["action"])=="block" ? .orange:muted)};Text(string(e["title"])).font(.system(size:16,weight:.medium)).textSelection(.enabled);HStack{Text("发布 "+localTime(e["published_at"]));Spacer();Text("采集 "+localTime(e["observed_at"]))}.font(.caption2).foregroundStyle(muted);let analysis=object(e["analysis"]);ForEach(analysis["hypotheses"] as? [String] ?? [],id:\.self){x in Text(x).font(.callout).foregroundStyle(muted)};Text(string(e["reason"])).font(.caption).foregroundStyle(muted);if let raw=e["url"] as? String,let u=URL(string:raw),u.scheme=="https" {Link("查看来源 ↗",destination:u).font(.caption).foregroundStyle(mint)}}}
        }
    }
    }
    func sourceName(_ s:String)->String { ["fed_policy":"美联储 · 政策","fed_speech":"美联储 · 讲话","whitehouse":"白宫","bbc_business":"BBC 财经","reuters_index":"路透 · 公开索引","x":"X" ][s] ?? s }
    func actionName(_ s:String)->String { ["observe":"待核实 / 观察","reduce":"降低新仓额度","block":"买入冷却规则"][s] ?? "观察" }
}

@main struct QuantDesktop:App {
    var body:some Scene {WindowGroup("量化控制台") {MainView()}.defaultSize(width:1220,height:820).windowStyle(.hiddenTitleBar)}
}
