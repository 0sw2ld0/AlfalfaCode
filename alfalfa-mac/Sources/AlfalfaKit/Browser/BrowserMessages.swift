import Foundation

/// Una petición del motor al navegador propio de la app (`{"tipo":"navegador"}`, alfalfa/navegador.py).
public struct BrowserRequest: Equatable, Sendable {

    // MARK: - Propiedades

    public let id: String
    /// abrir, atras, adelante, recargar, leer, pulsar, escribir, desplazar, captura, js o descargar.
    /// relevo espera a la persona; cancelar_relevo cancela por su id.
    public let action: String
    /// El resto de campos de la petición, como JSON.
    public let json: String

    /// Los campos de la petición (url, ref, texto, permitido…).
    public var fields: [String: Any] {
        (try? JSONSerialization.jsonObject(with: Data(json.utf8))) as? [String: Any] ?? [:]
    }

    // MARK: - Inicializadores

    public init(id: String, action: String, fields: [String: Any] = [:]) {
        self.id = id
        self.action = action
        let data = (try? JSONSerialization.data(withJSONObject: fields, options: [.sortedKeys])) ?? Data("{}".utf8)
        json = String(decoding: data, as: UTF8.self)
    }

    init?(object: [String: Any]) {
        guard let id = object["id"] as? String, !id.isEmpty,
              let action = object["accion"] as? String else { return nil }
        var fields = object
        for key in ["version", "tipo", "id", "accion"] { fields.removeValue(forKey: key) }
        guard JSONSerialization.isValidJSONObject(fields) else { return nil }
        self.init(id: id, action: action, fields: fields)
    }

    // MARK: - Métodos

    public func string(_ key: String) -> String? { fields[key] as? String }

    public func bool(_ key: String) -> Bool { (fields[key] as? NSNumber)?.boolValue ?? false }

    public func strings(_ key: String) -> [String] { fields[key] as? [String] ?? [] }
}

/// La respuesta de la app a una petición del navegador (`{"tipo":"navegador_respuesta"}`).
public struct BrowserReply: Equatable, Sendable {

    // MARK: - Propiedades

    public let id: String
    /// El objeto de la respuesta sin `tipo`, como JSON: ok, resultado, error o permiso.
    public let json: String

    public var ok: Bool { (body["ok"] as? Bool) ?? false }
    public var error: String? { body["error"] as? String }
    public var result: [String: Any] { body["resultado"] as? [String: Any] ?? [:] }
    /// formulario, contrasena o descarga: la app no hizo nada y espera `permitido`.
    public var permissionKind: String? { (body["permiso"] as? [String: Any])?["tipo"] as? String }

    /// La línea de entrada del motor.
    public var object: [String: Any] {
        var object = body
        object["tipo"] = "navegador_respuesta"
        object["id"] = id
        return object
    }

    // MARK: - Propiedades privadas

    private var body: [String: Any] {
        (try? JSONSerialization.jsonObject(with: Data(json.utf8))) as? [String: Any] ?? [:]
    }

    // MARK: - Inicializadores

    private init(id: String, body: [String: Any]) {
        self.id = id
        let valid: [String: Any] = JSONSerialization.isValidJSONObject(body)
            ? body : ["ok": false, "error": "Resultado no serializable."]
        let data = (try? JSONSerialization.data(withJSONObject: valid, options: [.sortedKeys])) ?? Data("{}".utf8)
        json = String(decoding: data, as: UTF8.self)
    }

    // MARK: - Métodos

    public static func success(_ id: String, _ result: [String: Any]) -> BrowserReply {
        BrowserReply(id: id, body: ["ok": true, "resultado": result])
    }

    public static func failure(_ id: String, _ message: String, result: [String: Any] = [:]) -> BrowserReply {
        BrowserReply(id: id, body: ["ok": false, "error": message, "resultado": result])
    }

    public static func permission(_ id: String, kind: String, detail: String,
                                  result: [String: Any] = [:]) -> BrowserReply {
        BrowserReply(id: id, body: ["ok": false, "permiso": ["tipo": kind, "detalle": detail], "resultado": result])
    }
}

/// Qué es una dirección para el navegador: pública, de este Mac o de la red de casa.
/// El mismo criterio que `navegador.destino` del motor.
public enum BrowserAddress {

