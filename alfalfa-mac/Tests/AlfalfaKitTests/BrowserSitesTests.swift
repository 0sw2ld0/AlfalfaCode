@testable import AlfalfaKit
import AppKit
import Network
import WebKit
import XCTest

@MainActor
final class BrowserSitesTests: XCTestCase {
    func testExactOriginsAndDenials() throws {
        let policy = try policy(["https://example.com:443"], denied: ["https://deny.test:443"])
        XCTAssertTrue(policy.permits(URL(string: "https://EXAMPLE.com/path")!))
        XCTAssertFalse(policy.permits(URL(string: "http://example.com/")!))
        XCTAssertFalse(policy.permits(URL(string: "https://example.com:444/")!))
        XCTAssertTrue(policy.blocks(URL(string: "https://deny.test/")!))
        XCTAssertEqual(BrowserSitePolicy.origin(URL(string: "http://127.1:8000/")!), "http://127.0.0.1:8000")
        XCTAssertEqual(BrowserSitePolicy.origin(URL(string: "http://[::1]:8000/")!), "http://[::1]:8000")
        XCTAssertEqual(BrowserSitePolicy.origin(URL(string: "https://faß.de/")!), "https://xn--fa-hia.de:443")
    }

    func testPendingTokenPreservesRequestAndRejectsChangedDocumentOrMissingPostBody() throws {
        let state = BrowserSiteState()
        let source = URL(string: "https://source.test/")!
        let target = URL(string: "https://target.test/submit")!
        state.policy = try policy([])
        var post = URLRequest(url: target)
        post.httpMethod = "POST"
        XCTAssertTrue(try state.intercept(post, document: 1, source: source))
        let token = try XCTUnwrap(state.pending?.token)
        state.policy = try policy(["https://target.test:443"])
        let resume = BrowserRequest(id: "resume", action: "reanudar_sitio", fields: [
            "token": token, "url": target.absoluteString])
        XCTAssertThrowsError(try state.take(resume, document: 1, source: source)) { error in
            XCTAssertTrue((error as? BrowserFailure)?.message.contains("no conservó el cuerpo") == true)
        }
        XCTAssertNil(state.pending)

        state.policy = try policy([])
        post.httpBody = Data("demo=valor".utf8)
        XCTAssertTrue(try state.intercept(post, document: 2, source: source))
        let valid = BrowserRequest(id: "valid", action: "reanudar_sitio", fields: [
            "token": state.pending!.token, "url": target.absoluteString])
        state.policy = try policy(["https://target.test:443"])
        let preserved = try state.take(valid, document: 2, source: source)
        XCTAssertEqual(preserved.httpMethod, "POST")
        XCTAssertEqual(preserved.httpBody, post.httpBody)
        state.policy = try policy([])
        _ = try state.intercept(post, document: 2, source: source)
        state.policy = try policy(["https://target.test:443"])
        XCTAssertThrowsError(try state.take(valid, document: 3, source: source))
    }

    func testOnceExpiresWhenEnteringAnotherApprovedSite() throws {
        let state = BrowserSiteState()
        var snapshot = fields(["https://saved.test:443"])
        let lease = String(repeating: "a", count: 32)
        snapshot["visita"] = ["id": lease, "origen": "https://once.test:443"]
        state.apply(try BrowserSitePolicy(snapshot))
        XCTAssertFalse(state.commitVisit(at: URL(string: "https://once.test/next")!))
        XCTAssertTrue(state.commitVisit(at: URL(string: "https://saved.test/")!))
        XCTAssertFalse(state.policy!.permits(URL(string: "https://once.test/")!))
        XCTAssertTrue(state.policy!.permits(URL(string: "https://saved.test/")!))
        state.apply(try BrowserSitePolicy(snapshot))
        XCTAssertFalse(state.policy!.permits(URL(string: "https://once.test/")!),
                       "Un snapshot viejo no revive la visita.")
        XCTAssertEqual(state.reportedConsumption, lease)
    }

    func testRejectedDestinationDoesNotConsumeCurrentVisit() throws {
        let state = BrowserSiteState()
        var snapshot = fields([])
        let lease = String(repeating: "b", count: 32)
        snapshot["visita"] = ["id": lease, "origen": "https://once.test:443"]
        state.apply(try BrowserSitePolicy(snapshot))
        let source = URL(string: "https://once.test/")!
        state.commitVisit(at: source)
        XCTAssertTrue(try state.intercept(URLRequest(url: URL(string: "https://other.test/")!),
                                          document: 1, source: source))
        state.pending = nil // Rechazado: no hubo commit ni salida de la página actual.
        XCTAssertTrue(state.policy!.permits(source))
        XCTAssertFalse(state.consumedVisits.contains(lease))
    }

