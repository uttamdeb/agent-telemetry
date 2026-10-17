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
        let base = URL(string: "http://127.0.0.1:\(port)")!
        // Supply the packaged app's real icon to this unbundled test executable.
        let appIcon = NSImage(contentsOfFile: CommandLine.arguments[2])!
        let originalIconSize = appIcon.size
        NSApplication.shared.applicationIconImage = appIcon
        let defaults = UserDefaults(suiteName: "AgentTelemetryIsolatedNativeTests")!
        defaults.removePersistentDomain(forName: "AgentTelemetryIsolatedNativeTests")
        defer { defaults.removePersistentDomain(forName: "AgentTelemetryIsolatedNativeTests") }
        defaults.set(-1, forKey: "refreshIntervalSeconds")
        let settings = AppSettings(defaults: defaults)
        precondition(settings.refreshIntervalSeconds == 15, "Invalid stored cadence was accepted")
        settings.menuBarDisplay = .icon
        let backend = BackendController(settings: settings)
        let menu = MenuBarPopoverController()
        let localServers = LocalServerMonitor()
        menu.install(backend: backend, settings: settings, localServers: localServers,
                     openDashboard: {}, openLocalServers: {}, quit: {})
        backend.start()
        try await Task.sleep(nanoseconds: 1_000_000_000)
        precondition(backend.summary.tokens == 100 && backend.isReady)
        let localServerDeadline = Date().addingTimeInterval(5)
        while !localServers.isAvailable && Date() < localServerDeadline {
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        precondition(localServers.isAvailable && localServers.isSupported == true)
        precondition(localServers.runningServers.first?.id == "server-fixture")
        precondition(localServers.runningServers.first?.address == "127.0.0.1:3000")
        let reflected = Mirror(reflecting: menu).children.first { $0.label == "statusItem" }!.value
        let statusItem = reflected as! NSStatusItem
        let brandIcon = statusItem.button!.image!
        precondition(!brandIcon.isTemplate && brandIcon.size == NSSize(width: 18, height: 18))
        precondition(appIcon.size == originalIconSize, "Resizing the status icon changed the app icon")
        func raster(_ image: NSImage) -> Data {
            let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 18, pixelsHigh: 18,
                bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
            NSGraphicsContext.saveGraphicsState()
            NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
            NSColor.clear.setFill()
            NSRect(x: 0, y: 0, width: 18, height: 18).fill(using: .copy)
            image.draw(in: NSRect(x: 0, y: 0, width: 18, height: 18))
            NSGraphicsContext.restoreGraphicsState()
            return bitmap.representation(using: .png, properties: [:])!
        }
        let expectedIcon = appIcon.copy() as! NSImage
        expectedIcon.size = NSSize(width: 18, height: 18)
        if CommandLine.arguments.count > 3 {
            let output = URL(fileURLWithPath: CommandLine.arguments[3])
            try raster(brandIcon).write(to: output.appendingPathComponent("mac-status-icon.png"))
            try raster(expectedIcon).write(to: output.appendingPathComponent("mac-expected-icon.png"))
        }
        // AppKit caches a small representation when an NSImage enters a status item.
        // Its edge filtering can vary slightly from drawing the 1024px master directly.
        let actualPixels = NSBitmapImageRep(data: raster(brandIcon))!
        let expectedPixels = NSBitmapImageRep(data: raster(expectedIcon))!
        var difference = 0.0
        for y in 0..<18 { for x in 0..<18 {
            let a = actualPixels.colorAt(x: x, y: y)!.usingColorSpace(.deviceRGB)!
            let b = expectedPixels.colorAt(x: x, y: y)!.usingColorSpace(.deviceRGB)!
            difference += abs(a.redComponent - b.redComponent) + abs(a.greenComponent - b.greenComponent)
                + abs(a.blueComponent - b.blueComponent) + abs(a.alphaComponent - b.alphaComponent)
        } }
        precondition(difference / (18 * 18 * 4) < 1.0 / 255, "Menu icon does not match the app mark")
        settings.menuBarDisplay = .numbers
        precondition(statusItem.button!.image !== brandIcon, "Numeric display did not replace the icon")
        settings.menuBarDisplay = .icon
        precondition(statusItem.button!.image === brandIcon, "Icon display did not restore the app mark")
        precondition(statusItem.button!.accessibilityLabel()!.contains("AgentTelemetry"))
        print("PASS real application mark, separate numeric display and unchanged application icon")
        precondition(statusItem.button!.toolTip!.contains("100 tokens"), "First menu summary is stale")
        print("PASS actual status-item subscriber renders the first summary")

        let fixtureServer = localServers.runningServers[0]
        try await localServers.start(fixtureServer)
        try await localServers.stop(fixtureServer)
        try await localServers.restart(fixtureServer)
        let (actionData, _) = try await URLSession.shared.data(from: base.appendingPathComponent("fixture/local-server-actions"))
        let actions = try JSONSerialization.jsonObject(with: actionData) as! [[String: Any]]
        precondition(actions.count == 3)
        let startBody = actions[0]["body"] as! [String: Any]
        precondition(actions[0]["path"] as? String == "/api/local-servers/start")
        precondition(startBody["confirmed"] as? Bool == true && startBody["profileId"] as? String == "profile-fixture")
        let stopBody = actions[1]["body"] as! [String: Any]
        precondition(actions[1]["path"] as? String == "/api/local-servers/stop")
        precondition(stopBody["confirmed"] as? Bool == true && stopBody["id"] as? String == "server-fixture")
        let restartBody = actions[2]["body"] as! [String: Any]
        precondition(actions[2]["path"] as? String == "/api/local-servers/restart")
        precondition(restartBody["confirmed"] as? Bool == true
            && restartBody["profileId"] as? String == "profile-fixture"
            && restartBody["serverId"] as? String == "server-fixture")
        print("PASS local server controls use only the shared backend API and confirm all launch/stop actions")

        _ = try await URLSession.shared.data(from: base.appendingPathComponent("disable-local-server-support"))
        await localServers.refresh()
        precondition(!localServers.isAvailable && localServers.isSupported == false && localServers.isStale)
        precondition(localServers.servers.first?.id == "server-fixture", "Unsupported backend erased the last successful server list")
        _ = try await URLSession.shared.data(from: base.appendingPathComponent("enable-local-server-support"))
        await localServers.refresh()
        precondition(localServers.isAvailable && localServers.isSupported == true && !localServers.isStale)
        settings.refreshIntervalSeconds = 0
        backend.refreshIntervalDidChange()
        try await Task.sleep(nanoseconds: 1_300_000_000)
        let (beforePollData, _) = try await URLSession.shared.data(from: base.appendingPathComponent("fixture/local-server-count"))
        let beforePoll = (try JSONSerialization.jsonObject(with: beforePollData) as! [String: Int])["count"]!
        try await Task.sleep(nanoseconds: 1_300_000_000)
        let (afterPollData, _) = try await URLSession.shared.data(from: base.appendingPathComponent("fixture/local-server-count"))
        let afterPoll = (try JSONSerialization.jsonObject(with: afterPollData) as! [String: Int])["count"]!
        precondition(beforePoll == afterPoll, "Manual refresh mode kept polling local server inventory")
        print("PASS unsupported capability preserves stale data, recovers, and Manual mode stops background polling")

        _ = try await URLSession.shared.data(from: base.appendingPathComponent("advance"))
        try await Task.sleep(nanoseconds: 1_300_000_000)
        precondition(backend.summary.tokens == 200, "Shared tick did not update the menu")
        settings.refreshIntervalSeconds = 60
        backend.refreshIntervalDidChange()
        try await Task.sleep(nanoseconds: 400_000_000)
        let (syncData, _) = try await URLSession.shared.data(from: base.appendingPathComponent("api/sync"))
        let sharedSettings = try JSONSerialization.jsonObject(with: syncData) as! [String: Any]
        precondition(sharedSettings["seconds"] as! Int == 60)
        var syncRequest = URLRequest(url: base.appendingPathComponent("api/sync"))
        syncRequest.httpMethod = "POST"
        syncRequest.httpBody = Data(#"{"seconds":0}"#.utf8)
        _ = try await URLSession.shared.data(for: syncRequest)
        try await Task.sleep(nanoseconds: 1_300_000_000)
        precondition(settings.refreshIntervalSeconds == 0, "Browser interval did not update the native picker")
        _ = try await URLSession.shared.data(from: base.appendingPathComponent("raw-advance"))
        try await Task.sleep(nanoseconds: 1_200_000_000)
        precondition(backend.summary.tokens == 200, "Manual mode updated without a shared signal")
        backend.refreshNow()
        try await Task.sleep(nanoseconds: 400_000_000)
        precondition(backend.summary.tokens == 250, "Manual native refresh did not reload the summary")
        print("PASS shared ticks, browser/native interval changes and Manual use one refresh clock")
        _ = try await URLSession.shared.data(from: base.appendingPathComponent("fail"))
        backend.refreshNow()
        try await Task.sleep(nanoseconds: 700_000_000)
        precondition(backend.summaryStale && backend.isMonitoring && backend.status.message != nil)
        precondition(backend.summary.tokens == 250)
        precondition(statusItem.button!.image === brandIcon, "Failure replaced the app mark")
        precondition(statusItem.button!.toolTip!.contains("Needs attention"), "Failure status is missing from the tooltip")
        settings.refreshIntervalSeconds = 15
        backend.refreshIntervalDidChange()
        _ = try await URLSession.shared.data(from: base.appendingPathComponent("recover"))
        try await Task.sleep(nanoseconds: 1_400_000_000)
        precondition(!backend.summaryStale && backend.isReady && backend.lastSummaryUpdate != nil)
        print("PASS summary failure retains data and retries after a cadence change during an outage")
        backend.stop()
        precondition(!localServers.isAvailable && localServers.isStale)
        precondition(statusItem.button!.image === brandIcon, "Stopping replaced the app mark")
        precondition(statusItem.button!.toolTip!.contains("Monitoring off"))
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
        let host = NSHostingView(rootView: DashboardWebView(url: base,
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
        let filteredURL = URL(string: base.absoluteString + "/?nativeApp=1&nativePollSeconds=60&range=today&models=Fixture&devices=Fixture#cost")!
        web.load(URLRequest(url: filteredURL))
        // A slow CI WebKit process may still be navigating after 700 ms. Take
        // the baseline only after the requested page and its saved URL settle.
        let navigationDeadline = Date().addingTimeInterval(5)
        while web.isLoading || web.url != filteredURL || latestURL != filteredURL {
            precondition(Date() < navigationDeadline, "Fixture WebView navigation did not finish")
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        let previousURL = web.url
        host.rootView = DashboardWebView(url: base, onNavigate: { latestURL = $0 })
        host.layoutSubtreeIfNeeded()
        try await Task.sleep(nanoseconds: 700_000_000)
        let result = web.url!.absoluteString
        precondition(result.contains("range=today") && result.contains("models=Fixture") && result.contains("devices=Fixture") && result.hasSuffix("#cost"))
        precondition(web.url == previousURL, "A shared interval update reloaded the dashboard")
        precondition(latestURL == web.url, "Navigation was not saved for owned restart")
        print("PASS view updates retain the WebView URL, range, model, device and tab; navigation saved")
        window.close()
    }
}
