import Combine
import Foundation

func dashboardURLSelecting(view: String, range: String? = nil, from baseURL: URL) -> URL? {
    guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else { return nil }
    if let range {
        var items = components.queryItems ?? []
        items.removeAll { $0.name == "range" }
        items.append(URLQueryItem(name: "range", value: range))
        components.queryItems = items
    }
    components.fragment = view
    return components.url
}

private final class NativeActivityRequestCancellation: @unchecked Sendable {
    private let lock = NSLock()
    private var task: URLSessionDataTask?
    private var cancelled = false

    func install(_ task: URLSessionDataTask) {
        lock.lock()
        self.task = task
        let shouldCancel = cancelled
        lock.unlock()
        if shouldCancel { task.cancel() }
    }

    func cancel() {
        lock.lock()
        cancelled = true
        let task = self.task
        lock.unlock()
        task?.cancel()
    }
}

private final class NativeActivityHTTPDelegate: NSObject, URLSessionDataDelegate, URLSessionTaskDelegate {
    static let maximumBytes = 64 * 1024
    private var response: URLResponse?
    private var bodyData = Data()
    var completion: ((Result<(Data, URLResponse), Error>) -> Void)?

    func urlSession(_ session: URLSession,
                    dataTask: URLSessionDataTask,
                    didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        if response.expectedContentLength > Int64(Self.maximumBytes) {
            completionHandler(.cancel)
            finish(.failure(NativeActivityMonitorError.responseTooLarge))
            return
        }
        self.response = response
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard bodyData.count + data.count <= Self.maximumBytes else {
            dataTask.cancel()
            finish(.failure(NativeActivityMonitorError.responseTooLarge))
            return
        }
        bodyData.append(data)
    }

    func urlSession(_ session: URLSession,
                    task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        // This endpoint is strictly local. Never follow a redirect to another host.
        completionHandler(nil)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let error {
            finish(.failure(error))
        } else if let response {
            finish(.success((bodyData, response)))
        } else {
            finish(.failure(NativeActivityMonitorError.requestFailed))
        }
    }

    private func finish(_ result: Result<(Data, URLResponse), Error>) {
        let callback = completion
        completion = nil
        callback?(result)
    }
}

private enum NativeActivityHTTP {
    static func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        let cancellation = NativeActivityRequestCancellation()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<(Data, URLResponse), Error>) in
                let configuration = URLSessionConfiguration.ephemeral
                configuration.timeoutIntervalForRequest = 8
                configuration.timeoutIntervalForResource = 8
                let delegate = NativeActivityHTTPDelegate()
                let session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
                delegate.completion = { result in
                    continuation.resume(with: result)
                    session.finishTasksAndInvalidate()
                }
                let task = session.dataTask(with: request)
                cancellation.install(task)
                task.resume()
            }
        } onCancel: {
            cancellation.cancel()
        }
    }
}

struct NativeActivitySession: Decodable, Identifiable, Equatable, Sendable {
    let key: String
    let source: String
    let tool: String
    let provider: String?
    let title: String
    let tokens: Int?
    let lastActivityAt: String
    let subagent: Bool

    var id: String { key }

    private enum CodingKeys: String, CodingKey {
        case key, source, tool, provider, title, tokens, subagent
        case lastActivityAt = "last_activity_at"
    }

    var activityDate: Date? { Self.parseISO8601(lastActivityAt) }

    var safeTitle: String {
        let scalars = title.unicodeScalars.map { CharacterSet.controlCharacters.contains($0) ? " " : String($0) }
        let cleaned = scalars.joined().split(whereSeparator: \.isWhitespace).joined(separator: " ")
        let bounded = String(cleaned.prefix(120))
        return bounded.isEmpty ? "Untitled session" : bounded
    }

    var displayTool: String {
        Self.brands[source]?.tool ?? String(tool.prefix(48))
    }

