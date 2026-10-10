import Combine
import Darwin
import Foundation

struct DailySummary: Decodable {
    let date: String
    let tokens: Int
    let spend: Double

    static let empty = DailySummary(date: "", tokens: 0, spend: 0)
}

private struct HealthPayload: Decodable {
    let service: String
    let version: String
    let ready: Bool
    let building: Bool
}

private struct LegacyDashboardPayload: Decodable {
    let records: [LegacyUsageRecord]

    var todaySummary: DailySummary {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = .current
        formatter.dateFormat = "yyyy-MM-dd"
        let today = formatter.string(from: Date())
        let todaysRecords = records.filter { $0.date == today }
        return DailySummary(
            date: today,
            tokens: todaysRecords.reduce(0) { $0 + $1.tokens },
            spend: todaysRecords.reduce(0) { $0 + ($1.cost ?? 0) }
        )
    }
}

private struct LegacyUsageRecord: Decodable {
    let date: String
    let inputTokens: Int?
    let outputTokens: Int?
    let cacheReadTokens: Int?
    let cacheWriteTokens: Int?
    let cost: Double?

    var tokens: Int {
        (inputTokens ?? 0) + (outputTokens ?? 0) + (cacheReadTokens ?? 0) + (cacheWriteTokens ?? 0)
    }

    private enum CodingKeys: String, CodingKey {
        case date
        case inputTokens = "in"
        case outputTokens = "out"
        case cacheReadTokens = "cr"
        case cacheWriteTokens = "cc"
        case cost
    }
}

enum BackendStatus: Equatable {
    case stopped
    case starting
    case running
    case connectedToExisting
    case stopping
    case needsAttention(String)

    var isReady: Bool {
        self == .running || self == .connectedToExisting
    }

    var title: String {
        switch self {
        case .stopped: return "Monitoring off"
        case .starting: return "Starting…"
        case .running: return "Monitoring on"
        case .connectedToExisting: return "Using existing dashboard"
        case .stopping: return "Stopping…"
        case .needsAttention: return "Needs attention"
        }
    }

    var symbol: String {
        switch self {
        case .stopped: return "pause.circle"
        case .starting, .stopping: return "ellipsis.circle"
        case .running, .connectedToExisting: return "checkmark.circle.fill"
        case .needsAttention: return "exclamationmark.triangle.fill"
        }
    }

    var isActive: Bool { isReady }

    var message: String? {
        if case let .needsAttention(message) = self { return message }
        return nil
    }
}

@MainActor
final class BackendController: ObservableObject {
    @Published private(set) var status: BackendStatus = .stopped
    @Published private(set) var summary = DailySummary.empty
    @Published private(set) var summaryStale = false
    @Published private(set) var lastSummaryUpdate: Date?
    // Owned restarts can recreate the WebView; retain its current filters and tab.
    var lastDashboardURL: URL?

    let dashboardURL = URL(string: "http://127.0.0.1:7878")!

    private let settings: AppSettings
    private let session = URLSession.shared
    private let diagnostics = DiagnosticBuffer()
    private var process: Process?
    private var ownsProcess = false
    private var isLegacyService = false
    private var isStopping = false
    private var lifecycle = 0
    private var healthTask: Task<Void, Never>?
    private var summaryTask: Task<Void, Never>?
    private var pendingDashboardOpen: (() -> Void)?
    private var pendingStop: (() -> Void)?

    private enum PortProbe {
        case unavailable
        case occupied
        case service(HealthPayload)
        case legacyService(LegacyDashboardPayload)
    }

    init(settings: AppSettings) {
        self.settings = settings
    }

    var isReady: Bool { status.isReady }
    var isMonitoring: Bool { isReady || summaryStale }

    var canImportExistingData: Bool {
        if status == .stopped { return true }
        if case .needsAttention = status { return process?.isRunning != true }
        return false
    }

    func start(openDashboard: (() -> Void)? = nil) {
        if let openDashboard { pendingDashboardOpen = openDashboard }
        if summaryStale {
            Task {
                await refreshSummary()
                if isReady { openPendingDashboard() }
            }
            return
        }
        if isReady {
            openPendingDashboard()
            Task { await refreshSummary() }
            return
        }
        guard status != .starting, status != .stopping else { return }

        lifecycle += 1
        let attempt = lifecycle
        status = .starting
        healthTask?.cancel()
        healthTask = Task { [weak self] in
            guard let self else { return }
            await self.connectOrLaunch(lifecycle: attempt)
        }
    }

