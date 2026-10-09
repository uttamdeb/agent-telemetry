import AppKit
import SwiftUI
import WebKit

struct DashboardWebView: NSViewRepresentable {
    let url: URL
    let refreshIntervalSeconds: Int

    private var pageURL: URL {
        guard var components = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return url }
        var items = components.queryItems ?? []
        items.removeAll { $0.name == "nativeApp" || $0.name == "nativePollSeconds" }
        items.append(URLQueryItem(name: "nativeApp", value: "1"))
        items.append(URLQueryItem(name: "nativePollSeconds", value: String(refreshIntervalSeconds)))
        components.queryItems = items
        return components.url ?? url
    }

    func makeCoordinator() -> Coordinator { Coordinator(origin: url) }

    func makeNSView(context: Context) -> WKWebView {
        let view = WKWebView(frame: .zero)
        view.navigationDelegate = context.coordinator
        view.load(URLRequest(url: pageURL))
        return view
    }

    func updateNSView(_ view: WKWebView, context: Context) {
        if view.url != pageURL { view.load(URLRequest(url: pageURL)) }
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        private let host: String
        private let port: Int

        init(origin: URL) {
            host = origin.host ?? "127.0.0.1"
            port = origin.port ?? 80
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