    var displayProvider: String? {
        guard let brand = Self.brands[source], let provider = brand.provider,
              provider.localizedCaseInsensitiveCompare(displayTool) != .orderedSame else { return nil }
        // The backend's source-to-provider mapping is fixed. Use that value so
        // a malformed or future provider label cannot impersonate another tool.
        return provider
    }

    var safeTokens: Int? {
        guard let tokens, tokens >= 0 else { return nil }
        return tokens
    }

    func ageLabel(at date: Date) -> String {
        guard let activityDate else { return "Time unavailable" }
        let seconds = max(0, Int(date.timeIntervalSince(activityDate)))
        if seconds < 60 { return "Just now" }
        return "\(seconds / 60)m ago"
    }

    fileprivate static func parseISO8601(_ value: String) -> Date? {
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let result = fractional.date(from: value) { return result }
        let standard = ISO8601DateFormatter()
        standard.formatOptions = [.withInternetDateTime]
        return standard.date(from: value)
    }

    private static let brands: [String: (tool: String, provider: String?)] = [
        "claude": ("Claude Code", "Anthropic"),
        "claude-desktop": ("Claude Desktop", "Anthropic"),
        "codex": ("Codex", "OpenAI"),
        "copilot": ("Copilot", "GitHub"),
        "cursor": ("Cursor", "Cursor"),
        "opencode": ("OpenCode", nil),
        "hermes": ("Hermes Agent", nil),
        "openclaw": ("OpenClaw", nil),
    ]
}

private struct NativeActivityPayload: Decodable, Sendable {
    let observedAt: String
    let windowSeconds: Int
    let sessions: [NativeActivitySession]
    let total: Int

    private enum CodingKeys: String, CodingKey {
        case sessions, total
        case observedAt = "observed_at"
        case windowSeconds = "window_seconds"
    }
}

@MainActor
final class NativeActivityMonitor: ObservableObject {
    @Published private(set) var sessions: [NativeActivitySession] = []
    @Published private(set) var total = 0
    @Published private(set) var observedAt: Date?
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
    private var refreshPending = false
    private var requestTask: Task<Void, Never>?

    var visibleSessions: [NativeActivitySession] {
        visibleSessions(at: Date())
    }

    func visibleSessions(at now: Date) -> [NativeActivitySession] {
        sessions.filter { session in
            guard let date = session.activityDate else { return false }
            let age = now.timeIntervalSince(date)
            return age >= 0 && age <= 900
        }
    }

    func connect(to backend: BackendController) {
        guard self.backend !== backend else { return }
        statusSubscription?.cancel()
        refreshSubscription?.cancel()
        invalidateRequests()
        self.backend = backend

        statusSubscription = backend.$status.sink { [weak self] status in
            // @Published emits in willSet; use the incoming status value.
            Task { @MainActor [weak self] in
                self?.backendStatusDidChange(status)
            }
        }
        refreshSubscription = backend.$refreshGeneration
            .dropFirst()
            .sink { [weak self] generation in
                // The summary generation follows a successful shared-clock
                // fetch. Reuse it instead of polling /api/sync a second time.
                Task { @MainActor [weak self] in
                    self?.backendRefreshDidSucceed(generation)
                }
            }
    }

    func refresh() {
        guard backendReady, let backend else { return }
        guard requestTask == nil else {
            refreshPending = true
            return
        }

        requestGeneration += 1
        let attempt = requestGeneration
        refreshPending = false
        isRefreshing = true
        requestTask = Task { [weak self] in
            await self?.fetchSnapshot(from: backend, generation: attempt)
        }
    }

    private func backendStatusDidChange(_ status: BackendStatus) {
        let wasReady = backendReady
        backendReady = status.isReady
        if !backendReady {
            invalidateRequests()
            isAvailable = false
            if status == .stopped {
                // A stopped service must not leave the menu implying that old
                // sessions are still live.
                sessions = []
                total = 0
                observedAt = nil
                isStale = false
                isSupported = nil
                errorMessage = "Start monitoring to view recently active sessions."
            } else {
                isStale = !sessions.isEmpty
                errorMessage = status.message ?? "The local dashboard is unavailable. The last successful activity list is shown."
            }
            return
        }

        if !wasReady {
            refresh()
        }
    }

