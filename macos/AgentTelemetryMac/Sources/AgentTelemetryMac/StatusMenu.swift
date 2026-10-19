import AppKit
import SwiftUI

private enum LocalServerAction: Equatable {
    case start
    case stop
    case restart

    func title(for server: LocalServer?) -> String {
        let process = server?.process ?? "server"
        switch self {
        case .start: return "Start \(process)?"
        case .stop: return "Stop \(process)?"
        case .restart: return "Restart \(process)?"
        }
    }

    var buttonTitle: String {
        switch self {
        case .start: return "Start server"
        case .stop: return "Stop server"
        case .restart: return "Restart server"
        }
    }
}

@MainActor
private final class StatusMenuState: ObservableObject {
    @Published var expanded = false
    @Published var recentServersExpanded = false
    @Published var pendingServer: LocalServer?
    @Published var pendingAction: LocalServerAction?
    @Published var showServerActionConfirmation = false
    @Published var actionError: String?
    @Published var showActionError = false
    @Published var busyServerID: String?
}

@MainActor
final class MenuBarPopoverLayout: ObservableObject {
    @Published var maximumScrollHeight: CGFloat = 400
    @Published var detailsExpanded = false

    func constrainScrollHeight(availableHeight: CGFloat, fixedHeight: CGFloat) {
        let desiredHeight: CGFloat = detailsExpanded ? 400 : 360
        maximumScrollHeight = max(0, min(desiredHeight, availableHeight - fixedHeight))
    }
}

struct StatusMenu: View {
    @ObservedObject var backend: BackendController
    @ObservedObject var settings: AppSettings
    @ObservedObject var localServers: LocalServerMonitor
    @ObservedObject var recentActivity: NativeActivityMonitor
    @ObservedObject var layout: MenuBarPopoverLayout
    let openDashboard: () -> Void
    let openLocalServers: () -> Void
    let openRecentSessions: () -> Void
    let quit: () -> Void
    let onDisclosureChange: (Bool) -> Void

    @StateObject private var disclosure = StatusMenuState()
    private var detailsExpanded: Bool {
        get { disclosure.expanded }
        nonmutating set { disclosure.expanded = newValue }
    }

    private var monitoringBinding: Binding<Bool> {
        Binding(
            get: { backend.isMonitoring },
            set: { enabled in enabled ? backend.start() : backend.stop() }
        )
    }

