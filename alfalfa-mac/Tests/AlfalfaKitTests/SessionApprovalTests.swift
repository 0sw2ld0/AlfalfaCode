@testable import AlfalfaKit
import XCTest

/// «Permitir en esta sesión», como ApprovedForSession de Codex.
final class SessionApprovalTests: XCTestCase {
    func testRequestOffersSessionAndResolutionSaysSo() throws {
        let request = #"{"version":2,"tipo":"permiso","id":"p1","argv":["sh","Abrir app.command"],"proposito":"Abrir","motivo":"reintento","motivos":["reintento"],"sin_sandbox":"El intento a1 terminó con código 1","para_sesion":true}"#
        guard case let .permission(permission)? = AlfalfaMessage.decode(line: request) else { return XCTFail("permiso") }
        XCTAssertTrue(permission.forSession)
        var timeline = Timeline()
        timeline.apply(.permission(permission))
        timeline.apply(try XCTUnwrap(AlfalfaMessage.decode(line:
            #"{"version":2,"tipo":"permiso_resuelto","id":"p1","aprobado":true,"motivo":"Respuesta del cliente","sesion":true}"#)))
        guard case let .permission(_, _, state)? = timeline.items.last else { return XCTFail("tarjeta") }
        XCTAssertEqual(state, .approvedForSession)
        XCTAssertNotNil(timeline.runningPermission, "la orden aprobada sigue corriendo")
    }

    func testOlderEnginesNeverOfferIt() {
        let line = #"{"version":2,"tipo":"permiso","id":"p2","argv":["rm","-rf","x"],"proposito":"Borrar","motivo":"peligroso"}"#
        guard case let .permission(permission)? = AlfalfaMessage.decode(line: line) else { return XCTFail("permiso") }
        XCTAssertFalse(permission.forSession)
        XCTAssertEqual(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"permiso_resuelto","id":"p2","aprobado":true,"motivo":"x"}"#),
                       .permissionResolved(id: "p2", approved: true, reason: "x"))
    }

    func testAnswerLine() {
        XCTAssertEqual(AlfalfaCommand.permission(id: "p1", approved: true, session: true).line,
                       #"{"aprobado":true,"id":"p1","sesion":true,"tipo":"permiso"}"# + "\n")
        // Negar nunca lleva «sesion».
        XCTAssertEqual(AlfalfaCommand.permission(id: "p1", approved: false, session: true).line,
                       #"{"aprobado":false,"id":"p1","tipo":"permiso"}"# + "\n")
    }
}
