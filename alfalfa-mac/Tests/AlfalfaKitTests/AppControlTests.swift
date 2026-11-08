@testable import AlfalfaKit
import XCTest

/// La tarjeta «Controlar una app» del control de apps (app_state, app_action).
final class AppControlTests: XCTestCase {
    func testAppPermissionCarriesTheApp() {
        let line = #"{"version":2,"tipo":"permiso","id":"a1","argv":["app","Contador Demo"],"#
            + #""proposito":"Controlar Contador Demo: mirar su ventana.","motivo":"app","motivos":["app"],"#
            + #""app":{"nombre":"Contador Demo","bundle":"com.alfalfacode.demo.contador","pid":4242,"accion":"mirar su ventana"},"#
            + #""para_sesion":true}"#
        guard case let .permission(request)? = AlfalfaMessage.decode(line: line) else { return XCTFail("permiso") }
        XCTAssertEqual(request.reason, .app)
        XCTAssertEqual(request.reason?.title, "Controlar una app")
        XCTAssertEqual(request.app, AppTarget(name: "Contador Demo", bundle: "com.alfalfacode.demo.contador",
                                              action: "mirar su ventana"))
        XCTAssertTrue(request.forSession)
        XCTAssertFalse(PermissionReason.app.leavesSandbox)
    }

    func testLimitedLevelIsShown() {
        let line = #"{"version":2,"tipo":"permiso","id":"a2","argv":["app","Terminal"],"proposito":"Controlar Terminal","#
            + #""motivo":"app","app":{"nombre":"Terminal","bundle":"com.apple.Terminal","nivel":"clic"},"para_sesion":true}"#
        guard case let .permission(request)? = AlfalfaMessage.decode(line: line) else { return XCTFail("permiso") }
        XCTAssertEqual(request.app?.levelText, "Sólo mirar y clics sueltos")
        XCTAssertNil(AppTarget(name: "Juego").levelText)
    }

    func testOtherPermissionsHaveNoApp() {
        let line = #"{"version":2,"tipo":"permiso","id":"p1","argv":["ls"],"proposito":"Ver","motivo":"red"}"#
        guard case let .permission(request)? = AlfalfaMessage.decode(line: line) else { return XCTFail("permiso") }
        XCTAssertNil(request.app)
    }
}
