import SwiftUI

struct DashboardWindow: View {
    @ObservedObject var backend: BackendController
    @ObservedObject var settings: AppSettings

    var body: some View {
        Group {
            if backend.isReady {
                DashboardWebView(
                    url: backend.dashboardURL,
                    refreshIntervalSeconds: settings.refreshIntervalSeconds
                )
            } else {
                VStack(spacing: 10) {
                    Image(systemName: backend.status.symbol)
                        .font(.system(size: 25))
                        .foregroundStyle(.secondary)
                    Text(backend.status.title)
                        .font(.headline)
                    if let message = backend.status.message {
                        Text(message)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                    }
                    Button("Start monitoring") { backend.start() }
                        .buttonStyle(.borderedProminent)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(minWidth: 760, minHeight: 560)
        .background(Color(nsColor: .windowBackgroundColor))
    }
}
