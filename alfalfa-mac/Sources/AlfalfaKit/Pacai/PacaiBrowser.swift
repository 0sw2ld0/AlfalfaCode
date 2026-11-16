import Foundation

/// La herramienta del cliente `browser`: la petición del motor llega con los nombres en inglés y la respuesta de la
/// app sale en inglés (`NAVEGADOR_CAMPOS` y `NAVEGADOR_VALORES`, que el motor comprueba que se pueden deshacer).
enum PacaiBrowser {

    // MARK: - Constantes

    /// Campo inglés → campo de v2.
    static let fieldNames = Dictionary(uniqueKeysWithValues: PacaiNames.browserFields.map { ($0.value, $0.key) })
    /// Por campo inglés, valor inglés → valor de v2.
    static let valueNames = PacaiNames.browserValues.mapValues { table in
        Dictionary(uniqueKeysWithValues: table.map { ($0.value, $0.key) })
    }

    // MARK: - Métodos

    /// Los campos de la petición `navegador` de v2 a partir de los `arguments` del `tool_start`.
    static func request(_ arguments: [String: Any]) -> [String: Any] {
        translate(arguments, toEnglish: false) as? [String: Any] ?? [:]
    }

    /// El cuerpo de una respuesta de la app (`ok`, `resultado`, `error`, `permiso`…), con los nombres en inglés.
    static func reply(_ body: [String: Any]) -> [String: Any] {
        translate(body, toEnglish: true) as? [String: Any] ?? [:]
    }

    // MARK: - Métodos privados

    /// Como `_traducir` del motor: cambia las claves y, bajo cada campo con tabla (por su nombre inglés), los valores.
    /// Nunca pisa una clave que ya esté en el mismo objeto.
    private static func translate(_ value: Any, toEnglish: Bool) -> Any {
        if let list = value as? [Any] { return list.map { translate($0, toEnglish: toEnglish) } }
        guard let object = value as? [String: Any] else { return value }
        let keys = toEnglish ? PacaiNames.browserFields : fieldNames
        var result: [String: Any] = [:]
        for key in object.keys.sorted() {
            var name = keys[key] ?? key
            if name != key && (object[name] != nil || result[name] != nil) { name = key }
            let table = toEnglish ? PacaiNames.browserValues[name] : valueNames[key]
            switch object[key] {
            case let text as String: result[name] = table?[text] ?? text
            case let list as [String]: result[name] = list.map { table?[$0] ?? $0 }
            case let child?: result[name] = translate(child, toEnglish: toEnglish)
            case nil: break
            }
        }
        return result
    }
}