    private var loginBinding: Binding<Bool> {
        Binding(
            get: { settings.openAtLogin },
            set: { settings.setOpenAtLogin($0) }
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            todaySummary
                .padding(.top, 12)

            ScrollView(.vertical) {
                VStack(alignment: .leading, spacing: 12) {
                    if let message = backend.status.message {
                        Label(message, systemImage: "exclamationmark.triangle.fill")
                            .font(.caption)
                            .foregroundStyle(.orange)
                            .fixedSize(horizontal: false, vertical: true)
                        Button("Retry") { backend.start() }
                            .buttonStyle(.bordered)
                    }

                    recentActivitySection
                    localServersSection

                    Button {
                        detailsExpanded.toggle()
                        onDisclosureChange(detailsExpanded)
                    } label: {
                        HStack(spacing: 7) {
                            Image(systemName: detailsExpanded ? "chevron.down" : "chevron.right")
                                .font(.system(size: 11, weight: .semibold))
                                .frame(width: 14)
                            Text("Details & settings")
                                .font(.caption.weight(.semibold))
                            Spacer(minLength: 0)
                        }
                        .frame(maxWidth: .infinity, minHeight: 32, alignment: .leading)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityValue(detailsExpanded ? "Expanded" : "Collapsed")
                    .accessibilityHint(detailsExpanded ? "Hide details and settings" : "Show details and settings")

                    if detailsExpanded {
                        settingsContent
                            .padding(.leading, 21)
                            .transition(.opacity.combined(with: .move(edge: .top)))
                    }
                }
                .padding(.top, 12)
            }
            .frame(height: min(detailsExpanded ? 400 : 360, layout.maximumScrollHeight), alignment: .top)
            .scrollIndicators(.automatic)

            Button(action: openDashboard) {
                HStack(spacing: 7) {
                    Spacer()
                    Text("Open dashboard")
                    Image(systemName: "arrow.up.right")
                    Spacer()
                }
                .padding(.vertical, 2)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.regular)
            .padding(.top, 8)

            HStack {
                Button(action: { backend.refreshNow() }) {
                    Label("Refresh now", systemImage: "arrow.clockwise")
                }
                .buttonStyle(.plain)
                .disabled(!backend.isReady)
                Spacer()
                Button(role: .destructive, action: quit) {
                    Label("Quit AgentTelemetry", systemImage: "power")
                }
                .buttonStyle(.plain)
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            .padding(.top, 8)
        }
        .padding(14)
        .frame(width: 310)
        .onChange(of: settings.refreshIntervalSeconds) { _ in
            backend.refreshIntervalDidChange()
        }
        .confirmationDialog(
            disclosure.pendingAction?.title(for: disclosure.pendingServer) ?? "Manage server?",
            isPresented: $disclosure.showServerActionConfirmation,
            titleVisibility: .visible
        ) {
            Button(disclosure.pendingAction?.buttonTitle ?? "Stop server",
                   role: disclosure.pendingAction == .start ? nil : .destructive) {
                performPendingAction()
            }
            Button("Cancel", role: .cancel) { clearPendingAction() }
        } message: {
            if let server = disclosure.pendingServer, let action = disclosure.pendingAction {
                if action == .start {
                    Text("AgentTelemetry will run the saved launch command in \(server.project.isEmpty ? server.cwd : server.project). This executes the command you saved for this server.")
                } else if action == .restart {
                    Text("AgentTelemetry will stop \(server.process) (PID \(server.pid)), then run its saved launch command.")
                } else {
                    Text("AgentTelemetry will send SIGTERM to \(server.process) (PID \(server.pid)). This may stop every port owned by that process.")
                }
            }
        }
        .alert("Server action failed", isPresented: $disclosure.showActionError) {
            Button("OK", role: .cancel) { }
        } message: {
            Text(disclosure.actionError ?? "Please refresh and try again.")
        }
    }

    private var localServersSection: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 6) {
                Text("Local servers")
                    .font(.system(size: 10, weight: .semibold))
                Spacer(minLength: 4)
                Text(localServers.isAvailable
                     ? "\(localServers.runningServers.count) running"
                     : (localServers.servers.isEmpty ? "Unavailable" : "Last known state"))
                    .font(.system(size: 9, weight: .medium).monospacedDigit())
                    .foregroundStyle(.secondary)
                Button {
                    Task { await localServers.refresh() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 9, weight: .medium))
                }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
                .help("Refresh local servers")
                .accessibilityLabel("Scan local servers")
                .disabled(!backend.isReady || localServers.isRefreshing)
            }

            if let message = localServers.errorMessage {
                Text(message)
                    .font(.system(size: 9))
                    .foregroundStyle(localServers.isStale ? Color.orange : Color.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if localServers.runningServers.isEmpty && featuredServers.isEmpty {
                if localServers.isRefreshing {
                    Text("Loading local servers…")
                        .font(.system(size: 9))
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 3)
                } else if localServers.errorMessage == nil {
                    Text("No local servers are running.")
                        .font(.system(size: 9))
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 3)
                }
            }

            ForEach(featuredServers) { server in
                serverRow(server)
            }