    func testBackgroundCommitConsumesLeaseAndOldSnapshotCannotReviveIt() async throws {
        let source = try SiteServer(body: "<title>Visita A</title>")
        defer { source.stop() }
        let sourceURL = try await source.start()
        let html = "<title>Guardado B</title><script>fetch('\(sourceURL)after').catch(()=>{});</script>"
        let other = try SiteServer(body: html)
        defer { other.stop() }
        let otherURL = try await other.start()
        let engine = BrowserEngine()
        defer { engine.close() }
        let lease = String(repeating: "c", count: 32)
        var snapshot = fields([origin(otherURL)], strict: true)
        snapshot["visita"] = ["id": lease, "origen": origin(sourceURL)]
        let network = [BrowserAddress.origin(of: sourceURL), BrowserAddress.origin(of: otherURL)]
        let opened = await engine.perform(BrowserRequest(id: "once", action: "abrir", fields: [
            "url": sourceURL.absoluteString, "sitios": snapshot, "red_local": network]))
        XCTAssertTrue(opened.ok, opened.json)
        XCTAssertEqual(opened.result["titulo"] as? String, "Visita A", opened.json)
        _ = try? await engine.webView.evaluateJavaScript("location.href = '\(otherURL.absoluteString)'")
        let deadline = Date().addingTimeInterval(5)
        while engine.webView.title != "Guardado B" && Date() < deadline {
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        XCTAssertEqual(engine.webView.title, "Guardado B")
        try await Task.sleep(nanoseconds: 100_000_000)
        XCTAssertFalse(source.paths.contains("/after"), "El lease no autoriza recursos desde el origen B.")
        let stale = await engine.perform(BrowserRequest(id: "stale", action: "abrir", fields: [
            "url": sourceURL.appendingPathComponent("again").absoluteString, "sitios": snapshot]))
        XCTAssertEqual(stale.permissionKind, "sitio", stale.json)
        XCTAssertEqual(stale.result["visita_consumida"] as? String, lease, stale.json)
        XCTAssertFalse(source.paths.contains("/again"), "El lease consumido no contacta otra vez con A.")
    }

    func testRedirectIsCancelledBeforeContactAndResumedOnlyByItsToken() async throws {
        let target = try SiteServer(body: "<title>Destino aprobado</title>")
        defer { target.stop() }
        let targetURL = try await target.start()
        let source = try SiteServer(body: "", location: targetURL.absoluteString)
        defer { source.stop() }
        let sourceURL = try await source.start()
        let engine = BrowserEngine()
        defer { engine.close() }
        let opened = await engine.perform(BrowserRequest(id: "open", action: "abrir", fields: [
            "url": sourceURL.absoluteString, "sitios": fields([origin(sourceURL)]),
            "red_local": [BrowserAddress.origin(of: sourceURL)]]))
        XCTAssertGreaterThan(source.connections, 0, "No llegó al origen inicial: " + opened.json)
        XCTAssertEqual(opened.permissionKind, "sitio", opened.json + " origen=" + sourceURL.absoluteString
                       + " destino=" + targetURL.absoluteString + " aviso=" + (engine.notice ?? "ninguno"))
        XCTAssertEqual(target.connections, 0, "La redirección no debe contactar con el sitio sin permiso.")
        let pending = try XCTUnwrap(engine.sites.pending)
        let resumed = await engine.perform(BrowserRequest(id: "resume", action: "reanudar_sitio", fields: [
            "token": pending.token, "url": targetURL.absoluteString,
            "sitios": fields([origin(sourceURL), origin(targetURL)]),
            "red_local": [BrowserAddress.origin(of: targetURL)]]))
        XCTAssertTrue(resumed.ok, resumed.json)
        XCTAssertEqual(resumed.result["titulo"] as? String, "Destino aprobado", resumed.json)
        XCTAssertGreaterThanOrEqual(target.connections, 1)
    }

    func testStrictSiteExceptionCannotDisableLocalNetworkBarrier() async throws {
        let target = try SiteServer(body: "local")
        defer { target.stop() }
        let url = try await target.start()
        let engine = BrowserEngine()
        defer { engine.close() }
        let snapshot = fields(["https://public.test:443", origin(url)], strict: true)
        _ = await engine.perform(BrowserRequest(id: "rules", action: "estado", fields: ["sitios": snapshot]))
        engine.webView.loadHTMLString("<title>Pública</title><img src='\(url.absoluteString)imagen'>",
                                      baseURL: URL(string: "https://public.test/"))
        try await Task.sleep(nanoseconds: 600_000_000)
        let fetch = await engine.perform(BrowserRequest(id: "fetch", action: "js", fields: [
            "sitios": snapshot, "modifica": true,
            "codigo": "try { await fetch('\(url.absoluteString)api'); return 'contacto'; } "
                + "catch(e) { return 'bloqueado'; }"]))
        XCTAssertEqual(fetch.result["valor"] as? String, "\"bloqueado\"", fetch.json)
        XCTAssertEqual(target.connections, 0, "Permitir un sitio nunca concede acceso a la red local.")
    }

    func testStrictBlocksAnUnknownSubresourceEvenFromAnApprovedLocalTopPage() async throws {
        let target = try SiteServer(body: "externo")
        defer { target.stop() }
        let targetURL = try await target.start()
        let source = try SiteServer(body: "<title>Origen</title><img src='\(targetURL.absoluteString)image'>")
        defer { source.stop() }
        let sourceURL = try await source.start()
        let engine = BrowserEngine()
        defer { engine.close() }
        let reply = await engine.perform(BrowserRequest(id: "open", action: "abrir", fields: [
            "url": sourceURL.absoluteString, "sitios": fields([origin(sourceURL)], strict: true),
            "red_local": [BrowserAddress.origin(of: sourceURL)]]))
        XCTAssertTrue(reply.ok, reply.json)
        XCTAssertEqual(reply.result["titulo"] as? String, "Origen", reply.json)
        XCTAssertGreaterThan(source.connections, 0)
        try await Task.sleep(nanoseconds: 400_000_000)
        XCTAssertEqual(target.connections, 0)
    }

    private func fields(_ allowed: [String], denied: [String] = [], strict: Bool = false) -> [String: Any] {
        ["version": 1, "permitidos": allowed, "bloqueados": denied, "estricto": strict]
    }

    private func policy(_ allowed: [String], denied: [String] = [], strict: Bool = false) throws -> BrowserSitePolicy {
        try BrowserSitePolicy(fields(allowed, denied: denied, strict: strict))
    }

    private func origin(_ url: URL) -> String { BrowserSitePolicy.origin(url)! }
}

private final class SiteServer: @unchecked Sendable {
    let listener: NWListener
    let body: String
    let location: String?
    private let lock = NSLock()
    private var count = 0
    private var receivedPaths: [String] = []
    var connections: Int { lock.lock(); defer { lock.unlock() }; return count }
    var paths: [String] { lock.lock(); defer { lock.unlock() }; return receivedPaths }

