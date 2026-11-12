@testable import AlfalfaKit
import WebKit
import XCTest

@MainActor
final class BrowserViewportTests: XCTestCase {

    func testViewportChangesCSSWidthMediaQueriesAndKeepsBrowserIdentity() async throws {
        let engine = BrowserEngine()
        defer { engine.close() }
        _ = await engine.perform(BrowserRequest(id: "0", action: "estado"))
        engine.webView.loadHTMLString("""
        <!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Adaptable</title><style>@media(max-width:500px){body{background:rgb(1,2,3)}}</style><p>Hola</p>
        """, baseURL: URL(string: "https://alfalfa.test/"))
        for _ in 0..<100 {
            if (try? await engine.webView.evaluateJavaScript("document.title") as? String) == "Adaptable" { break }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        let identity = try await engine.webView.evaluateJavaScript("navigator.userAgent") as? String
        let mobile = await engine.perform(BrowserRequest(id: "1", action: "viewport", fields: [
            "ancho": 390, "alto": 844]))
        XCTAssertTrue(mobile.ok, mobile.json)
        let mobileWidth = try await engine.webView.evaluateJavaScript("innerWidth") as? Int
        let mobileMatch = try await engine.webView.evaluateJavaScript("matchMedia('(max-width:500px)').matches")
        XCTAssertEqual(mobileWidth, 390)
        XCTAssertEqual(mobileMatch as? Bool, true)
        XCTAssertEqual(engine.viewport, CGSize(width: 390, height: 844))
        let desktop = await engine.perform(BrowserRequest(id: "2", action: "viewport", fields: [
            "ancho": 1100, "alto": 800]))
        XCTAssertTrue(desktop.ok, desktop.json)
        let desktopWidth = try await engine.webView.evaluateJavaScript("innerWidth") as? Int
        let desktopMatch = try await engine.webView.evaluateJavaScript("matchMedia('(max-width:500px)').matches")
        let identityAfter = try await engine.webView.evaluateJavaScript("navigator.userAgent") as? String
        XCTAssertEqual(desktopWidth, 1100)
        XCTAssertEqual(desktopMatch as? Bool, false)
        XCTAssertEqual(identityAfter, identity)
        XCTAssertTrue(identity?.contains("AlfalfaCode") == true)
    }

    func testInvalidViewportCannotResizeTheView() async {
        let engine = BrowserEngine()
        defer { engine.close() }
        let reply = await engine.perform(BrowserRequest(id: "1", action: "viewport", fields: [
            "ancho": 10, "alto": 100_000]))
        XCTAssertFalse(reply.ok)
        XCTAssertNil(engine.viewport)
        XCTAssertThrowsError(try BrowserViewport.size(["ancho": 390.5, "alto": 844]))
    }
}