    func stop(after: (() -> Void)? = nil) {
        lifecycle += 1
        summaryStale = false
        healthTask?.cancel()
        summaryTask?.cancel()
        summaryTask = nil
        pendingDashboardOpen = nil

        guard ownsProcess, let process, process.isRunning else {
            status = .stopped
            ownsProcess = false
            isLegacyService = false
            after?()
            return
        }

        isStopping = true
        pendingStop = after
        status = .stopping
        process.terminate() // SIGTERM lets dashboard.py flush its durable ledger.
        let pid = process.processIdentifier
        Task {
            try? await Task.sleep(nanoseconds: 15_000_000_000)
            if process.isRunning { _ = kill(pid, SIGKILL) }
        }
    }

    func refreshNow() {
        guard isMonitoring else { start(); return }
        Task { await postRefreshAndReloadSummary() }
    }

    func importExistingData(from source: URL) {
        guard canImportExistingData else { return }
        let destination = applicationSupportDirectory
        status = .starting
        Task { [weak self] in
            guard let self else { return }
            do {
                let probe = await self.probePort()
                guard case .unavailable = probe else {
                    self.status = .needsAttention("Quit the existing local service before importing data.")
                    return
                }
                try await Task.detached(priority: .userInitiated) {
                    try CacheMigration.importData(from: source, to: destination)
                }.value
                self.summary = .empty
                self.status = .stopped
                self.start()
            } catch {
                self.status = .needsAttention(error.localizedDescription)
            }
        }
    }

    func refreshIntervalDidChange() {
        summaryTask?.cancel()
        summaryTask = nil
        guard isReady else { return }
        beginSummaryUpdates()

        // The Python service reads its refresh cadence at launch. Restart only
        // a process owned by this app; an existing dashboard remains untouched.
        guard ownsProcess else { return }
        stop { [weak self] in self?.start() }
    }

    private func connectOrLaunch(lifecycle attempt: Int) async {
        let probe = await probePort()
        guard !Task.isCancelled, lifecycle == attempt else { return }
        switch probe {
        case let .service(health) where health.service == "agent-telemetry":
            if health.ready {
                ownsProcess = false
                isLegacyService = false
                status = .connectedToExisting
                beginSummaryUpdates()
                openPendingDashboard()
            } else {
                await waitUntilReady(lifecycle: attempt, owned: false)
            }
        case let .legacyService(payload):
            guard lifecycle == attempt else { return }
            ownsProcess = false
            isLegacyService = true
            summary = payload.todaySummary
            status = .connectedToExisting
            beginSummaryUpdates()
            openPendingDashboard()
        case .service, .occupied:
            guard lifecycle == attempt else { return }
            fail("Port 7878 is being used by another app.")
        case .unavailable:
            guard lifecycle == attempt else { return }
            launchOwnedBackend(lifecycle: attempt)
        }
    }

    private func launchOwnedBackend(lifecycle attempt: Int) {
        guard let backendDirectory = locateBackendDirectory(),
              let python = locatePythonRuntime() else {
            fail("The bundled Python runtime or dashboard files are missing.")
            return
        }

        let supportDirectory = applicationSupportDirectory
        do {
            try FileManager.default.createDirectory(at: supportDirectory,
                                                    withIntermediateDirectories: true)
        } catch {
            fail("AgentTelemetry couldn’t create its local data folder.")
            return
        }

        let child = Process()
        child.executableURL = python
        child.currentDirectoryURL = backendDirectory
        child.arguments = [
            backendDirectory.appendingPathComponent("dashboard.py").path,
            "--host", "127.0.0.1",
            "--port", "7878",
            "--interval", String(settings.refreshIntervalSeconds),
            "--data-dir", supportDirectory.path
        ]
        child.environment = ProcessInfo.processInfo.environment.merging([
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "AGENT_TELEMETRY_VERSION": Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "development"
        ]) { _, new in new }

        let output = Pipe()
        let errors = Pipe()
        child.standardOutput = output
        child.standardError = errors
        drain(output.fileHandleForReading)
        drain(errors.fileHandleForReading)
        child.terminationHandler = { [weak self] terminatedChild in
            let exitStatus = terminatedChild.terminationStatus
            Task { @MainActor [weak self] in
                self?.processDidExit(status: exitStatus)
            }
        }

        do {
            process = child
            ownsProcess = true
            isLegacyService = false
            try child.run()
            healthTask = Task { [weak self] in
                guard let self else { return }
                await self.waitUntilReady(lifecycle: attempt, owned: true)
            }
        } catch {
            process = nil
            ownsProcess = false
            fail("AgentTelemetry couldn’t start its local service.")
        }
    }

