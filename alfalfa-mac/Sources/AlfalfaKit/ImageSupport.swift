import Foundation

/// Un modelo para ofrecer en lugar de otro: el nombre que guarda la sesión y el visible.
public struct ModelChoice: Equatable, Sendable {
    public var model: String
    public var label: String

    public init(model: String, label: String) {
        self.model = model
        self.label = label
    }
}

/// El motor no empezó el turno de un mensaje: su texto y sus imágenes vuelven al cuadro.
public struct MessageRejection: Equatable, Sendable {
    /// Lo que explica el rechazo, para el aviso.
    public var message: String
    /// `sin_vision`: traía imágenes y el modelo no las ve.
    public var reason: String?
    public var text: String
    public var images: [String]
    /// Modelos con visión que el usuario tiene, según el motor.
    public var alternatives: [ModelChoice]

    public init(message: String, reason: String? = nil, text: String, images: [String] = [],
                alternatives: [ModelChoice] = []) {
        self.message = message
        self.reason = reason
        self.text = text
        self.images = images
        self.alternatives = alternatives
    }
}

/// Qué hacer con las imágenes según el modelo, con lo que dice `--modelos --json` (`vision`).
public enum ImageSupport {
    public static let notice = "Este modelo no ve imágenes"

    /// La ayuda del botón «+»: los archivos se adjuntan con cualquier modelo.
    public static func attachHelp(allowsImages: Bool) -> String {
        allowsImages ? "Adjuntar imágenes, PDF, texto, código, Word o Excel (también puedes pegarlos o soltarlos aquí)"
            : notice + ": adjunta PDF, texto, código, Word o Excel (se envía su texto)"
    }

    /// Se deja adjuntar salvo que se sepa que no las ve: con visión desconocida (nil), sí.
    public static func allows(_ vision: Bool?) -> Bool { vision != false }

    /// La ruta sin enlaces simbólicos: el motor devuelve las rutas resueltas (/private/var/…)
    /// y la app guarda las que eligió el usuario (/var/…).
    public static func canonicalPath(_ path: String) -> String {
        URL(fileURLWithPath: path).resolvingSymlinksInPath().path
    }

    /// El cuadro tras un rechazo: si el usuario ya escribió otra cosa, el texto devuelto se
    /// añade detrás en vez de pisarla.
    public static func restoredDraft(current: String, returned: String) -> String {
        let back = returned.trimmingCharacters(in: .whitespacesAndNewlines)
        let now = current.trimmingCharacters(in: .whitespacesAndNewlines)
        if back.isEmpty || now.contains(back) { return current }
        return now.isEmpty ? back : now + "\n\n" + back
    }

    /// Las imágenes del cuadro tras un rechazo: las que ya tenía y, detrás, las devueltas que
    /// existan y no estén.
    public static func restoredImages(current: [URL], returned: [String],
                                      exists: (URL) -> Bool = { FileManager.default.fileExists(atPath: $0.path) })
        -> [URL] {
        var seen = Set(current.map { canonicalPath($0.path) })
        var result = current
        for path in returned {
            let url = URL(fileURLWithPath: path)
            guard exists(url), seen.insert(canonicalPath(path)).inserted else { continue }
            result.append(url)
        }
        return result
    }

    /// Los modelos con visión que se pueden usar ya (sin clave pendiente), los del mismo proveedor primero.
    public static func alternatives(to model: String, in models: [ModelInfo], limit: Int = 3) -> [ModelChoice] {
        let provider = ModelProvider.id(of: model, in: models)
        let usable = models.filter { info in
            info.vision == true && info.missingKey == nil && info.name != model
                && !info.name.lowercased().contains("embed")
        }
        func rank(_ item: (offset: Int, element: ModelInfo)) -> (Int, Int, Int) {
            (item.element.provider == provider ? 0 : 1, item.element.free ? 1 : 0, item.offset)
        }
        let sorted = usable.enumerated().sorted { rank($0) < rank($1) }
        return sorted.prefix(limit).map { ModelChoice(model: $0.element.name, label: $0.element.label) }
    }
}
