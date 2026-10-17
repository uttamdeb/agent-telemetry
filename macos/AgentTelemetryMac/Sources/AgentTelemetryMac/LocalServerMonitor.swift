import Combine
import Foundation

struct LocalServer: Decodable, Identifiable, Equatable, Sendable {
    let id: String
    let pid: Int
    let process: String
    let cwd: String
    let project: String
    let host: String
    let port: Int
    let startedAt: String
    let state: String
    let firstSeen: String
    let lastSeen: String
    let stoppedAt: String?
    let profileID: String?
    let isConfigured: Bool
    let isPinned: Bool

    var isRunning: Bool { state == "running" }
    var address: String {
        let addressHost = ["*", "0.0.0.0", "::"].contains(host) ? "localhost" : host
        let formattedHost = addressHost.contains(":") ? "[\(addressHost)]" : addressHost
        return "\(formattedHost):\(port)"
    }
    var location: String { project.isEmpty ? process : project }
    var displayDetails: String { project.isEmpty ? address : "\(address) · \(process)" }

    private enum CodingKeys: String, CodingKey {
        case id, pid, process, cwd, project, host, port, startedAt, state, firstSeen, lastSeen, stoppedAt
        case profileID = "profileId"
        case isConfigured, isPinned
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        pid = try values.decode(Int.self, forKey: .pid)
        process = try values.decode(String.self, forKey: .process)
        cwd = try values.decode(String.self, forKey: .cwd)
        project = try values.decode(String.self, forKey: .project)
        host = try values.decode(String.self, forKey: .host)
        port = try values.decode(Int.self, forKey: .port)
        startedAt = try values.decode(String.self, forKey: .startedAt)
        state = try values.decode(String.self, forKey: .state)
        firstSeen = try values.decode(String.self, forKey: .firstSeen)
        lastSeen = try values.decode(String.self, forKey: .lastSeen)
        stoppedAt = try values.decodeIfPresent(String.self, forKey: .stoppedAt)
        profileID = try values.decodeIfPresent(String.self, forKey: .profileID)
        isConfigured = try values.decodeIfPresent(Bool.self, forKey: .isConfigured) ?? false
        isPinned = try values.decodeIfPresent(Bool.self, forKey: .isPinned) ?? false
    }
}

private struct LocalServerPayload: Decodable, Sendable {
    let supported: Bool?
    let message: String?
    let updatedAt: String?
    let servers: [LocalServer]

    private enum CodingKeys: String, CodingKey { case supported, message, updatedAt, servers }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        supported = try values.decodeIfPresent(Bool.self, forKey: .supported)
        message = try values.decodeIfPresent(String.self, forKey: .message)
        updatedAt = try values.decodeIfPresent(String.self, forKey: .updatedAt)
        servers = try values.decodeIfPresent([LocalServer].self, forKey: .servers) ?? []
    }
}

@MainActor
final class LocalServerMonitor: ObservableObject {
    @Published private(set) var servers: [LocalServer] = []
    @Published private(set) var errorMessage: String?
    @Published private(set) var isRefreshing = false
    @Published private(set) var isAvailable = false
    @Published private(set) var isStale = false
    @Published private(set) var isSupported: Bool?

    private weak var backend: BackendController?
    private var statusSubscription: AnyCancellable?
    private var refreshSubscription: AnyCancellable?
    private var backendReady = false
    private var requestGeneration = 0
    private var actionInFlight = false
    private var refreshPending = false

    var runningServers: [LocalServer] { servers.filter(\.isRunning) }
    var recentlyStopped: [LocalServer] { servers.filter { !$0.isRunning } }
    var pinnedServers: [LocalServer] { servers.filter(\.isPinned) }

