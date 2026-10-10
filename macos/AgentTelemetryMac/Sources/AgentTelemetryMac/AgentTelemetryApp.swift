import AppKit
import SwiftUI

@main
@MainActor
struct AgentTelemetryApp: App {
    @Environment(\.openWindow) private var openWindow
    @StateObject private var settings: AppSettings
    @StateObject private var backend: BackendController
    @StateObject private var menuBarPopover = MenuBarPopoverController()
    @StateObject private var nativeControl = NativeControl()

    init() {
        let settings = AppSettings()
        let backend = BackendController(settings: settings)
        _settings = StateObject(wrappedValue: settings)
        _backend = StateObject(wrappedValue: backend)
        if settings.startMonitoringAutomatically {
            Task { @MainActor in backend.start() }
        }
    }

    var body: some Scene {
        let _ = menuBarPopover.install(
            backend: backend,
            settings: settings,
            openDashboard: { openDashboard() },
            quit: { quit() }
        )
        let _ = nativeControl.start(quit: { quit() })

        WindowGroup("AgentTelemetry", id: "dashboard") {
            DashboardWindow(backend: backend, settings: settings)
                .onAppear {
                    if nativeControl.requestedFromDashboard { backend.start() }
                }
        }
    }

    private func openDashboard() {
        backend.start(openDashboard: {
            openWindow(id: "dashboard")
            NSApp.activate(ignoringOtherApps: true)
        })
    }

    private func quit() {
        backend.stop {
            nativeControl.finish()
            NSApp.terminate(nil)
        }
    }

}
