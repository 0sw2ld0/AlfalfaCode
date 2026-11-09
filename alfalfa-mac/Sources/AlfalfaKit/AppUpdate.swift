import CryptoKit
import Foundation

/// Lo que publica `publicar.sh` como `version.json` en cada release de GitHub.
public struct ReleaseInfo: Decodable, Equatable, Sendable {
    public let version: String
    public let url: URL
    public let sha256: String
    public let tamano: Int64?
    public let fecha: String?
    public let notas: [String]?

    public init(version: String, url: URL, sha256: String, tamano: Int64? = nil, fecha: String? = nil,
                notas: [String]? = nil) {
        self.version = version
        self.url = url
        self.sha256 = sha256
        self.tamano = tamano
        self.fecha = fecha
        self.notas = notas
    }
}

/// Las reglas del actualizador de AlfalfaCode.app, el mismo que el de CuyCleaner: sólo se instala
/// un DMG de las releases de 0sw2ld0/AlfalfaCode, con la huella publicada, firmado por el equipo de
/// CuyCoders y notarizado por Apple. La parte con interfaz y procesos está en `AppUpdater`.
public enum AppUpdatePolicy {

    // MARK: - Constantes

    /// `releases/latest` es la última release publicada (no borradores ni versiones previas).
    public static let feed = URL(string: "https://github.com/0sw2ld0/AlfalfaCode/releases/latest/download/version.json")!
    public static let releasesPage = URL(string: "https://github.com/0sw2ld0/AlfalfaCode/releases/latest")!
    /// Sólo se instala lo firmado con este equipo de desarrollador (CuyCoders).
    public static let teamID = "FZ8TW9AWLE"
    public static let bundleIdentifier = "com.cuycoders.alfalfacode"
    /// Como mucho una comprobación automática cada 20 horas.
    public static let interval: TimeInterval = 20 * 3600
    /// Para probar el circuito entero sin GitHub: un `version.json` local (file://).
    public static let feedOverrideKey = "ALFALFA_ACTUALIZACION_FEED"

    // MARK: - Métodos

    /// El feed de este arranque: el de GitHub, o uno local (file://) si se pidió para una prueba.
    public static func feed(environment: [String: String]) -> URL {
        if let value = environment[feedOverrideKey], let url = URL(string: value), url.isFileURL { return url }
        return feed
    }

    /// «0.10.0» es más nueva que «0.9.9»; lo que no son números cuenta como 0.
    public static func isNewer(_ candidate: String, than installed: String) -> Bool {
        let a = candidate.split(separator: ".").map { Int($0) ?? 0 }
        let b = installed.split(separator: ".").map { Int($0) ?? 0 }
        for i in 0..<max(a.count, b.count) {
            let x = i < a.count ? a[i] : 0, y = i < b.count ? b[i] : 0
            if x != y { return x > y }
        }
        return false
    }

    /// El DMG tiene que venir de una release de 0sw2ld0/AlfalfaCode por https. Con un feed local
    /// (prueba), también vale un DMG local.
    public static func isTrustedDownload(_ url: URL, feed: URL = feed) -> Bool {
        guard url.pathExtension == "dmg" else { return false }
        if feed.isFileURL { return url.isFileURL }
        return url.scheme == "https" && url.host == "github.com"
            && url.path.hasPrefix("/0sw2ld0/AlfalfaCode/releases/download/")
    }

    /// Si toca avisar de `release`: más nueva, de un sitio de confianza y, si la busca la propia
    /// app (no el usuario), que no sea la versión que el usuario pidió omitir.
    public static func shouldOffer(_ release: ReleaseInfo, installed: String, skipped: String?, manual: Bool,
                                   feed: URL = feed) -> Bool {
        guard isNewer(release.version, than: installed), isTrustedDownload(release.url, feed: feed) else { return false }
        return manual || skipped != release.version
    }

    public static func isDue(lastCheck: Date?, now: Date = Date()) -> Bool {
        guard let lastCheck else { return true }
        return now.timeIntervalSince(lastCheck) >= interval
    }

    /// Huella SHA-256 en hexadecimal, leyendo el archivo por trozos.
    public static func sha256(of file: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: file)
        defer { try? handle.close() }
        var hasher = SHA256()
        while let chunk = try handle.read(upToCount: 1 << 20), !chunk.isEmpty { hasher.update(data: chunk) }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    /// Lo que escribe `codesign -dv`: ¿la firma es de nuestro equipo? Sólo la línea exacta.
    public static func signedByTeam(codesignDetails: String, team: String = teamID) -> Bool {
        codesignDetails.split(whereSeparator: \.isNewline).contains { $0 == "TeamIdentifier=\(team)" }
    }

    /// Una app que macOS abrió desde una copia temporal («App Translocation») no puede cambiarse
    /// a sí misma: pasa si se abrió sin moverla con Finder a Aplicaciones.
    public static func isTranslocated(_ bundle: URL) -> Bool {
        bundle.path.contains("/AppTranslocation/")
    }
}
