@testable import AlfalfaKit
import Network
import WebKit
import XCTest

@MainActor
final class BrowserDiagnosticsTests: XCTestCase {

    func testConsoleCapturesEarlyMessagesErrorsAndRejectionsThenResets() async throws {
        let engine = BrowserEngine()
        defer { engine.close() }
        _ = await engine.perform(BrowserRequest(id: "0", action: "estado"))
        engine.webView.loadHTMLString("""
        <!doctype html><title>Diagnósticos</title><script>
        console.error('error esperado');
        setTimeout(() => { throw new Error('excepción esperada'); }, 0);
        Promise.reject(new Error('rechazo esperado'));
        </script>
        """, baseURL: URL(string: "https://alfalfa.test/"))
        var reply = BrowserReply.success("", [:])
        for _ in 0..<100 {
            reply = await engine.perform(BrowserRequest(id: "1", action: "consola"))
            if (reply.result["mensajes"] as? [[String: Any]])?.count == 3 { break }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        let entries = try XCTUnwrap(reply.result["mensajes"] as? [[String: Any]])
        XCTAssertEqual(Set(entries.compactMap { $0["nivel"] as? String }),
                       Set(["error", "exception", "unhandledrejection"]), reply.json)
        XCTAssertTrue(entries.contains { ($0["texto"] as? String)?.contains("rechazo esperado") == true })
        engine.webView.loadHTMLString("<!doctype html><title>Otra</title>",
                                      baseURL: URL(string: "https://otra.test/"))
        try await Task.sleep(nanoseconds: 400_000_000)
        let empty = await engine.perform(BrowserRequest(id: "2", action: "consola"))
        XCTAssertEqual((empty.result["mensajes"] as? [[String: Any]])?.count, 0, empty.json)
    }

    func testBuffersAreBoundedAndIgnoreUnexpectedFields() {
        let diagnostics = BrowserDiagnostics()
        for index in 0..<210 {
            diagnostics.receive(["tipo": "consola", "nivel": "log", "texto": String(index)])
            diagnostics.receive(["tipo": "red", "url": "https://a.test/\(index)", "metodo": "GET",
                                 "estado": 500, "body": "secreto", "headers": ["clave": "secreto"]])
        }
        let console = diagnostics.result(network: false, limit: 200)
        XCTAssertEqual((console["mensajes"] as? [[String: Any]])?.count, 200)
        XCTAssertEqual(console["descartados"] as? Int, 10)
        let network = diagnostics.result(network: true, limit: 2)
        let entries = network["peticiones"] as? [[String: Any]] ?? []
        XCTAssertEqual(entries.count, 2)
        XCTAssertEqual(network["descartados"] as? Int, 208)
        XCTAssertNil(entries.first?["body"])
        XCTAssertNil(entries.first?["headers"])
        diagnostics.reset()
        XCTAssertEqual(diagnostics.result(network: true, limit: 200)["descartados"] as? Int, 0)
    }

    func testFetchXHRAndBrokenResourceAreObserved() async throws {
        let server = try DiagnosticsServer()
        let port = try await server.start()
        defer { server.listener.cancel() }
        let engine = BrowserEngine()
        defer { engine.close() }
        let url = "http://127.0.0.1:\(port)/"
        let opened = await engine.perform(BrowserRequest(id: "0", action: "abrir", fields: [
            "url": url, "red_local": ["127.0.0.1:\(port)"]]))
        XCTAssertTrue(opened.ok, opened.json)
        var entries: [[String: Any]] = []
        for _ in 0..<100 {
            let reply = await engine.perform(BrowserRequest(id: "1", action: "red"))
            entries = reply.result["peticiones"] as? [[String: Any]] ?? []
            if entries.contains(where: { ($0["estado"] as? Int) == 500 }) && entries.count >= 3 { break }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        XCTAssertTrue(entries.contains { ($0["estado"] as? Int) == 404 }, "\(entries)")
        XCTAssertTrue(entries.contains { ($0["estado"] as? Int) == 500 }, "\(entries)")
        let broken = entries.contains { entry in
            (entry["url"] as? String)?.contains("rota.png") == true && (entry["fallo"] as? Bool) == true
        }
        XCTAssertTrue(broken, "\(entries)")
    }
}

/// Servidor propio sin dependencias: nunca usa el puerto 8765 ni páginas del usuario.
private final class DiagnosticsServer: @unchecked Sendable {
    let listener: NWListener

    init() throws {
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = NWEndpoint.hostPort(host: "127.0.0.1", port: .any)
        listener = try NWListener(using: parameters)
        listener.newConnectionHandler = { connection in
            connection.start(queue: .global())
            connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) { data, _, _, _ in
                let request = String(decoding: data ?? Data(), as: UTF8.self)
                let status = request.contains(" /404") ? 404 : (request.contains(" / ") ? 200 : 500)
                let html = """
                <!doctype html><title>Red</title><img src='/rota.png'><script>
                fetch('/404?token=prueba'); const x = new XMLHttpRequest(); x.open('GET', '/500'); x.send();
                </script>
                """
                let body = status == 200 ? html : "Fallo esperado"
                let response = "HTTP/1.1 \(status) Test\r\nContent-Type: text/html\r\n"
                    + "Content-Length: \(body.utf8.count)\r\nConnection: close\r\n\r\n" + body
                connection.send(content: Data(response.utf8),
                                completion: .contentProcessed { _ in connection.cancel() })
            }
        }
    }

    func start() async throws -> UInt16 {
        listener.start(queue: .global())
        for _ in 0..<100 {
            if let port = listener.port?.rawValue, port > 0 { return port }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        throw BrowserFailure("El servidor de diagnóstico no arrancó.")
    }
}
