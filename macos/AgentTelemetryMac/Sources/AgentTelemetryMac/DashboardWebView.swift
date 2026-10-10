import AppKit
import SwiftUI
import WebKit

struct DashboardWebView: NSViewRepresentable {
    let url: URL
    let refreshIntervalSeconds: Int
    var onNavigate: ((URL) -> Void)? = nil

    private var pageURL: URL {
        guard var components = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return url }
        var items = components.queryItems ?? []
        items.removeAll { $0.name == "nativeApp" || $0.name == "nativePollSeconds" }
        items.append(URLQueryItem(name: "nativeApp", value: "1"))
        items.append(URLQueryItem(name: "nativePollSeconds", value: String(refreshIntervalSeconds)))
        components.queryItems = items
        return components.url ?? url
    }

    func makeCoordinator() -> Coordinator { Coordinator(origin: url, onNavigate: onNavigate) }

    func makeNSView(context: Context) -> WKWebView {
        let view = WKWebView(frame: .zero)
        view.navigationDelegate = context.coordinator
        context.coordinator.observe(view)
        view.load(URLRequest(url: pageURL))
        return view
    }

    func updateNSView(_ view: WKWebView, context: Context) {
        guard let current = view.url,
              var components = URLComponents(url: current, resolvingAgainstBaseURL: false) else { return }
        var items = components.queryItems ?? []
        if items.first(where: { $0.name == "nativePollSeconds" })?.value == String(refreshIntervalSeconds) { return }
        items.removeAll { $0.name == "nativeApp" || $0.name == "nativePollSeconds" }
        items.append(URLQueryItem(name: "nativeApp", value: "1"))
        items.append(URLQueryItem(name: "nativePollSeconds", value: String(refreshIntervalSeconds)))
        components.queryItems = items
        if let updated = components.url { view.load(URLRequest(url: updated)) }
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        private let host: String
        private let port: Int
        private let onNavigate: ((URL) -> Void)?
        private var observation: NSKeyValueObservation?

        init(origin: URL, onNavigate: ((URL) -> Void)?) {
            host = origin.host ?? "127.0.0.1"
            port = origin.port ?? 80
            self.onNavigate = onNavigate
        }

        func observe(_ view: WKWebView) {
            observation = view.observe(\.url, options: [.new]) { [weak self] view, _ in
                guard let current = view.url else { return }
                self?.onNavigate?(current)
            }
        }

        func webView(_ webView: WKWebView,
                     decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = navigationAction.request.url else {
                decisionHandler(.cancel)
                return
            }
            if url.scheme == "http", url.host == host, (url.port ?? 80) == port {
                decisionHandler(.allow)
                return
            }
            if navigationAction.navigationType == .linkActivated,
               let scheme = url.scheme, ["http", "https"].contains(scheme) {
                NSWorkspace.shared.open(url)
            }
            decisionHandler(.cancel)
        }
    }
}
