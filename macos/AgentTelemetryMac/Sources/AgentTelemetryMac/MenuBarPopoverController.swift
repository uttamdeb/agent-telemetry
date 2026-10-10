import AppKit
import Combine
import SwiftUI

@MainActor
final class MenuBarPopoverController: NSObject, ObservableObject, NSPopoverDelegate {
    private var statusItem: NSStatusItem?
    private let popover = NSPopover()
    private var hostingController: NSHostingController<StatusMenu>?
    private var backend: BackendController?
    private var settings: AppSettings?
    private var subscriptions = Set<AnyCancellable>()

    func install(
        backend: BackendController,
        settings: AppSettings,
        openDashboard: @escaping () -> Void,
        quit: @escaping () -> Void
    ) {
        guard statusItem == nil else { return }

        self.backend = backend
        self.settings = settings

        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem = item
        item.button?.target = self
        item.button?.action = #selector(togglePopover(_:))
        item.button?.imagePosition = .imageOnly
        item.button?.imageScaling = .scaleProportionallyDown

        popover.behavior = .transient
        popover.animates = true
        popover.delegate = self

        let rootView = StatusMenu(
            backend: backend,
            settings: settings,
            openDashboard: { [weak self] in
                guard let self else { return }
                self.popover.performClose(nil)
                openDashboard()
            },
            quit: quit,
            onDisclosureChange: { [weak self] _ in self?.resizePopoverFromContent() }
        )
        let host = NSHostingController(rootView: rootView)
        hostingController = host
        popover.contentViewController = host

        backend.$status
            .sink { [weak self] status in self?.updateStatusItem(status: status) }
            .store(in: &subscriptions)
        backend.$summary
            .sink { [weak self] summary in self?.updateStatusItem(summary: summary) }
            .store(in: &subscriptions)
        settings.$menuBarDisplay
            .sink { [weak self] display in self?.updateStatusItem(display: display) }
            .store(in: &subscriptions)

        updateStatusItem()
    }

    @objc private func togglePopover(_ sender: Any?) {
        guard let button = statusItem?.button else { return }
        if popover.isShown {
            popover.performClose(sender)
            return
        }

        updatePopoverSize()
        button.highlight(true)
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
    }

    func popoverDidClose(_ notification: Notification) {
        statusItem?.button?.highlight(false)
    }

    private func resizePopoverFromContent() {
        DispatchQueue.main.async { [weak self] in
            guard let self, self.popover.isShown else { return }
            self.updatePopoverSize()
            guard let button = self.statusItem?.button else { return }
            self.popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        }
    }

    private func updatePopoverSize() {
        guard let host = hostingController else { return }
        host.view.layoutSubtreeIfNeeded()
        let fitted = host.view.fittingSize
        guard fitted.height.isFinite, fitted.height > 0 else { return }
        popover.contentSize = NSSize(width: 310, height: fitted.height)
    }

    private func updateStatusItem(display: AppSettings.MenuBarDisplay? = nil,
                                  summary: DailySummary? = nil, status: BackendStatus? = nil) {
        guard let button = statusItem?.button, let backend, let settings else { return }
        let display = display ?? settings.menuBarDisplay
        let summary = summary ?? backend.summary
        let status = status ?? backend.status
        button.title = ""
        button.toolTip = summary.date.isEmpty
            ? "AgentTelemetry; today's summary is not available"
            : "AgentTelemetry; \(summary.tokens) tokens today; estimated spend \(summary.spend) dollars"

        if display == .numbers {
            button.image = menuBarNumbersImage(for: summary)
            button.contentTintColor = nil
            return
        }

        let symbol: String
        switch status {
        case .starting, .stopping:
            symbol = "ellipsis.circle"
        case .needsAttention:
            symbol = "exclamationmark.triangle.fill"
        case .stopped:
            symbol = "chart.bar.fill"
        case .running, .connectedToExisting:
            symbol = "chart.bar.fill"
        }
        let configuration = NSImage.SymbolConfiguration(pointSize: 15, weight: .medium)
        button.image = NSImage(systemSymbolName: symbol, accessibilityDescription: "AgentTelemetry")?
            .withSymbolConfiguration(configuration)
        button.image?.size = NSSize(width: 15, height: 15)
        button.image?.isTemplate = true
        // Leave the template untinted so macOS chooses a contrasting
        // foreground for the current menu bar appearance.
        button.contentTintColor = nil
    }

    private func menuBarNumbersImage(for summary: DailySummary) -> NSImage {
        let tokenText = summary.date.isEmpty ? "—" : compactTokens(summary.tokens)
        let spendText = summary.date.isEmpty ? "—" : String(format: "$%.2f", summary.spend)
        let font = NSFont.monospacedDigitSystemFont(ofSize: 9, weight: .semibold)
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .right

        func line(_ text: String, color: NSColor) -> NSAttributedString {
            NSAttributedString(string: text, attributes: [
                .font: font,
                .foregroundColor: color,
                .paragraphStyle: paragraph
            ])
        }

        let tokenLine = line(tokenText, color: .labelColor)
        let spendLine = line(spendText, color: .secondaryLabelColor)
        // NSStatusItem sizes itself to the image. Keep only enough width for
        // the longer line so right alignment does not create a visible gutter.
        let width = ceil(max(tokenLine.size().width, spendLine.size().width)) + 1
        let size = NSSize(width: width, height: 22)
        let image = NSImage(size: size, flipped: false) { rect in
            NSColor.clear.setFill()
            rect.fill()
            tokenLine.draw(in: NSRect(x: 0, y: 11, width: size.width, height: 11))
            spendLine.draw(in: NSRect(x: 0, y: 0, width: size.width, height: 11))
            return true
        }
        image.isTemplate = false
        return image
    }

    private func compactTokens(_ value: Int) -> String {
        if value >= 1_000_000 { return String(format: "%.1fM", Double(value) / 1_000_000) }
        if value >= 1_000 { return String(format: "%.1fk", Double(value) / 1_000) }
        return value.formatted()
    }
}
