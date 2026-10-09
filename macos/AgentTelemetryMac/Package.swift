// swift-tools-version: 5.8
import PackageDescription

let package = Package(
    name: "AgentTelemetryMac",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "AgentTelemetryMac", targets: ["AgentTelemetryMac"])
    ],
    targets: [
        .executableTarget(
            name: "AgentTelemetryMac",
            path: "Sources/AgentTelemetryMac"
        )
    ]
)
