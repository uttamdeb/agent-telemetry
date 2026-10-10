import AppKit
import Combine
import Darwin
import Foundation
import SwiftUI
import WebKit

@main
struct MacNativeRegression {
    @MainActor
    static func main() async throws {
        _ = NSApplication.shared
        let port = Int(CommandLine.arguments[1])!
        let defaults = UserDefaults(suiteName: "AgentTelemetryIsolatedNativeTests")!
        defaults.removePersistentDomain(forName: "AgentTelemetryIsolatedNativeTests")
        defer { defaults.removePersistentDomain(forName: "AgentTelemetryIsolatedNativeTests") }
        defaults.set(-1, forKey: "refreshIntervalSeconds")
        let settings = AppSettings(defaults: defaults)
        precondition(settings.refreshIntervalSeconds == 300, "Invalid stored cadence was accepted")
        let backend = BackendController(settings: settings)
        let menu = MenuBarPopoverController()
        menu.install(backend: backend, settings: settings, openDashboard: {}, quit: {})
        backend.start()
        try await Task.sleep(nanoseconds: 1_000_000_000)
        precondition(backend.summary.tokens == 100 && backend.isReady)
        let reflected = Mirror(reflecting: menu).children.first { $0.label == "statusItem" }!.value
        let statusItem = reflected as! NSStatusItem
        precondition(statusItem.button!.toolTip!.contains("100 tokens"), "First menu summary is stale")
        print("PASS actual status-item subscriber renders the first summary")

        let base = URL(string: "http://127.0.0.1:\(port)")!
        _ = try await URLSession.shared.data(from: base.appendingPathComponent("fail"))
        backend.refreshNow()
        try await Task.sleep(nanoseconds: 700_000_000)
        precondition(backend.summaryStale && backend.isMonitoring && backend.status.message != nil)
        precondition(backend.summary.tokens == 100)
        settings.refreshIntervalSeconds = 1 // Short isolated retry interval, no user defaults.
        backend.refreshIntervalDidChange()
        _ = try await URLSession.shared.data(from: base.appendingPathComponent("recover"))
        try await Task.sleep(nanoseconds: 1_400_000_000)
        precondition(!backend.summaryStale && backend.isReady && backend.lastSummaryUpdate != nil)
        print("PASS summary failure retains data and retries after a cadence change during an outage")
        backend.stop()
        settings.refreshIntervalSeconds = 300

        _ = try await URLSession.shared.data(from: base.appendingPathComponent("delay"))
        let stoppingBackend = BackendController(settings: settings)
        stoppingBackend.start()
        try await Task.sleep(nanoseconds: 100_000_000)
        usleep(600_000) // Queue a completed response behind Stop on the MainActor.
        stoppingBackend.stop()
        try await Task.sleep(nanoseconds: 800_000_000)
        precondition(stoppingBackend.status == .stopped, "Health completion undid Stop")
        print("PASS canceled health completion cannot revive monitoring")

        let controls = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: controls, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: controls) }
        let controlURL = controls.appendingPathComponent("native-control.json")
        let heartbeatURL = controls.appendingPathComponent("native-status.json")
        try Data(#"{"enabled":true,"generation":"closing-fixture"}"#.utf8).write(to: controlURL)
        var quits = 0
        let native = NativeControl(root: controls, generation: "closing-fixture")
        native.start { quits += 1 } // Simulate a backend that is still saving.
        try await Task.sleep(nanoseconds: 200_000_000)
        try Data(#"{"enabled":false,"generation":"closing-fixture"}"#.utf8).write(to: controlURL)
        try await Task.sleep(nanoseconds: 1_400_000_000)
        let firstHeartbeat = try JSONSerialization.jsonObject(with: Data(contentsOf: heartbeatURL)) as! [String: Any]
        try await Task.sleep(nanoseconds: 1_200_000_000)
        let nextHeartbeat = try JSONSerialization.jsonObject(with: Data(contentsOf: heartbeatURL)) as! [String: Any]
        precondition(quits == 1 && (nextHeartbeat["time"] as! Double) > (firstHeartbeat["time"] as! Double))
        native.finish()
        print("PASS closing native app retains a live heartbeat while its owned backend saves")

        var latestURL: URL?
        let host = NSHostingView(rootView: DashboardWebView(url: base, refreshIntervalSeconds: 60,
                                                            onNavigate: { latestURL = $0 }))
        host.frame = NSRect(x: 0, y: 0, width: 900, height: 700)
        let window = NSWindow(contentRect: host.frame, styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        try await Task.sleep(nanoseconds: 1_000_000_000)
        func findWebView(_ view: NSView) -> WKWebView? {
            if let web = view as? WKWebView { return web }
            return view.subviews.compactMap(findWebView).first
        }
        let web = findWebView(host)!
        web.load(URLRequest(url: URL(string: base.absoluteString + "/?nativeApp=1&nativePollSeconds=60&range=today&models=Fixture&devices=Fixture#cost")!))
        try await Task.sleep(nanoseconds: 700_000_000)
        host.rootView = DashboardWebView(url: base, refreshIntervalSeconds: 300, onNavigate: { latestURL = $0 })
        host.layoutSubtreeIfNeeded()
        try await Task.sleep(nanoseconds: 700_000_000)
        let result = web.url!.absoluteString
        precondition(result.contains("range=today") && result.contains("models=Fixture") && result.contains("devices=Fixture") && result.hasSuffix("#cost"))
        precondition(result.contains("nativePollSeconds=300"))
        precondition(latestURL == web.url, "Navigation was not saved for owned restart")
        print("PASS interval change preserves range, model, device and tab; navigation saved")
        window.close()
    }
}
