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
    dependencies: [
        // La terminal integrada: CuyTerm, nuestra copia de SwiftTerm (MIT) sin dependencias y
        // con el portapapeles cerrado. Versión exacta: nada cambia sin que lo decidamos.
        .package(url: "https://github.com/0sw2ld0/CuyTerm.git", exact: "1.99.2-cuy.1")
    ],
    targets: [
        .target(name: "AlfalfaKit"),
        .executableTarget(
            name: "AlfalfaCode",
            dependencies: ["AlfalfaKit", .product(name: "SwiftTerm", package: "CuyTerm")],
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