            if !recentServers.isEmpty {
                Button {
                    disclosure.recentServersExpanded.toggle()
                } label: {
                    HStack {
                        Text("\(recentServers.count) stopped recently")
                        Spacer()
                        Image(systemName: disclosure.recentServersExpanded ? "chevron.down" : "chevron.right")
                    }
                    .font(.system(size: 9, weight: .medium))
                    .foregroundStyle(.secondary)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                if disclosure.recentServersExpanded {
                    ForEach(recentServers.prefix(2)) { server in
                        HStack {
                            Text("\(server.location) · \(server.displayDetails)")
                                .lineLimit(1)
                            Spacer(minLength: 4)
                            Text(server.stoppedAt.map(shortTime) ?? "Stopped")
                                .lineLimit(1)
                            if server.isConfigured {
                                Button {
                                    confirmAction(.start, for: server)
                                } label: {
                                    Image(systemName: "play.fill")
                                }
                                .buttonStyle(.plain)
                                .foregroundStyle(Color.accentColor)
                                .help("Start \(server.location)")
                                .accessibilityLabel("Start \(server.location)")
                                .disabled(disclosure.busyServerID != nil || !localServers.isAvailable)
                            }
                            Button {
                                Task { await togglePin(server) }
                            } label: {
                                Image(systemName: "pin")
                            }
                            .buttonStyle(.plain)
                            .help("Pin \(server.location) to this menu")
                            .accessibilityLabel("Pin \(server.location) to menu bar popover")
                            .disabled(disclosure.busyServerID != nil || !localServers.isAvailable)
                        }
                        .font(.system(size: 9))
                        .foregroundStyle(.secondary)
                    }
                }
            }

            Button(action: openLocalServers) {
                HStack {
                    Text("Manage all servers")
                    Image(systemName: "arrow.up.right")
                }
                .font(.system(size: 9, weight: .semibold))
                .frame(maxWidth: .infinity, alignment: .center)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.accentColor)
            .padding(.top, 1)
        }
        .padding(10)
        .background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 9))
        .overlay(RoundedRectangle(cornerRadius: 9).stroke(Color.primary.opacity(0.07)))
    }

    private var recentActivitySection: some View {
        TimelineView(.periodic(from: Date(), by: 30)) { timeline in
            let visible = recentActivity.visibleSessions(at: timeline.date)
            VStack(alignment: .leading, spacing: 7) {
                HStack(spacing: 6) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Recently active")
                            .font(.system(size: 10, weight: .semibold))
                            .help("Shows sessions with recent log activity; this does not confirm that a tool is still running.")
                            .accessibilityHint("Sessions are included when their logs record recent activity. This does not confirm a tool is still running.")
                        Text("Last 15 min")
                            .font(.system(size: 9))
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 4)
                    Text(activityCountLabel(visible: visible))
                        .font(.system(size: 9, weight: .medium).monospacedDigit())
                        .foregroundStyle(.secondary)
                    Button {
                        recentActivity.refresh()
                    } label: {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 9, weight: .medium))
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
                    .help("Refresh recent activity")
                    .accessibilityLabel("Refresh recently active sessions")
                    .disabled(!backend.isReady || recentActivity.isRefreshing)
                }

                if recentActivity.isStale, let message = recentActivity.errorMessage {
                    Label(message, systemImage: "exclamationmark.triangle.fill")
                        .font(.system(size: 9))
                        .foregroundStyle(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                }

                if visible.isEmpty {
                    if recentActivity.isRefreshing && recentActivity.sessions.isEmpty {
                        HStack(spacing: 6) {
                            ProgressView().controlSize(.small)
                            Text("Checking local activity…")
                                .font(.system(size: 9))
                                .foregroundStyle(.secondary)
                        }
                        .padding(.vertical, 3)
                    } else if let message = recentActivity.errorMessage, !recentActivity.isAvailable {
                        Text(message)
                            .font(.system(size: 9))
                            .foregroundStyle(recentActivity.isSupported == false ? .orange : .secondary)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.vertical, 2)
                    } else {
                        Text("No sessions have logged activity in the last 15 minutes.")
                            .font(.system(size: 9))
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.vertical, 2)
                    }
                } else {
                    ForEach(visible) { session in
                        recentActivityRow(session, now: timeline.date)
                    }
                }

                Button(action: openRecentSessions) {
                    HStack(spacing: 5) {
                        Text("View sessions")
                        Image(systemName: "arrow.up.right")
                            .font(.system(size: 8, weight: .semibold))
                    }
                    .font(.system(size: 9, weight: .semibold))
                    .frame(maxWidth: .infinity, alignment: .center)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .foregroundStyle(Color.accentColor)
                .padding(.top, 1)
                .help("Open the Sessions view for the last 7 days")
            }
            .padding(10)
            .background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 9))
            .overlay(RoundedRectangle(cornerRadius: 9).stroke(Color.primary.opacity(0.07)))
        }
    }

    private func recentActivityRow(_ session: NativeActivitySession, now: Date) -> some View {
        HStack(alignment: .top, spacing: 7) {
            Image(systemName: session.subagent ? "person.2.wave.2.fill" : "terminal.fill")
                .font(.system(size: 10, weight: .medium))
                .foregroundStyle(session.subagent ? Color.accentColor : Color.secondary)
                .frame(width: 15, alignment: .center)
                .padding(.top, 1)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 4) {
                    Text(session.displayTool)
                        .font(.system(size: 10, weight: .semibold))
                        .lineLimit(1)
                    if let provider = session.displayProvider {
                        Text(provider)
                            .font(.system(size: 9))
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .help("Tool vendor; not the provider that billed a particular request.")
                    }
                    if session.subagent {
                        Text("Subagent")
                            .font(.system(size: 8, weight: .semibold))
                            .foregroundStyle(Color.accentColor)
                            .padding(.horizontal, 4)
                            .padding(.vertical, 1)
                            .background(Color.accentColor.opacity(0.1), in: Capsule())
                    }
                }
                Text(session.safeTitle)
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .accessibilityLabel(session.safeTitle)
                    .help(session.safeTitle)
                HStack(spacing: 4) {
                    Text(session.ageLabel(at: now))
                    Text("·")
                    if let tokens = session.safeTokens {
                        Text("\(compactTokens(tokens)) session tokens")
                            .help("Lifetime recorded session tokens, including input, output, cache reads and writes. This is not current context or recent-window usage.")
                    } else {
                        Text("Session tokens unavailable")
                            .help("Lifetime session token totals were unavailable in the source logs.")
                    }
                }
                .font(.system(size: 9, weight: .medium).monospacedDigit())
                .foregroundStyle(.secondary)
                .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(activityAccessibilityLabel(session, now: now))
        .accessibilityHint("Session token values are lifetime recorded totals, not current context usage.")
        .help(session.safeTitle)
        .padding(.vertical, 2)
    }

    private func activityAccessibilityLabel(_ session: NativeActivitySession, now: Date) -> String {
        let provider = session.displayProvider.map { " · \($0)" } ?? ""
        let agent = session.subagent ? " · Subagent" : ""
        let tokens = session.safeTokens.map { " · \(compactTokens($0)) session tokens" } ?? " · session tokens unavailable"
        return "\(session.displayTool)\(provider)\(agent) · \(session.safeTitle) · \(session.ageLabel(at: now))\(tokens)"
    }

    private func activityCountLabel(visible: [NativeActivitySession]) -> String {
        if visible.count == recentActivity.sessions.count && visible.count == 3 {
            return "\(max(3, recentActivity.total)) sessions"
        }
        return "\(visible.count) \(visible.count == 1 ? "session" : "sessions")"
    }

    private var featuredServers: [LocalServer] {
        let sorted = localServers.servers.sorted {
            if $0.isPinned != $1.isPinned { return $0.isPinned }
            if $0.isRunning != $1.isRunning { return $0.isRunning }
            return $0.lastSeen > $1.lastSeen
        }
        let pinned = sorted.filter(\.isPinned)
        let live = sorted.filter { $0.isRunning && !$0.isPinned }
        return pinned + Array(live.prefix(max(0, 3 - pinned.count)))
    }

    private var recentServers: [LocalServer] {
        localServers.recentlyStopped.filter { !$0.isPinned }
    }

    @ViewBuilder
    private func serverRow(_ server: LocalServer) -> some View {
        HStack(spacing: 7) {
            Circle()
                .fill(server.isRunning ? Color.green : Color.secondary.opacity(0.65))
                .frame(width: 6, height: 6)
            VStack(alignment: .leading, spacing: 2) {
                Text(server.location)
                    .font(.system(size: 10, weight: .semibold))
                    .lineLimit(1)
                Text(server.displayDetails)
                    .font(.system(size: 9))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 1)

            if disclosure.busyServerID == server.id {
                ProgressView().controlSize(.small)
            } else if server.isRunning {
                if server.isConfigured {
                    Button {
                        confirmAction(.restart, for: server)
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .help("Restart \(server.location)")
                    .accessibilityLabel("Restart \(server.location)")
                } else {
                    Button("Configure") { openLocalServers() }
                        .font(.system(size: 9, weight: .medium))
                        .help("Save a launch command in Local servers")
                }
                Button {
                    confirmAction(.stop, for: server)
                } label: {
                    Image(systemName: "stop.fill")
                }
                .help("Stop \(server.location)")
                .accessibilityLabel("Stop \(server.location)")
            } else if server.isConfigured {
                Button {
                    confirmAction(.start, for: server)
                } label: {
                    Image(systemName: "play.fill")
                }
                .help("Start \(server.location)")
                .accessibilityLabel("Start \(server.location)")
            } else {
                Button("Configure") { openLocalServers() }
                    .font(.system(size: 9, weight: .medium))
                    .help("Save a launch command in Local servers")
            }

            Button {
                Task { await togglePin(server) }
            } label: {
                Image(systemName: server.isPinned ? "pin.fill" : "pin")
            }
            .help(server.isPinned ? "Unpin \(server.location)" : "Pin \(server.location) to this menu")
            .accessibilityLabel(server.isPinned ? "Unpin \(server.location)" : "Pin \(server.location) to menu bar popover")
            .buttonStyle(.plain)
            .foregroundStyle(server.isPinned ? Color.accentColor : Color.secondary)
            .disabled(disclosure.busyServerID != nil)
        }
        .buttonStyle(.plain)
        .font(.system(size: 9, weight: .medium))
        .foregroundStyle(.secondary)
        .padding(.vertical, 3)
        .disabled(disclosure.busyServerID != nil || !localServers.isAvailable)
    }

    private func confirmAction(_ action: LocalServerAction, for server: LocalServer) {
        disclosure.pendingAction = action
        disclosure.pendingServer = server
        disclosure.showServerActionConfirmation = true
    }

    private func performPendingAction() {
        guard let action = disclosure.pendingAction, let server = disclosure.pendingServer else { return }
        Task { await runAction(action, for: server) }
        clearPendingAction()
    }

    private func clearPendingAction() {
        disclosure.pendingAction = nil
        disclosure.pendingServer = nil
    }

    private func runAction(_ action: LocalServerAction, for server: LocalServer) async {
        guard disclosure.busyServerID == nil else { return }
        disclosure.busyServerID = server.id
        defer { disclosure.busyServerID = nil }
        do {
            switch action {
            case .start: try await localServers.start(server)
            case .stop: try await localServers.stop(server)
            case .restart: try await localServers.restart(server)
            }
        } catch {
            disclosure.actionError = error.localizedDescription
            disclosure.showActionError = true
        }
    }

    private func togglePin(_ server: LocalServer) async {
        guard disclosure.busyServerID == nil else { return }
        disclosure.busyServerID = server.id
        defer { disclosure.busyServerID = nil }
        do {
            try await localServers.setPinned(server, pinned: !server.isPinned)
        } catch {
            disclosure.actionError = error.localizedDescription
            disclosure.showActionError = true
        }
    }

    private func shortTime(_ stamp: String) -> String {
        let formatter = ISO8601DateFormatter()
        guard let date = formatter.date(from: stamp) else { return "Stopped" }
        return date.formatted(date: .omitted, time: .shortened)
    }

    private var header: some View {
        HStack(spacing: 9) {
            Image(nsImage: AgentTelemetryAppIcon.image)
                .resizable()
                .interpolation(.high)
                .frame(width: 28, height: 28)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 3) {
                Text("AgentTelemetry")
                    .font(.system(size: 12, weight: .semibold))
                Label(backend.status.title, systemImage: backend.status.symbol)
                    .font(.system(size: 9))
                    .foregroundStyle(statusColor)
            }
            Spacer(minLength: 8)
            Toggle("Monitoring", isOn: monitoringBinding)
                .labelsHidden()
                .toggleStyle(.switch)
                .disabled(backend.status == .starting || backend.status == .stopping)
                .accessibilityLabel(backend.isMonitoring ? "Stop monitoring" : "Start monitoring")
        }
        .padding(.bottom, 2)
    }

    private var todaySummary: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("TODAY")
                .font(.system(size: 8, weight: .bold))
                .tracking(1)
                .foregroundStyle(.secondary)
            if backend.summaryStale {
                Text("Last successful refresh; figures may be out of date")
                    .font(.caption2).foregroundStyle(.secondary)
            }
            HStack(alignment: .center, spacing: 0) {
                metric(title: "Tokens used", value: summaryValue(tokens: true))
                Divider().frame(height: 34).padding(.horizontal, 12)
                metric(title: "Estimated spend", value: summaryValue(tokens: false))
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 9))
        .overlay(RoundedRectangle(cornerRadius: 9).stroke(Color.primary.opacity(0.07)))
    }

    private func metric(title: String, value: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.system(size: 9))
                .foregroundStyle(.secondary)
            Text(value)
                .font(.system(size: 16, weight: .semibold, design: .rounded).monospacedDigit())
                .lineLimit(1)
                .minimumScaleFactor(0.8)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var settingsContent: some View {
        VStack(alignment: .leading, spacing: 10) {
            Picker("Menu bar display", selection: $settings.menuBarDisplay) {
                ForEach(AppSettings.MenuBarDisplay.allCases) { mode in
                    Text(mode.title).tag(mode)
                }
            }
            .pickerStyle(.segmented)

            Picker("Shared refresh", selection: $settings.refreshIntervalSeconds) {
                Text("15 sec").tag(15)
                Text("1 min").tag(60)
                Text("5 min").tag(300)
                Text("15 min").tag(900)
                Text("Manual").tag(0)
            }
            .disabled(!backend.isMonitoring || backend.changingInterval)

            Toggle("Open at login", isOn: loginBinding)
            Toggle("Start monitoring automatically", isOn: $settings.startMonitoringAutomatically)
                .help("Starts the local service when AgentTelemetry opens.")

            if let message = settings.loginItemMessage {
                Text(message)
                    .font(.system(size: 9))
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Button(action: chooseImportFolder) {
                Label("Import existing data…", systemImage: "square.and.arrow.down")
            }
            .disabled(!backend.canImportExistingData)
            .help("Copy an existing AgentTelemetry cache into this app without changing the original.")
        }
        .controlSize(.small)
    }

    private func chooseImportFolder() {
        let panel = NSOpenPanel()
        panel.title = "Import existing AgentTelemetry data"
        panel.message = "Choose a folder containing .usage_cache.json. The original files stay in place."
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let source = panel.url else { return }
        backend.importExistingData(from: source)
    }

    private var statusColor: Color {
        switch backend.status {
        case .running, .connectedToExisting: return .green
        case .starting, .stopping: return .blue
        case .needsAttention: return .orange
        case .stopped: return .secondary
        }
    }

    private func compactTokens(_ value: Int) -> String {
        if value >= 1_000_000 { return String(format: "%.1fM", Double(value) / 1_000_000) }
        if value >= 1_000 { return String(format: "%.1fk", Double(value) / 1_000) }
        return value.formatted()
    }

    private func summaryValue(tokens: Bool) -> String {
        guard !backend.summary.date.isEmpty else { return "—" }
        return tokens
            ? compactTokens(backend.summary.tokens)
            : String(format: "$%.2f", backend.summary.spend)
    }
}
