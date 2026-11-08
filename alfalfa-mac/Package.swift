// swift-tools-version: 5.9
import Foundation
import PackageDescription

/// El Info.plist de la app va dentro del ejecutable: así el menú del sistema sale en español.
let infoPlist = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
    .appendingPathComponent("Sources/AlfalfaCode/Info.plist").path

let package = Package(
    name: "AlfalfaCode",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "AlfalfaCode", targets: ["AlfalfaCode"]),
        .library(name: "AlfalfaKit", targets: ["AlfalfaKit"])
    ],
    targets: [
        .target(name: "AlfalfaKit"),
        .executableTarget(
            name: "AlfalfaCode",
            dependencies: ["AlfalfaKit"],
            exclude: ["Info.plist"],
            resources: [.process("Resources")],
            linkerSettings: [
                .unsafeFlags(["-Xlinker", "-sectcreate", "-Xlinker", "__TEXT", "-Xlinker", "__info_plist",
                              "-Xlinker", infoPlist])
            ]
        ),
        .testTarget(
            name: "AlfalfaKitTests",
            dependencies: ["AlfalfaKit"],
            resources: [.copy("Fixtures")]
        )
    ]
)
