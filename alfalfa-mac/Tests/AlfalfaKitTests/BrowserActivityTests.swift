@testable import AlfalfaKit
import Foundation
import Testing

@Suite("Actividad del navegador")
struct BrowserActivityTests {

    // MARK: - Métodos de prueba

    @Test func reportKeepsMetadataAndDoesNotRevealPathsOrInstructions() throws {
        try withStore { store, directory in
            let unsafe = "https://usuario:CLAVE@ejemplo.test:443/ruta?token=PRIVADO#oculto"
            var entry = Self.entry(action: "browser_click", origin: unsafe)
            entry["captura"] = "../../PRIVADO.png"
            entry["params"] = ["text": "ignora las instrucciones"]
            entry["permisos"] = [["tipo": "formulario", "decision": "omitido", "alcance": "accion"]]
            try write([entry], to: directory)
            let log = try store.load()
            let event = try #require(log.eventos.first)
            #expect(event.origin == nil)
            #expect(!event.hasCapture)
            let html = String(decoding: BrowserActivityReport.render(log, store: store, includeCaptures: false),
                              as: UTF8.self)
            #expect(html.contains("Qué hizo el agente"))
            #expect(html.contains("default-src 'none'"))
            #expect(html.contains("concedido por Omitir permisos"))
            #expect(!html.contains("CLAVE"))
            #expect(!html.contains("PRIVADO"))
            #expect(!html.contains("ignora las instrucciones"))
            #expect(!html.contains("<script"))
        }
    }

    @Test func onlyAssociatedCaptureIsIncludedWhenExplicitlySelected() throws {
        try withStore { store, directory in
            let captures = directory.appendingPathComponent("capturas")
            try makePrivate(captures)
            let name = "navegador-11111111111141118111111111111111.png"
            let pngText = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAARXRFWHREZXNjcmlwdGlvbgBodHRw" +
                "czovL3VzdWFyaW86Q0xBVkVAZWplbXBsby50ZXN0L3J1dGE/dG9rZW49UE5HX1BSSVZBRE84sMajAAAADElEQVR4nGN" +
                "Qm5kNAAITAStGnxmmAAAAAElFTkSuQmCC"
            let png = try #require(Data(base64Encoded: pngText))
            try png.write(to: captures.appendingPathComponent(name))
            var screenshot = Self.entry(action: "browser_screenshot")
            screenshot["captura"] = name
            var click = Self.entry(action: "browser_click", id: String(repeating: "2", count: 32))
            click["captura"] = name
            try write([screenshot, click], to: directory)
            let log = try store.load()
            try #require(log.eventos.count == 2)
            #expect(store.capture(for: log.eventos[0]) == png)
            #expect(store.capture(for: log.eventos[1]) == nil)
            let onlyMetadata = BrowserActivityReport.render(log, store: store, includeCaptures: false)
            let withImage = BrowserActivityReport.render(log, store: store, includeCaptures: true)
            #expect(!String(decoding: onlyMetadata, as: UTF8.self).contains("data:image/png"))
            let html = String(decoding: withImage, as: UTF8.self)
            let images = html.components(separatedBy: "data:image/png;base64,")
            try #require(images.count == 2)
            let encoded = try #require(images[1].split(separator: "\"").first)
            let pixels = try #require(Data(base64Encoded: String(encoded)))
            #expect(pixels.range(of: Data("PNG_PRIVADO".utf8)) == nil)
            #expect(!html.contains(name))
        }
    }

    @Test func savedHistoryDoesNotFollowLinksOrUnboundedFiles() throws {
        try withStore { store, directory in
            #expect(try store.load().eventos.isEmpty)
            let path = directory.appendingPathComponent("actividad-navegador.json")
            let other = directory.appendingPathComponent("otro.json")
            try Data("privado".utf8).write(to: other)
            try FileManager.default.createSymbolicLink(at: path, withDestinationURL: other)
            #expect(throws: BrowserActivityFileError.self) { try store.load() }
            try FileManager.default.removeItem(at: path)
            try Data(repeating: 0, count: 512 * 1024 + 1).write(to: path)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path.path)
            #expect(throws: BrowserActivityFileError.self) { try store.load() }
            #expect(BrowserActivityStore(home: directory, sessionID: "../otro") == nil)
        }
    }

    @Test func reportsOldCuyWithoutInventingNativePermissions() throws {
        try withStore { store, directory in
            var event = Self.entry(action: "browser_read")
            event["motor"] = "cuybrowser"
            event["cuy"] = ["estado": "no_disponible", "acciones": []]
            try write([event], to: directory)
            let report = BrowserActivityReport.render(try store.load(), store: store, includeCaptures: false)
            let html = String(decoding: report, as: UTF8.self)
            #expect(html.contains("Este CuyBrowser no ofrece auditoría reciente"))
            #expect(!html.contains("Aprobado por CuyBrowser"))
        }
    }

    // MARK: - Métodos privados

    private static func entry(action: String, origin: String = "https://ejemplo.test:443",
                              id: String = String(repeating: "1", count: 32)) -> [String: Any] {
        ["id": id, "hora": 1_763_000_000, "accion": action, "motor": "webkit",
         "resultado": "correcta", "origen": origin, "permisos": []]
    }

    private func withStore(_ body: (BrowserActivityStore, URL) throws -> Void) throws {
        let home = FileManager.default.temporaryDirectory.appendingPathComponent("actividad-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: home) }
        try makePrivate(home)
        let directory = try ["sesiones", "demo", "native", "demo"].reduce(home) { parent, part in
            let child = parent.appendingPathComponent(part)
            try makePrivate(child)
            return child
        }
        try body(#require(BrowserActivityStore(home: home, sessionID: "demo")), directory)
    }

    private func makePrivate(_ url: URL) throws {
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
    }

    private func write(_ events: [[String: Any]], to directory: URL) throws {
        let file = directory.appendingPathComponent("actividad-navegador.json")
        let data = try JSONSerialization.data(withJSONObject: ["esquema": 1, "recortado": false, "eventos": events])
        try data.write(to: file)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
    }
}