    func connect(to backend: BackendController) {
        guard self.backend !== backend else { return }
        statusSubscription?.cancel()
        refreshSubscription?.cancel()
        invalidateRequests()
        self.backend = backend
        statusSubscription = backend.$status.sink { [weak self] status in
            // @Published emits in willSet: use this incoming value instead of
            // rereading backend.status before the assignment has completed.
            self?.backendStatusDidChange(status)
        }
        refreshSubscription = backend.$refreshGeneration
            .dropFirst()
            .sink { [weak self] generation in
                // This signal follows a successful shared-clock summary fetch.
                // It keeps server details on the same cadence without another
                // native /api/sync poller.
                Task { @MainActor [weak self] in
                    self?.backendRefreshDidSucceed(generation)
                }
            }
    }

    func refresh() async {
        guard backendReady, let backend else {
            isAvailable = false
            isStale = !servers.isEmpty
            if errorMessage == nil {
                errorMessage = "Start monitoring to view local servers."
            }
            return
        }
        guard !isRefreshing, !actionInFlight else {
            refreshPending = true
            return
        }
        requestGeneration += 1
        let attempt = requestGeneration
        refreshPending = false
        isRefreshing = true
        defer {
            if requestGeneration == attempt {
                isRefreshing = false
                if refreshPending && backendReady && !actionInFlight {
                    refreshPending = false
                    Task { await refresh() }
                }
            }
        }

        do {
            let payload = try await fetchFromBackend(backend)
            guard !Task.isCancelled, requestGeneration == attempt, backendReady else { return }
            guard payload.supported == true else {
                isSupported = false
                isAvailable = false
                isStale = !servers.isEmpty
                errorMessage = payload.message ?? "Update AgentTelemetry to view local servers in the native app."
                return
            }
            servers = payload.servers
            isSupported = true
            isAvailable = true
            isStale = false
            errorMessage = nil
        } catch {
            guard !Task.isCancelled, requestGeneration == attempt, backendReady else { return }
            isAvailable = false
            isStale = !servers.isEmpty
            if let monitorError = error as? LocalServerMonitorError, case let .unsupported(message) = monitorError {
                isSupported = false
                errorMessage = message
            } else {
                errorMessage = "Could not refresh local servers. The last successful list is shown; AgentTelemetry will retry."
            }
        }
    }

    func stop(_ server: LocalServer) async throws {
        try await mutate("stop", body: ["id": server.id, "confirmed": true], timeout: 15)
    }

    func configure(_ server: LocalServer, command: String) async throws {
        try await mutate("configure", body: ["id": server.id, "command": command], timeout: 10)
    }

    func setPinned(_ server: LocalServer, pinned: Bool) async throws {
        try await mutate("pin", body: ["id": server.id, "pinned": pinned], timeout: 10)
    }

    func start(_ server: LocalServer) async throws {
        guard let profileID = server.profileID else { throw LocalServerMonitorError.profileUnavailable }
        try await mutate("start", body: ["profileId": profileID, "confirmed": true], timeout: 35)
    }

    func restart(_ server: LocalServer) async throws {
        guard let profileID = server.profileID else { throw LocalServerMonitorError.profileUnavailable }
        try await mutate("restart", body: [
            "profileId": profileID,
            "serverId": server.id,
            "confirmed": true,
        ], timeout: 60)
    }

    private func backendStatusDidChange(_ status: BackendStatus) {
        let wasReady = backendReady
        backendReady = status.isReady
        if !backendReady {
            invalidateRequests()
            isAvailable = false
            isStale = !servers.isEmpty
            if let message = status.message {
                errorMessage = message
            } else if status == .stopped {
                errorMessage = "Start monitoring to view local servers."
            } else {
                errorMessage = "The local dashboard is unavailable. Retry monitoring to view local servers."
            }
            return
        }
        if !wasReady {
            Task { await refresh() }
        }
    }

    private func backendRefreshDidSucceed(_ generation: Int) {
        guard generation > 0, backendReady else { return }
        Task { await refresh() }
    }

