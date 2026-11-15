import Foundation

/// Lo que el mundo de sólo lectura de browser_eval no puede parar por sí mismo: `location`.
///
/// WebKit no deja redefinir `location` (ni en `window` ni en `document`), y una URL `javascript:`
/// se ejecuta en la página sin pasar por la política de navegación: `location['assign']('javascript:…')`
/// enviaba un formulario sin tarjeta. El motor ya lo manda a permiso (`navegador.parece_que_cambia`);
/// esta es la misma regla en la app, por si una petición de sólo lectura llega sin pasar por ese filtro.
/// Es estática: un script que arma `'loc' + 'ation'` en tiempo de ejecución no se ve aquí.
public enum ReadOnlyScript {
    private static let readsOfLocation = try! NSRegularExpression(
        pattern: #"\blocation\s*\.\s*(?:href|host|hostname|pathname|search|hash|origin|protocol|port)\b"#)
    private static let location = try! NSRegularExpression(pattern: "location", options: [.caseInsensitive])
    private static let javascriptURL = try! NSRegularExpression(pattern: #"javascript\s*:"#,
                                                                options: [.caseInsensitive])
    private static let escape = try! NSRegularExpression(
        pattern: #"\\(?:x([0-9a-fA-F]{2})|u\{([0-9a-fA-F]{1,6})\}|u([0-9a-fA-F]{4}))"#)

    /// Por qué este script no puede correr sin permiso, o nil si puede.
    public static func refusal(_ code: String) -> String? {
        let raw = unescaped(code)
        let range = NSRange(raw.startIndex..., in: raw)
        if javascriptURL.firstMatch(in: raw, range: range) != nil {
            return "usa una URL javascript:"
        }
        if location.numberOfMatches(in: raw, range: range) > readsOfLocation.numberOfMatches(in: raw, range: range) {
            return "usa location para algo más que leer sus partes"
        }
        return nil
    }

    /// `\x6a`, `\u006a` y `\u{6a}` pasados a su carácter: así no esconden «javascript:» ni «location».
    static func unescaped(_ code: String) -> String {
        let source = code as NSString
        var result = ""
        var last = 0
        for match in escape.matches(in: code, range: NSRange(location: 0, length: source.length)) {
            result += source.substring(with: NSRange(location: last, length: match.range.location - last))
            let hex = (1...3).lazy.compactMap { i -> String? in
                let r = match.range(at: i)
                return r.location == NSNotFound ? nil : source.substring(with: r)
            }.first ?? ""
            if let value = UInt32(hex, radix: 16), let scalar = Unicode.Scalar(value) {
                result.unicodeScalars.append(scalar)
            }
            last = match.range.location + match.range.length
        }
        return result + source.substring(from: last)
    }
}