    private func waitUntilReady(lifecycle attempt: Int, owned: Bool) async {
        for _ in 0..<600 {
            guard !Task.isCancelled, lifecycle == attempt else { return }
            let probe = await probePort()
            guard !Task.isCancelled, lifecycle == attempt else { return }
            switch probe {
            case let .service(health) where health.service == "agent-telemetry" && health.ready:
                ownsProcess = owned
                isLegacyService = false
                status = owned ? .running : .connectedToExisting
                beginSummaryUpdates()
                openPendingDashboard()
                return
            case let .service(health) where health.service == "agent-telemetry" && !health.ready:
                break
            case let .legacyService(payload):
                guard lifecycle == attempt else { return }
                guard !owned else {
                    fail("Port 7878 is being used by another AgentTelemetry service.", stopOwnedProcess: true)
                    return
                }
                ownsProcess = false
                isLegacyService = true
                summary = payload.todaySummary
                status = .connectedToExisting
                beginSummaryUpdates()
                openPendingDashboard()
                return
            case .service, .occupied:
                guard lifecycle == attempt else { return }
                fail("Port 7878 is responding without AgentTelemetry readiness.")
                return
            case .unavailable:
                break
            }
            try? await Task.sleep(nanoseconds: 500_000_000)
        }
        guard lifecycle == attempt else { return }
        fail("AgentTelemetry didn’t become ready in time.", stopOwnedProcess: true)
    }

    private func probePort() async -> PortProbe {
        var request = URLRequest(url: dashboardURL.appendingPathComponent("api/health"))
        request.timeoutInterval = 1.2
        do {
            let (data, response) = try await session.data(for: request)
            guard let response = response as? HTTPURLResponse else { return .occupied }
            if response.statusCode == 200,
               let health = try? JSONDecoder().decode(HealthPayload.self, from: data) {
                return .service(health)
            }
            guard response.statusCode == 404 else { return .occupied }
            return await probeLegacyDashboard()
        } catch {
            return .unavailable
        }
    }

    private func probeLegacyDashboard() async -> PortProbe {
        var rootRequest = URLRequest(url: dashboardURL)
        rootRequest.timeoutInterval = 1.2
        do {
            let (rootData, rootResponse) = try await session.data(for: rootRequest)
            guard (rootResponse as? HTTPURLResponse)?.statusCode == 200,
                  let html = String(data: rootData, encoding: .utf8),
                  html.localizedCaseInsensitiveContains("<title>AgentTelemetry</title>") else {
                return .occupied
            }

            var dataRequest = URLRequest(url: dashboardURL.appendingPathComponent("api/data"))
            dataRequest.timeoutInterval = 8
            let (data, response) = try await session.data(for: dataRequest)
            guard (response as? HTTPURLResponse)?.statusCode == 200,
                  let payload = try? JSONDecoder().decode(LegacyDashboardPayload.self, from: data) else {
                return .occupied
            }
            return .legacyService(payload)
        } catch {
            return .unavailable
        }
    }

