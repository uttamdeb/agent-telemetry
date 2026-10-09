import Combine
import Foundation
import ServiceManagement

@MainActor
final class AppSettings: ObservableObject {
    enum MenuBarDisplay: String, CaseIterable, Identifiable {
        case icon
        case numbers

        var id: String { rawValue }
        var title: String { rawValue.capitalized }
    }

    @Published var menuBarDisplay: MenuBarDisplay {
        didSet { defaults.set(menuBarDisplay.rawValue, forKey: Key.menuBarDisplay) }
    }

    @Published var refreshIntervalSeconds: Int {
        didSet { defaults.set(refreshIntervalSeconds, forKey: Key.refreshInterval) }
    }

    @Published var startMonitoringAutomatically: Bool {
        didSet { defaults.set(startMonitoringAutomatically, forKey: Key.autoStart) }
    }

    @Published private(set) var openAtLogin: Bool
    @Published private(set) var loginItemMessage: String?

    private let defaults: UserDefaults

    private enum Key {
        static let menuBarDisplay = "menuBarDisplay"
        static let refreshInterval = "refreshIntervalSeconds"
        static let autoStart = "startMonitoringAutomatically"
    }

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let savedDisplay = defaults.string(forKey: Key.menuBarDisplay)
        menuBarDisplay = MenuBarDisplay(rawValue: savedDisplay ?? "") ?? .numbers
        refreshIntervalSeconds = defaults.object(forKey: Key.refreshInterval) as? Int ?? 300
        startMonitoringAutomatically = defaults.bool(forKey: Key.autoStart)
        openAtLogin = SMAppService.mainApp.status == .enabled
    }

    func setOpenAtLogin(_ enabled: Bool) {
        do {
            if enabled {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
            openAtLogin = SMAppService.mainApp.status == .enabled
            loginItemMessage = SMAppService.mainApp.status == .requiresApproval
                ? "Allow AgentTelemetry in System Settings → General → Login Items."
                : nil
        } catch {
            openAtLogin = SMAppService.mainApp.status == .enabled
            loginItemMessage = "Login item could not be changed. Check Login Items in System Settings."
        }
    }
}