    private func backendRefreshDidSucceed(_ generation: Int) {
        guard generation > 0, backendReady else { return }
        refresh()
    }

    private func fetchSnapshot(from backend: BackendController, generation attempt: Int) async {
        defer { finishRequest(generation: attempt) }
        var request = URLRequest(url: backend.dashboardURL.appendingPathComponent("api/native-activity"))
        request.timeoutInterval = 8
        do {
            guard let requestURL = request.url,
                  let components = URLComponents(url: requestURL, resolvingAgainstBaseURL: false),
                  components.scheme == "http", components.host == "127.0.0.1",
                  components.user == nil, components.password == nil else {
                throw NativeActivityMonitorError.requestFailed
            }
            let (data, response) = try await NativeActivityHTTP.data(for: request)
            guard !Task.isCancelled, requestGeneration == attempt, backendReady,
                  self.backend === backend else { return }
            guard let http = response as? HTTPURLResponse,
                  http.url?.scheme == "http", http.url?.host == "127.0.0.1",
                  http.url?.port == components.port else {
                throw NativeActivityMonitorError.requestFailed
            }
            if http.statusCode == 404 {
                throw NativeActivityMonitorError.unsupported
            }
            guard http.statusCode == 200 else {
                throw NativeActivityMonitorError.requestFailed
            }

            let payload: NativeActivityPayload
            do {
                payload = try JSONDecoder().decode(NativeActivityPayload.self, from: data)
            } catch {
                throw NativeActivityMonitorError.unsupported
            }
            guard payload.windowSeconds == 900,
                  payload.total >= 0,
                  payload.sessions.count <= 3,
                  payload.sessions.count == min(payload.total, 3),
                  let observed = NativeActivitySession.parseISO8601(payload.observedAt) else {
                throw NativeActivityMonitorError.unsupported
            }
            guard payload.sessions.allSatisfy({
                !$0.key.isEmpty && $0.key.count <= 128
                    && !$0.source.isEmpty && $0.source.count <= 80
                    && !$0.tool.isEmpty && $0.tool.count <= 80
                    && $0.title.count <= 512 && $0.lastActivityAt.count <= 128
                    && $0.activityDate != nil
            }) else {
                throw NativeActivityMonitorError.unsupported
            }
            guard !Task.isCancelled, requestGeneration == attempt, backendReady,
                  self.backend === backend else { return }
            sessions = payload.sessions
            total = payload.total
            observedAt = observed
            isAvailable = true
            isStale = false
            isSupported = true
            errorMessage = nil
        } catch {
            guard !Task.isCancelled, requestGeneration == attempt, backendReady,
                  self.backend === backend else { return }
            isAvailable = false
            isStale = !sessions.isEmpty
            if let monitorError = error as? NativeActivityMonitorError, monitorError == .unsupported {
                isSupported = false
                errorMessage = "Recently active sessions need a newer dashboard. Update AgentTelemetry and retry."
            } else {
                errorMessage = sessions.isEmpty
                    ? "Recently active sessions could not be loaded. AgentTelemetry will retry on its next refresh."
                    : "The last successful activity list is shown; AgentTelemetry will retry on its next refresh."
            }
        }
    }

    private func finishRequest(generation attempt: Int) {
        guard requestGeneration == attempt else { return }
        requestTask = nil
        isRefreshing = false
        if refreshPending && backendReady {
            refreshPending = false
            refresh()
        }
    }

    private func invalidateRequests() {
        requestGeneration += 1
        requestTask?.cancel()
        requestTask = nil
        isRefreshing = false
        refreshPending = false
    }
}

private enum NativeActivityMonitorError: Error, Equatable {
    case requestFailed
    case unsupported
    case responseTooLarge
}
