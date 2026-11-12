import CoreFoundation
import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

public enum BrowserActivityReport {

    // MARK: - Constantes

    public static let maxCaptures = 12
    private static let maxImageBytes = 16 * 1024 * 1024

    // MARK: - Métodos públicos

    public static func render(_ log: BrowserActivityLog, store: BrowserActivityStore?, includeCaptures: Bool) -> Data {
        var html = """
        <!doctype html><html lang="es"><meta charset="utf-8">
        <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:;
        style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
        <title>Qué hizo el agente</title>
        <style>body{font:16px system-ui;max-width:56rem;margin:2rem auto;padding:0 1rem}
        article{border-top:1px solid #bbb;padding:.75rem 0} h2{font-size:1rem} img{max-width:100%}</style>
        <h1>Qué hizo el agente</h1><p>\(escape(BrowserActivityLog.note))</p>
        """
        if log.recortado { html += "<p>El historial contiene las acciones más recientes.</p>" }
        var images = 0
        var inspected = 0
        var remaining = maxImageBytes
        for event in log.eventos.suffix(200).reversed() {
            html += row(event)
            if includeCaptures, event.hasCapture, inspected < maxCaptures, remaining > 0 {
                inspected += 1
                if let raw = store?.capture(for: event, limit: remaining),
                   let png = pixelsOnly(raw), png.count <= remaining {
                    html += "<img alt=\"Captura asociada\" src=\"data:image/png;base64,\(png.base64EncodedString())\">"
                    images += 1
                    remaining -= png.count
                }
            }
            html += "</article>"
        }
        if includeCaptures {
            html += "<p>Se incluyeron \(images) capturas disponibles (hasta \(maxCaptures), 16 MiB en total).</p>"
        }
        html += "</html>"
        return Data(html.utf8)
    }

    // MARK: - Métodos privados

    /// El informe recibe los píxeles, no el texto, perfiles u otros metadatos del PNG original.
    private static func pixelsOnly(_ data: Data) -> Data? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil),
              let color = CGColorSpace(name: CGColorSpace.sRGB),
              let raster = CGContext(data: nil, width: image.width, height: image.height, bitsPerComponent: 8,
                                     bytesPerRow: 0, space: color,
                                     bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        let bounds = CGRect(x: 0, y: 0, width: CGFloat(image.width), height: CGFloat(image.height))
        raster.draw(image, in: bounds)
        guard let pixels = raster.makeImage(), let output = CFDataCreateMutable(nil, 0),
              let destination = CGImageDestinationCreateWithData(output, UTType.png.identifier as CFString, 1, nil)
        else { return nil }
        CGImageDestinationAddImage(destination, pixels, nil)
        return CGImageDestinationFinalize(destination) ? output as Data : nil
    }

    private static func row(_ event: BrowserActivityEntry) -> String {
        let site = event.origin ?? "Sin sitio registrado"
        var html = "<article><h2>\(escape(event.title))</h2>"
        html += "<p>\(escape(event.date.ISO8601Format())) · \(escape(event.motor.title)) · \(escape(site))</p>"
        html += "<p>\(escape(event.resultado.title))</p><ul>"
        if event.permisos.isEmpty { html += "<li>\(escape(event.permissionSummary))</li>" }
        for permission in event.permisos {
            let origin = permission.origin.map { " · " + $0 } ?? ""
            html += "<li>\(escape(permission.title + origin))</li>"
        }
        html += "</ul>"
        if let cuy = event.cuy {
            html += "<p>\(escape(cuy.estado.title))</p><ul>"
            for action in cuy.acciones {
                let site = action.origin.map { " · " + $0 } ?? ""
                let state = action.ok ? "Completada" : "No completada"
                let detail = action.date.ISO8601Format() + " · " + action.title + site + " · " + state
                html += "<li>\(escape(detail + " · " + action.permission.title))</li>"
            }
            html += "</ul>"
        }
        return html
    }

    private static func escape(_ value: String) -> String {
        value.replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
            .replacingOccurrences(of: "\"", with: "&quot;")
            .replacingOccurrences(of: "'", with: "&#39;")
    }
}
