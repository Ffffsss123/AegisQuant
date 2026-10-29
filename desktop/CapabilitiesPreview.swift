import AppKit

// A separate read-only scope viewer. No trading model, backend, or credentials.
let app=NSApplication.shared
app.setActivationPolicy(.regular)
let window=NSWindow(contentRect:NSRect(x:0,y:0,width:1050,height:760),styleMask:[.titled,.closable,.miniaturizable],backing:.buffered,defer:false)
window.title="AegisQuant — Securities Capabilities"
let navy=NSColor(calibratedRed:0.035,green:0.065,blue:0.105,alpha:1)
let mint=NSColor(calibratedRed:0.38,green:0.88,blue:0.72,alpha:1)
let content=NSView();content.wantsLayer=true;content.layer?.backgroundColor=navy.cgColor;window.contentView=content
let stack=NSStackView();stack.orientation = .vertical;stack.alignment = .leading;stack.spacing=22;stack.translatesAutoresizingMaskIntoConstraints=false;content.addSubview(stack)
NSLayoutConstraint.activate([stack.leadingAnchor.constraint(equalTo:content.leadingAnchor,constant:36),stack.trailingAnchor.constraint(equalTo:content.trailingAnchor,constant:-36),stack.topAnchor.constraint(equalTo:content.topAnchor,constant:28)])
func label(_ text:String,_ size:CGFloat=16,_ color:NSColor = .white)->NSTextField {let l=NSTextField(wrappingLabelWithString:text);l.font=NSFont.systemFont(ofSize:size,weight:size>24 ? .bold:.regular);l.textColor=color;return l}
stack.addArrangedSubview(label("AegisQuant",34,mint))
stack.addArrangedSubview(label("Securities research & simulation",25))
stack.addArrangedSubview(label("READ-ONLY CAPABILITY PREVIEW · No account data loaded",13,mint))
let path=Bundle.main.url(forResource:"capabilities",withExtension:"json")!
let data=try Data(contentsOf:path)
let manifest=try JSONSerialization.jsonObject(with:data) as! [String:Any]
for row in manifest["markets"] as! [[String:Any]] {
 let box=NSStackView();box.orientation = .vertical;box.alignment = .leading;box.spacing=8
 box.addArrangedSubview(label(row["market"] as! String,22,mint))
 box.addArrangedSubview(label(row["scope"] as! String,16))
 box.addArrangedSubview(label("SIMULATE execution: \((row["simulation_execution"] as! Bool) ? "available code path" : "NOT ENABLED")",13,.lightGray))
 stack.addArrangedSubview(box)
}
stack.addArrangedSubview(label("FUTURE is a reserved accounting category, not a verified execution channel.\nBinance commodity perpetuals are a separate application.",16,.orange))
stack.addArrangedSubview(label("No backend started · No orders submitted\nCode scope does not prove broker acceptance, live readiness, or profitability.",13,.lightGray))
window.center();window.makeKeyAndOrderFront(nil);app.activate(ignoringOtherApps:true);app.run()