    private func beginSummaryUpdates() {
        summaryTask?.cancel()
        summaryTask = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                await self.refreshSummary()
                try? await Task.sleep(nanoseconds: UInt64(self.settings.refreshIntervalSeconds) * 1_000_000_000)
            }
        }
    }

    private func refreshSummary() async {
        guard isReady || summaryStale else { return }
        let attempt = lifecycle
        do {
            let endpoint = isLegacyService ? "api/data" : "api/summary"
            var request = URLRequest(url: dashboardURL.appendingPathComponent(endpoint))
            request.timeoutInterval = isLegacyService ? 8 : 10
            let (data, response) = try await session.data(for: request)
            guard !Task.isCancelled, lifecycle == attempt else { return }
            guard (response as? HTTPURLResponse)?.statusCode == 200 else {
                markSummaryStale()
                return
            }
            if isLegacyService {
                summary = try JSONDecoder().decode(LegacyDashboardPayload.self, from: data).todaySummary
            } else {
                summary = try JSONDecoder().decode(DailySummary.self, from: data)
            }
            summaryStale = false
            lastSummaryUpdate = Date()
            status = ownsProcess ? .running : .connectedToExisting
        } catch {
            guard !Task.isCancelled, lifecycle == attempt else { return }
            markSummaryStale()
        }
    }

    private func markSummaryStale() {
        summaryStale = true
        status = .needsAttention("The dashboard is unavailable. Figures are from the last successful refresh; retrying.")
    }

    private func postRefreshAndReloadSummary() async {
        let attempt = lifecycle
        var request = URLRequest(url: dashboardURL.appendingPathComponent("api/refresh"))
        request.httpMethod = "POST"
        request.timeoutInterval = 60
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = Data("{}".utf8)
        do {
            let (_, response) = try await session.data(for: request)
            guard !Task.isCancelled, lifecycle == attempt else { return }
            guard (response as? HTTPURLResponse)?.statusCode == 200 else {
                markSummaryStale()
                return
            }
            await refreshSummary()
        } catch {
            guard !Task.isCancelled, lifecycle == attempt else { return }
            markSummaryStale()
        }
    }

    private func fail(_ message: String, stopOwnedProcess: Bool = false) {
        status = .needsAttention(message)
        isLegacyService = false
        pendingDashboardOpen = nil
        summaryTask?.cancel()
        summaryTask = nil
        guard stopOwnedProcess, ownsProcess, let process, process.isRunning else { return }
        isStopping = true
        process.terminate()
        let pid = process.processIdentifier
        Task {
            try? await Task.sleep(nanoseconds: 15_000_000_000)
            if process.isRunning { _ = kill(pid, SIGKILL) }
        }
    }

    private func processDidExit(status exitStatus: Int32) {
        lifecycle += 1
        summaryStale = false
        process = nil
        ownsProcess = false
        isLegacyService = false
        summaryTask?.cancel()
        summaryTask = nil
        if isStopping {
            isStopping = false
            if status.message == nil { status = .stopped }
            let callback = pendingStop
            pendingStop = nil
            callback?()
            return
        }
        status = .needsAttention(exitStatus == 0
            ? "The local service stopped unexpectedly."
            : "The local service exited. Retry to start it again.")
    }

    private func openPendingDashboard() {
        let callback = pendingDashboardOpen
        pendingDashboardOpen = nil
        callback?()
    }

    private func drain(_ handle: FileHandle) {
        let buffer = diagnostics
        handle.readabilityHandler = { file in
            let data = file.availableData
            if data.isEmpty {
                file.readabilityHandler = nil
            } else {
                buffer.append(data)
            }
        }
    }

    private func locateBackendDirectory() -> URL? {
        if let resources = Bundle.main.resourceURL {
            let bundled = resources.appendingPathComponent("AgentTelemetry", isDirectory: true)
            if FileManager.default.fileExists(atPath: bundled.appendingPathComponent("dashboard.py").path) {
                return bundled
            }
        }
        var directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        for _ in 0..<8 {
            if FileManager.default.fileExists(atPath: directory.appendingPathComponent("dashboard.py").path) {
                return directory
            }
            directory.deleteLastPathComponent()
        }
        return nil
    }

    private var applicationSupportDirectory: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
            .appendingPathComponent("AgentTelemetry", isDirectory: true)
    }

    private func locatePythonRuntime() -> URL? {
        if let resources = Bundle.main.resourceURL {
            for relative in ["AgentTelemetryRuntime/bin/python3", "Python/bin/python3"] {
                let candidate = resources.appendingPathComponent(relative)
                if FileManager.default.isExecutableFile(atPath: candidate.path) { return candidate }
            }
        }
        let searchPath = ProcessInfo.processInfo.environment["PATH"] ?? ""
        for directory in searchPath.split(separator: ":") {
            let candidate = URL(fileURLWithPath: String(directory)).appendingPathComponent("python3")
            if FileManager.default.isExecutableFile(atPath: candidate.path) { return candidate }
        }
        return nil
    }
}

private final class DiagnosticBuffer: @unchecked Sendable {
    private let lock = NSLock()
    private var tail = Data()

    func append(_ data: Data) {
        lock.lock()
        tail.append(data)
        if tail.count > 8_192 { tail.removeFirst(tail.count - 8_192) }
        lock.unlock()
    }
}
