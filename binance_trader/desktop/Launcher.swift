import AppKit
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var backend: Process?
    var attempts = 0
    let endpoint = URL(string: "http://127.0.0.1:8766")!
    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu(); let item = NSMenuItem(); menu.addItem(item)
        let appMenu = NSMenu(); item.submenu = appMenu
        appMenu.addItem(withTitle: "显示交易器", action: #selector(showWindow), keyEquivalent: "0")
        appMenu.addItem(withTitle: "API 连接保存设置…", action: #selector(configureCredentialStorage), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "退出商品交易器", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let editItem = NSMenuItem(); menu.addItem(editItem)
        let edit = NSMenu(title: "编辑"); editItem.submenu = edit
        edit.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "复制", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        NSApp.mainMenu = menu
        window = NSWindow(contentRect: NSRect(x: 0,y: 0,width: 1200,height: 820), styleMask: [.titled,.closable,.miniaturizable,.resizable], backing: .buffered, defer: false)
        window.title = "商品策略交易器"; window.minSize = NSSize(width: 800,height: 600)
        window.isReleasedWhenClosed = false
        web = WKWebView(); web.navigationDelegate = self; window.contentView = web
        window.center(); showWindow()
        web.loadHTMLString("<body style='font:22px -apple-system;padding:60px;background:#f4f7f6'>正在启动商品交易器…</body>", baseURL: nil)
        probe(startIfNeeded: true)
    }
    @objc func showWindow() { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }
    @objc func configureCredentialStorage() {
        let choice = NSAlert()
        choice.messageText = "API 连接保存设置"
        choice.informativeText = "选择保存后，后续连接的币安及模型 API 密钥保存在 macOS 钥匙串；重启只读核验恢复，新开仓仍暂停。设置在下次后台启动时生效，不会重启当前后台；仅本次运行不会清除以前已保存的密钥，可在连接页面清除。"
        choice.addButton(withTitle: "取消")
        choice.addButton(withTitle: "保存到钥匙串")
        choice.addButton(withTitle: "仅本次运行")
        let answer = choice.runModal()
        if answer != .alertFirstButtonReturn {
            UserDefaults.standard.set(answer == .alertSecondButtonReturn, forKey: "saveAPIConnections")
        }
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { showWindow(); return true }
    func probe(startIfNeeded: Bool) {
        var req = URLRequest(url: endpoint.appendingPathComponent("api/state")); req.timeoutInterval = 1
        URLSession.shared.dataTask(with: req) { data, response, error in
            DispatchQueue.main.async {
                if let data = data, let value = (try? JSONSerialization.jsonObject(with: data)) as? [String:Any], value["state"] != nil,
                   value["application"] as? String == "commodity-strategy-terminal",
                   ["paper", "live"].contains(value["mode"] as? String ?? "") {
                    self.web.load(URLRequest(url: self.endpoint)); return
                }
                if startIfNeeded { self.startBackend() }
                self.attempts += 1
                if self.attempts >= 120 { self.fail("本地服务未能启动。请检查数据目录中的 desktop.log，确认端口 8766 未被其他程序占用。"); return }
                DispatchQueue.main.asyncAfter(deadline: .now()+1) { self.probe(startIfNeeded: false) }
            }
        }.resume()
    }
    func startBackend() {
        guard let resources = Bundle.main.resourceURL,
              let path = try? String(contentsOf: resources.appendingPathComponent("data-path.txt"), encoding: .utf8) else { fail("找不到应用配置。"); return }
        let data = URL(fileURLWithPath: path.trimmingCharacters(in: .whitespacesAndNewlines))
        do {
            try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true)
            let log = data.appendingPathComponent("desktop.log")
            if !FileManager.default.fileExists(atPath: log.path) { FileManager.default.createFile(atPath: log.path, contents: nil, attributes: [.posixPermissions: 0o600]) }
            let handle = try FileHandle(forWritingTo: log); handle.seekToEndOfFile()
            let p = Process(); p.executableURL = resources.appendingPathComponent("backend/commodity-backend")
            p.arguments = ["--port", "8766"]
            var env = ProcessInfo.processInfo.environment; env["BINANCE_TRADER_DATA"] = data.path
            let preferences = UserDefaults.standard
            if preferences.object(forKey: "saveAPIConnections") == nil {
                let choice = NSAlert()
                choice.messageText = "是否在 macOS 钥匙串保存 API 连接？"
                choice.informativeText = "选择保存后，后续连接的币安及模型 API 密钥将保存在本机钥匙串；重启时只读核验并恢复连接，新开仓仍暂停。选择仅本次运行则不保存，退出后需重新输入。"
                choice.addButton(withTitle: "仅本次运行")
                choice.addButton(withTitle: "保存到钥匙串")
                preferences.set(choice.runModal() == .alertSecondButtonReturn, forKey: "saveAPIConnections")
            }
            env["COMMODITY_SAVE_CREDENTIALS"] = preferences.bool(forKey: "saveAPIConnections") ? "1" : "0"
            p.environment = env; p.standardOutput = handle; p.standardError = handle
            try p.run(); backend = p
        } catch { fail("启动失败：\(error.localizedDescription)") }
    }
    func fail(_ message: String) { let alert = NSAlert(); alert.messageText = "无法启动交易器"; alert.informativeText = message; alert.runModal() }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        // External links open only after a deliberate click, outside the app.
        if navigationAction.navigationType == .linkActivated && url.scheme == "https" &&
           url.host != nil && url.user == nil && url.password == nil {
            NSWorkspace.shared.open(url); decisionHandler(.cancel); return
        }
        decisionHandler((url.scheme == "http" && url.host == "127.0.0.1" && url.port == 8766) || url.scheme == "about" ? .allow : .cancel)
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if let p = backend, p.isRunning {
            let alert = NSAlert(); alert.messageText = "退出交易器？"
            alert.informativeText = "退出将停止后台策略、模拟止损及实盘状态监控。交易所已确认的保护单仍会保留；未确认的订单需在币安核对。只关闭窗口可让后台继续运行。重启后新开仓保持暂停；未保存的 API 密钥需重新输入。"
            alert.addButton(withTitle: "取消"); alert.addButton(withTitle: "退出应用")
            if alert.runModal() != .alertSecondButtonReturn { return .terminateCancel }
            p.terminate()
        }
        return .terminateNow
    }
}
let app = NSApplication.shared
let delegate = AppDelegate()
app.setActivationPolicy(.regular); app.delegate = delegate; app.run()
