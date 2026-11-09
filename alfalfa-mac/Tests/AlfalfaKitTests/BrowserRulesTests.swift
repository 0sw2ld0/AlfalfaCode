@testable import AlfalfaKit
import Network
import WebKit
import XCTest

/// Las reglas que bloquean los subrecursos hacia este Mac: con un servidor de verdad en 127.0.0.1.
@MainActor
final class BrowserRulesTests: XCTestCase {

    /// Un servidor HTTP mínimo que cuenta las conexiones que recibe.
    private final class Server: @unchecked Sendable {
        let listener: NWListener
        private let lock = NSLock()
        private var count = 0
        var connections: Int { lock.lock(); defer { lock.unlock() }; return count }

        init() throws {
            let parameters = NWParameters.tcp
            parameters.requiredLocalEndpoint = NWEndpoint.hostPort(host: "127.0.0.1", port: .any)
            listener = try NWListener(using: parameters)
            listener.newConnectionHandler = { [weak self] connection in
                guard let self else { return }
                self.lock.lock(); self.count += 1; self.lock.unlock()
                connection.start(queue: .global())
                connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) { _, _, _, _ in
                    let body = "<!doctype html><title>Local</title><p>hola</p>"
                    let reply = "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nAccess-Control-Allow-Origin: *\r\n"
                        + "Content-Length: \(body.utf8.count)\r\nConnection: close\r\n\r\n" + body
                    connection.send(content: Data(reply.utf8), completion: .contentProcessed { _ in connection.cancel() })
                }
            }
        }

        func start() async throws -> UInt16 {
            listener.start(queue: .global())
            let deadline = Date().addingTimeInterval(5)
            while listener.port == nil || listener.port?.rawValue == 0 {
                if Date() > deadline { throw BrowserFailure("El servidor de prueba no arrancó") }
                try await Task.sleep(nanoseconds: 20_000_000)
            }
            return listener.port?.rawValue ?? 0
        }
    }

    func testTheRulesCompileAndKeepApprovedOrigins() async throws {
        let json = BrowserRules.json(allowing: ["127.0.0.1:8000", "::1:5000", "impresora.local:80"])
        let rules = try XCTUnwrap(try JSONSerialization.jsonObject(with: Data(json.utf8)) as? [[String: Any]])
        XCTAssertGreaterThan(rules.count, 20)
        XCTAssertTrue(json.contains(#"127\\.0\\.0\\.1:8000\/"#), json)
        XCTAssertTrue(json.contains(#"\\[::1\\]:5000\/"#), json)
        XCTAssertTrue(json.contains(#"impresora\\.local(:80)?\/"#), json)
        _ = try await BrowserRules.compile(allowing: [])
        _ = try await BrowserRules.compile(allowing: ["127.0.0.1:8000", "::1:5000"])
    }

    func testAPublicPageCannotReachThisMacButAnApprovedOneCan() async throws {
        let server = try Server()
        defer { server.listener.cancel() }
        let port = try await server.start()
        let local = "http://127.0.0.1:\(port)/"

        let engine = BrowserEngine()
        _ = await engine.perform(BrowserRequest(id: "0", action: "estado"))
        let html = "<!doctype html><title>Prueba</title><img src='\(local)imagen.png'><iframe src='\(local)marco'></iframe>"
        engine.webView.loadHTMLString(html, baseURL: URL(string: "https://prueba.alfalfa.test/"))
        try await Task.sleep(nanoseconds: 1_200_000_000)
        let fetched = await engine.perform(BrowserRequest(id: "1", action: "js", fields: [
            "codigo": "try { await fetch('\(local)api'); return 'llegó'; } catch (e) { return 'bloqueado'; }",
            "modifica": true]))
        XCTAssertEqual(fetched.result["valor"] as? String, "\"bloqueado\"", fetched.json)
        XCTAssertEqual(server.connections, 0, "Una página pública llegó a 127.0.0.1")

        // Aprobado por el motor: la página local carga, y desde ella se puede usar la red local.
        let opened = await engine.perform(BrowserRequest(id: "2", action: "abrir", fields: [
            "url": local, "red_local": ["127.0.0.1:\(port)"]]))
        XCTAssertTrue(opened.ok, opened.error ?? opened.json)
        XCTAssertEqual(opened.result["titulo"] as? String, "Local")
        let own = await engine.perform(BrowserRequest(id: "3", action: "js", fields: [
            "codigo": "try { await fetch('\(local)api'); return 'llegó'; } catch (e) { return String(e); }",
            "modifica": true]))
        XCTAssertEqual(own.result["valor"] as? String, "\"llegó\"", own.json)
        XCTAssertGreaterThanOrEqual(server.connections, 2)
    }
}
