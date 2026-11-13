import Foundation

/// Lo que pinta la tarjeta de permiso, siempre acotado.
///
/// Tras leer contenido externo, una herramienta que no es una orden (spawn_agent, send_input…) llega
/// como `argv = [nombre, "{…json…}"]`: el encargo entero de un subagente, de 5 a 50 KB, en una sola
/// línea con los saltos escapados. La app de la 0.99.13 se quedó con el hilo principal al 100 % al
/// pintar esa tarjeta. Aquí se lee el JSON: un titular, los argumentos cortos y el texto largo con sus
/// saltos de verdad en una vista previa de pocas líneas; «Ver todo» abre el texto entero en una hoja.
/// Cada texto que llega a SwiftUI sale de aquí con un tope.
public struct PermissionPreview: Equatable, Sendable {
    /// Un argumento corto de la llamada: «tipo: explorador».
    public struct Field: Equatable, Sendable {
        public var key: String
        public var value: String
    }

    public static let maxBodyLines = 12
    public static let maxCommandLines = 30
    public static let maxDetailLines = 8
    public static let maxFieldLength = 200
    public static let maxFields = 12
    public static let maxHeadline = 160

    /// «Encargar a un subagente: Revisar X»; nil si es una orden de terminal.
    public var headline: String?
    /// Los argumentos cortos, en orden de clave.
    public var fields: [Field]
    /// El argumento largo (el mensaje del subagente) o la orden, en pocas líneas.
    public var body: TextPreview
    /// La orden tal cual (monoespaciada) o un texto para leer (el encargo de un subagente).
    public var bodyIsCommand: Bool
    /// Qué argumento es el cuerpo («message»); nil en las órdenes.
    public var bodyKey: String?
    /// Lo entero, para la hoja de «Ver todo»: los argumentos y el texto largo sin recortar.
    public var full: String
    /// Hay algo que no se ve en la tarjeta.
    public var truncated: Bool

    public var purpose: TextPreview?
    public var justification: TextPreview?
    public var detail: TextPreview?
    public var rule: String?

    public init(_ request: PermissionRequest) {
        if let call = Self.toolCall(request.argv) {
            let name = call.name
            let arguments = call.arguments
            let bodyKey = Self.bodyKey(in: arguments)
            let body = bodyKey.flatMap { arguments[$0] as? String } ?? ""
            let keys = arguments.keys.filter { $0 != bodyKey }.sorted()
            let described = (arguments["description"] as? String).map { TextPreview.line($0, limit: 100) }
            let label = ToolCalls.fallbackDescription(name, nil)
            headline = TextPreview.line(described.map { label + ": " + $0 } ?? label, limit: Self.maxHeadline)
            fields = keys.prefix(Self.maxFields).map {
                Field(key: TextPreview.line($0, limit: 40),
                      value: TextPreview.line(Self.text(arguments[$0]), limit: Self.maxFieldLength))
            }
            self.bodyKey = bodyKey
            self.body = TextPreview(body, maxLines: Self.maxBodyLines)
            bodyIsCommand = false
            var whole = keys.map { $0 + ": " + Self.text(arguments[$0]) }
            if let bodyKey { whole.append(contentsOf: ["", "\(bodyKey):", body]) }
            full = whole.joined(separator: "\n")
            truncated = self.body.truncated || keys.count > Self.maxFields
                || fields.contains { $0.value.hasSuffix("…") }
        } else {
            headline = nil
            fields = []
            bodyKey = nil
            full = request.command
            body = TextPreview(full, maxLines: Self.maxCommandLines)
            bodyIsCommand = true
            truncated = body.truncated
        }
        purpose = Self.detail(request.purpose)
        justification = request.justification.flatMap(Self.detail)
        detail = request.detail.flatMap(Self.detail)
        rule = request.rule.map { TextPreview.line($0, limit: Self.maxFieldLength) }
    }

    // MARK: - Privado

    private static func detail(_ text: String) -> TextPreview? {
        text.isEmpty ? nil : TextPreview(text, maxLines: maxDetailLines)
    }

    /// `[nombre, "{…}"]` con un objeto JSON: una herramienta que no es una orden de terminal.
    private static func toolCall(_ argv: [String]) -> (name: String, arguments: [String: Any])? {
        guard argv.count == 2, let first = argv[1].first(where: { !$0.isWhitespace }), first == "{",
              let data = argv[1].data(using: .utf8),
              let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return nil }
        return (argv[0], object)
    }

    /// El texto largo de la llamada: el encargo de un subagente o, si no, la cadena más larga que no
    /// cabe en una línea corta.
    private static func bodyKey(in arguments: [String: Any]) -> String? {
        for key in ["message", "input", "content", "prompt", "text"] where arguments[key] is String {
            return key
        }
        let long = arguments.compactMap { key, value -> (String, Int)? in
            guard let text = value as? String else { return nil }
            let length = text.utf16.count
            return length > maxFieldLength || text.contains("\n") ? (key, length) : nil
        }
        return long.max { $0.1 == $1.1 ? $0.0 > $1.0 : $0.1 < $1.1 }?.0
    }

    private static func text(_ value: Any?) -> String {
        switch value {
        case let text as String: return text
        case let number as NSNumber:
            if CFGetTypeID(number) == CFBooleanGetTypeID() { return number.boolValue ? "sí" : "no" }
            return number.stringValue
        case nil, is NSNull: return ""
        case let other?:
            guard JSONSerialization.isValidJSONObject(other),
                  let data = try? JSONSerialization.data(withJSONObject: other, options: [.sortedKeys]),
                  let text = String(data: data, encoding: .utf8) else { return String(describing: other) }
            return text
        }
    }
}
