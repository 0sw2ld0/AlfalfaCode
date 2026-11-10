import Foundation

/// Comparaciones que adjunta el arnés al cierre; no son un resultado automático de las pruebas.
public struct InterfaceProof: Equatable, Sendable {

    // MARK: - Tipos

    public struct Comparison: Equatable, Sendable {
        public var medium: String
        public var title: String
        public var before: URL
        public var after: URL

        public var images: [MarkdownMedia] {
            [MarkdownMedia(alt: "Antes", source: before.path, url: before, kind: .image),
             MarkdownMedia(alt: "Después", source: after.path, url: after, kind: .image)]
        }
    }

    // MARK: - Propiedades

    public var complete: Bool
    public var comparisons: [Comparison]
    public var notes: [String]

    // MARK: - Inicializadores

    public init?(value: Any?) {
        guard let object = value as? [String: Any], let state = object["estado"] as? String,
              ["completa", "incompleta"].contains(state) else { return nil }
        let items = object["comparaciones"] as? [[String: Any]] ?? []
        var seen = Set<String>()
        comparisons = items.prefix(2).compactMap { item in
            guard let medium = item["medio"] as? String, ["web", "mac"].contains(medium),
                  seen.insert(medium).inserted, let before = Self.image(item["antes"], moment: "antes"),
                  let after = Self.image(item["despues"], moment: "despues"),
                  before.deletingLastPathComponent() == after.deletingLastPathComponent(),
                  before.lastPathComponent.replacingOccurrences(of: "-antes.png", with: "")
                    == after.lastPathComponent.replacingOccurrences(of: "-despues.png", with: "") else { return nil }
            return Comparison(medium: medium, title: String((item["titulo"] as? String ?? "Interfaz").prefix(120)),
                              before: before, after: after)
        }
        notes = (object["notas"] as? [String] ?? []).prefix(4).map { String($0.prefix(300)) }
        complete = state == "completa" && !comparisons.isEmpty && comparisons.count == items.count
        if comparisons.count != items.count { notes.append("Hay capturas que no se pueden mostrar.") }
    }

    // MARK: - Métodos privados

    private static func image(_ value: Any?, moment: String) -> URL? {
        guard let path = value as? String, path.hasPrefix("/"), !path.contains("\0"),
              !path.split(separator: "/").contains("..") else { return nil }
        let url = URL(fileURLWithPath: path).standardizedFileURL
        let pattern = "^navegador-interfaz-[a-f0-9]{32}-" + moment + "\\.png$"
        guard url.lastPathComponent.range(of: pattern, options: .regularExpression) != nil,
              url.deletingLastPathComponent().lastPathComponent == "capturas" else { return nil }
        return url
    }
}
