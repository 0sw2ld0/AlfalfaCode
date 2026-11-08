// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "AlfalfaCode",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "AlfalfaCode", targets: ["AlfalfaCode"]),
        .library(name: "AlfalfaKit", targets: ["AlfalfaKit"]),
    ],
    targets: [
        .target(name: "AlfalfaKit"),
        .executableTarget(
            name: "AlfalfaCode",
            dependencies: ["AlfalfaKit"],
            resources: [.process("Resources")]
        ),
        .testTarget(
            name: "AlfalfaKitTests",
            dependencies: ["AlfalfaKit"],
            resources: [.copy("Fixtures")]
        ),
    ]
)
