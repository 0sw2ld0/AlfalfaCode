import AlfalfaKit
import Foundation
import Testing

struct AppControlPreviewStoreTests {
    @Test func readsOnlyTheCaptureOfTheSelectedSession() throws {
        let manager = FileManager.default
        let home = manager.temporaryDirectory.appendingPathComponent("control-apps-\(UUID())")
        defer { try? manager.removeItem(at: home) }
        try manager.createDirectory(at: home, withIntermediateDirectories: false,
                                    attributes: [.posixPermissions: 0o700])
        let folder = try ["sesiones", "demo", "native", "demo", "capturas"].reduce(home) { parent, part in
            let child = parent.appendingPathComponent(part)
            try manager.createDirectory(at: child, withIntermediateDirectories: false,
                                        attributes: [.posixPermissions: 0o700])
            return child
        }
        let store = try #require(AppControlPreviewStore(home: home, sessionID: "demo"))
        let other = try #require(AppControlPreviewStore(home: home, sessionID: "otra"))
        let manifest = folder.deletingLastPathComponent().appendingPathComponent("vista-control-apps.json")
        let name = "vista-app-\(String(repeating: "a", count: 32)).png"
        let capture = folder.appendingPathComponent(name)
        let png = try #require(Data(base64Encoded: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/ccxe94AAAAASUVORK5CYII="))
        try png.write(to: capture)
        try write(name, to: manifest)

        let preview = try #require(store.load())
        #expect(preview.app == "Contador")
        #expect(preview.action == "Hacer clic")
        #expect(store.capture(for: preview) == png)
        #expect(other.load() == nil)

        try write("../\(name)", to: manifest)
        #expect(store.capture(for: try #require(store.load())) == nil)

        try write(name, to: manifest)
        try manager.removeItem(at: capture)
        let outside = home.appendingPathComponent("externa.png")
        try png.write(to: outside)
        try manager.createSymbolicLink(at: capture, withDestinationURL: outside)
        #expect(store.capture(for: try #require(store.load())) == nil)
    }

    private func write(_ capture: String, to file: URL) throws {
        let metadata = ["app": "Contador", "accion": "Hacer clic", "captura": capture, "aviso": ""]
        try JSONSerialization.data(withJSONObject: metadata).write(to: file)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
    }
}
