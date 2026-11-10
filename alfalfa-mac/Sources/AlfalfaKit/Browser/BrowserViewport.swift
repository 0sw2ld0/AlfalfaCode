import Foundation

/// Tamaños de diseño; conserva el agente de usuario honesto de AlfalfaCode.
enum BrowserViewport {
    static func size(_ fields: [String: Any]) throws -> CGSize {
        guard let width = fields["ancho"] as? NSNumber, let height = fields["alto"] as? NSNumber else {
            throw BrowserFailure("Faltan ancho y alto del viewport.")
        }
        let w = width.doubleValue, h = height.doubleValue
        guard w.isFinite, h.isFinite, w.rounded() == w, h.rounded() == h,
              (200...4096).contains(w), (200...4096).contains(h) else {
            throw BrowserFailure("Ancho y alto deben ser enteros entre 200 y 4096 píxeles CSS.")
        }
        return CGSize(width: w, height: h)
    }
}