    public enum Kind: Equatable, Sendable {
        case publicInternet
        case local
        case privateNetwork
    }

    // MARK: - Métodos

    public static func kind(of url: URL) -> Kind {
        let host = (url.host ?? "").lowercased().trimmingCharacters(in: CharacterSet(charactersIn: ".[]"))
        if host == "localhost" || host.hasSuffix(".localhost") { return .local }
        if let bytes = ipv4(host) { return kind(ipv4: bytes) }
        if host.contains(":") { return kind(ipv6: host) }
        let suffixes = [".local", ".lan", ".home", ".internal", ".home.arpa"]
        if host.isEmpty || !host.contains(".") || suffixes.contains(where: host.hasSuffix) { return .privateNetwork }
        return .publicInternet
    }

    /// «host:puerto», como el origen que aprueba el motor.
    public static func origin(of url: URL) -> String {
        let host = (url.host ?? "").lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "."))
        let port = url.port ?? (url.scheme == "https" ? 443 : 80)
        return host + ":" + String(port)
    }

    /// Lo que el usuario escribe en la barra de dirección: sin esquema, https://.
    public static func normalized(_ text: String) -> URL? {
        let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty, !clean.contains(" ") else { return nil }
        let withScheme = clean.contains("://") ? clean : (clean.hasPrefix("localhost") || clean.hasPrefix("127.")
                                                         ? "http://" : "https://") + clean
        guard let url = URL(string: withScheme), ["http", "https"].contains(url.scheme ?? ""), url.host != nil else {
            return nil
        }
        return url
    }

    // MARK: - Métodos privados

    /// Como el analizador de URL del navegador (y `navegador._ip` del motor): si la última etiqueta es un
    /// número, es una IPv4, también en sus formas cortas, hexadecimales u octales (`127.1`, `0x7f.1`,
    /// `0177.0.0.1`, `2130706433`). Un número que no es una IP válida cuenta como 0.0.0.0, no como pública.
    private static func ipv4(_ host: String) -> [UInt8]? {
        let last = host.split(separator: ".", omittingEmptySubsequences: false).last.map(String.init) ?? host
        guard last.range(of: #"^(?:0x[0-9a-f]*|[0-9]+)$"#, options: .regularExpression) != nil else { return nil }
        var address = in_addr()
        guard inet_aton(host, &address) == 1 else { return [0, 0, 0, 0] }
        return withUnsafeBytes(of: address.s_addr) { Array($0) }
    }

    private static func kind(ipv4 b: [UInt8]) -> Kind {
        if b[0] == 127 { return .local }
        let reserved = b[0] == 0 || b[0] == 10 || (b[0] == 100 && (64...127).contains(b[1]))
            || (b[0] == 169 && b[1] == 254) || (b[0] == 172 && (16...31).contains(b[1]))
            || (b[0] == 192 && b[1] == 168) || (b[0] == 192 && b[1] == 0 && (b[2] == 0 || b[2] == 2))
            || (b[0] == 198 && (18...19).contains(b[1])) || (b[0] == 198 && b[1] == 51 && b[2] == 100)
            || (b[0] == 203 && b[1] == 0 && b[2] == 113) || b[0] >= 224
        return reserved ? .privateNetwork : .publicInternet
    }

    private static func kind(ipv6 host: String) -> Kind {
        var address = in6_addr()
        guard inet_pton(AF_INET6, host, &address) == 1 else { return .privateNetwork }
        let b = withUnsafeBytes(of: address) { Array($0) }
        if b[0..<15].allSatisfy({ $0 == 0 }) && b[15] == 1 { return .local }
        if b[0..<10].allSatisfy({ $0 == 0 }) && b[10] == 0xff && b[11] == 0xff { return kind(ipv4: Array(b[12..<16])) }
        let unspecified = b.allSatisfy { $0 == 0 }
        let uniqueLocal = (b[0] & 0xfe) == 0xfc
        let linkLocal = b[0] == 0xfe && (b[1] & 0xc0) == 0x80
        if unspecified || uniqueLocal || linkLocal || b[0] == 0xff {
            return .privateNetwork
        }
        return .publicInternet
    }
}