    private func mutate(_ action: String, body: [String: Any], timeout: TimeInterval) async throws {
        guard backendReady, let backend else { throw LocalServerMonitorError.notReady }
        guard isAvailable, isSupported == true else {
            throw LocalServerMonitorError.requestFailed("Refresh local servers before managing them.")
        }
        guard !actionInFlight else { throw LocalServerMonitorError.requestFailed("Another local server action is already running.") }
        requestGeneration += 1
        let attempt = requestGeneration
        actionInFlight = true
        isRefreshing = false
        defer {
            if requestGeneration == attempt { actionInFlight = false }
        }
        do {
            try await post(action, body: body, backend: backend, timeout: timeout)
            guard !Task.isCancelled else { throw CancellationError() }
            guard requestGeneration == attempt, backendReady else { throw LocalServerMonitorError.notReady }
            actionInFlight = false
            await refresh()
        } catch {
            guard !Task.isCancelled, requestGeneration == attempt, backendReady else { throw error }
            actionInFlight = false
            let surfacedError = actionFailure(error)
            if let monitorError = surfacedError as? LocalServerMonitorError, case let .unsupported(message) = monitorError {
                isSupported = false
                isAvailable = false
                isStale = !servers.isEmpty
                errorMessage = message
            } else {
                await refresh()
            }
            throw surfacedError
        }
    }

    private func fetchFromBackend(_ backend: BackendController) async throws -> LocalServerPayload {
        var request = URLRequest(url: backend.dashboardURL.appendingPathComponent("api/local-servers"))
        // The backend bounds three process-table commands and both inventory
        // locks to one second each; leave room for scheduling without letting
        // the native UI wait indefinitely.
        request.timeoutInterval = 20
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw LocalServerMonitorError.requestFailed("The local dashboard returned no response.")
        }
        if http.statusCode == 404 {
            throw LocalServerMonitorError.unsupported("The running dashboard is too old to show local servers. Update AgentTelemetry and retry.")
        }
        guard http.statusCode == 200 else {
            throw LocalServerMonitorError.requestFailed(responseMessage(data, fallback: "Could not refresh local servers."))
        }
        do {
            return try JSONDecoder().decode(LocalServerPayload.self, from: data)
        } catch {
            throw LocalServerMonitorError.unsupported("The running dashboard returned an unsupported local server response. Update AgentTelemetry and retry.")
        }
    }

    private func post(_ action: String, body: [String: Any], backend: BackendController, timeout: TimeInterval) async throws {
        var request = URLRequest(url: backend.dashboardURL.appendingPathComponent("api/local-servers/\(action)"))
        request.httpMethod = "POST"
        request.timeoutInterval = timeout
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw LocalServerMonitorError.requestFailed("The local dashboard returned no response.")
        }
        if http.statusCode == 404 {
            throw LocalServerMonitorError.unsupported("The running dashboard is too old to manage local servers. Update AgentTelemetry and retry.")
        }
        guard http.statusCode == 200 else {
            throw LocalServerMonitorError.requestFailed(responseMessage(data, fallback: "Could not \(action) that server."))
        }
    }

    private func responseMessage(_ data: Data, fallback: String) -> String {
        guard let body = try? JSONSerialization.jsonObject(with: data) as? [String: String],
              let message = body["error"] else { return fallback }
        return message
    }

    private func actionFailure(_ error: Error) -> Error {
        guard let urlError = error as? URLError, urlError.code == .timedOut else { return error }
        return LocalServerMonitorError.requestFailed(
            "The request timed out. Refresh the local server list to check its current state before trying again."
        )
    }

    private func invalidateRequests() {
        requestGeneration += 1
        actionInFlight = false
        isRefreshing = false
        refreshPending = false
    }

}

private enum LocalServerMonitorError: LocalizedError {
    case notReady
    case unsupported(String)
    case requestFailed(String)
    case profileUnavailable

    var errorDescription: String? {
        switch self {
        case .notReady: return "Start monitoring before managing local servers."
        case let .unsupported(message), let .requestFailed(message): return message
        case .profileUnavailable: return "Configure a launch command for this server first."
        }
    }
}
