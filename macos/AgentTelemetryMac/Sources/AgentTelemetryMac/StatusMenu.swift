import AppKit
import SwiftUI

@MainActor
private final class DisclosureState: ObservableObject {
    @Published var expanded = false
}

struct StatusMenu: View {
    @ObservedObject var backend: BackendController
    @ObservedObject var settings: AppSettings
    let openDashboard: () -> Void
    let quit: () -> Void
    let onDisclosureChange: (Bool) -> Void

    @StateObject private var disclosure = DisclosureState()
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
            .frame(
                height: detailsExpanded ? 220 : (backend.status.message == nil ? 44 : 112),
                alignment: .top
            )
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
    }

    private var header: some View {
        HStack(spacing: 9) {
            Image(systemName: "chart.bar.fill")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(Color.accentColor)
                .frame(width: 28, height: 28)
                .background(Color.accentColor.opacity(0.1), in: RoundedRectangle(cornerRadius: 8))

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