    init(body: String, location: String? = nil) throws {
        self.body = body
        self.location = location
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = NWEndpoint.hostPort(host: "127.0.0.1", port: .any)
        listener = try NWListener(using: parameters)
        listener.newConnectionHandler = { [weak self] connection in
            guard let self else { return }
            self.lock.lock(); self.count += 1; self.lock.unlock()
            connection.start(queue: .global())
            connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [self] data, _, _, _ in
                if let data, let line = String(data: data, encoding: .utf8)?.split(separator: "\r\n").first,
                   let path = line.split(separator: " ").dropFirst().first {
                    self.lock.lock(); self.receivedPaths.append(String(path)); self.lock.unlock()
                }
                let status = self.location == nil ? "200 OK" : "302 Found"
                let redirect = self.location.map { "Location: \($0)\r\n" } ?? ""
                let response = "HTTP/1.1 \(status)\r\n\(redirect)Content-Type: text/html\r\n"
                    + "Access-Control-Allow-Origin: *\r\nContent-Length: \(body.utf8.count)\r\n"
                    + "Connection: close\r\n\r\n" + body
                connection.send(content: Data(response.utf8), completion: .contentProcessed { _ in
                    connection.cancel()
                })
            }
        }
    }

    func start() async throws -> URL {
        listener.start(queue: .global())
        let deadline = Date().addingTimeInterval(5)
        while listener.port == nil || listener.port?.rawValue == 0 {
            if Date() > deadline { throw BrowserFailure("No arrancó el servidor ficticio.") }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        return URL(string: "http://127.0.0.1:\(listener.port!.rawValue)/")!
    }

    func stop() { listener.cancel() }
}
