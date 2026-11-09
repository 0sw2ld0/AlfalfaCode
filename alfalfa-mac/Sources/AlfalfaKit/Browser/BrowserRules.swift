import CryptoKit
import Foundation
import WebKit

/// Las reglas de contenido (`WKContentRuleList`) que bloquean los subrecursos hacia este Mac o la red de
/// casa: imágenes, marcos, fetch, XHR y WebSocket a 127.0.0.1, localhost, 10.x, 192.168.x, 169.254.x,
/// `.local`, nombres sin punto… La política de navegación sólo ve el marco principal; esto cubre el resto.
///
/// Una página de un origen aprobado (por el motor o porque el usuario escribió la dirección) puede usar
/// la red local: es la app del usuario y suele hablar con su backend en otro puerto. Una pública, no.
/// Lo que no cubre: un nombre público que resuelve a una IP local (127.0.0.1.nip.io), porque las reglas
/// miran la URL y no la IP; el motor sí lo mira antes de abrir o descargar.
public enum BrowserRules {

    /// Hosts locales o de la red de casa, ya normalizados por WebKit (127.1 llega como 127.0.0.1).
    /// Las reglas de contenido no admiten `|` ni `{n}`: una regla por forma.
    static let localHosts: [String] = {
        let quad = "[0-9]+\\.[0-9]+\\.[0-9]+[:/]"
        let twoMore = "[0-9]+\\.[0-9]+[:/]"
        return [
            "localhost[:/]", "[^/:@]*\\.localhost[:/]",
            "127\\." + quad,
            "10\\." + quad, "0\\." + quad,
            "192\\.168\\." + twoMore, "169\\.254\\." + twoMore, "192\\.0\\.0\\.[0-9]+[:/]",
            "172\\.1[6-9]\\." + twoMore, "172\\.2[0-9]\\." + twoMore, "172\\.3[01]\\." + twoMore,
            "100\\.6[4-9]\\." + twoMore, "100\\.[7-9][0-9]\\." + twoMore, "100\\.1[01][0-9]\\." + twoMore,
            "100\\.12[0-7]\\." + twoMore, "198\\.1[89]\\." + twoMore,
            "22[4-9]\\." + quad, "2[3-5][0-9]\\." + quad,
            "[^/:@]*\\.local[:/]", "[^/:@]*\\.lan[:/]", "[^/:@]*\\.home[:/]", "[^/:@]*\\.internal[:/]",
            "[^/:@]*\\.home\\.arpa[:/]",
            // Sin punto («router», «nas»), y todas las IPv6 literales («[::1]», «[fe80::…]»).
            "[^/:@.]+[:/]"
        ]
    }()

    /// El JSON de las reglas para los orígenes aprobados («host:puerto»).
    public static func json(allowing origins: Set<String>) -> String {
        let prefix = "^[a-z]+://([^/@]*@)?"
        let approved = origins.sorted().map { prefix + pattern(forOrigin: $0) }
        var rules: [[String: Any]] = localHosts.map { host in
            var trigger: [String: Any] = ["url-filter": prefix + host]
            if !approved.isEmpty { trigger["unless-top-url"] = approved }
            return ["trigger": trigger, "action": ["type": "block"]]
        }
        // Lo que viene de un origen aprobado tampoco se bloquea (también su propio documento).
        for pattern in approved {
            rules.append(["trigger": ["url-filter": pattern], "action": ["type": "ignore-previous-rules"]])
        }
        let data = (try? JSONSerialization.data(withJSONObject: rules, options: [.sortedKeys])) ?? Data("[]".utf8)
        return String(decoding: data, as: UTF8.self)
    }

    /// El origen «host:puerto» como regex de URL normalizada: sin puerto si es el de siempre.
    static func pattern(forOrigin origin: String) -> String {
        guard let colon = origin.lastIndex(of: ":") else { return escape(origin) + "[:/]" }
        let host = String(origin[..<colon])
        let port = String(origin[origin.index(after: colon)...])
        let escapedHost = host.contains(":") ? "\\[" + escape(host) + "\\]" : escape(host)
        if port == "80" || port == "443" { return escapedHost + "(:" + port + ")?/" }
        return escapedHost + ":" + port + "/"
    }

    private static func escape(_ text: String) -> String {
        var out = ""
        for character in text {
            if "\\^$.*+?()[]{}|/".contains(character) { out.append("\\") }
            out.append(character)
        }
        return out
    }

    /// Compila (o reutiliza) la lista para esos orígenes. Va a un almacén propio en la carpeta temporal,
    /// con el nombre según los orígenes: dos sesiones con los mismos permisos comparten la misma.
    @MainActor
    static func compile(allowing origins: Set<String>) async throws -> WKContentRuleList {
        let text = json(allowing: origins)
        let digest = SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
        let identifier = "alfalfa-red-local-" + digest.prefix(24)
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("alfalfa-reglas-navegador")
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let store = WKContentRuleListStore(url: folder) ?? WKContentRuleListStore.default()!
        if let existing = try? await store.contentRuleList(forIdentifier: identifier) { return existing }
        guard let list = try await store.compileContentRuleList(forIdentifier: identifier,
                                                               encodedContentRuleList: text) else {
            throw BrowserFailure("No se pudieron preparar las reglas de la red local.")
        }
        return list
    }
}
