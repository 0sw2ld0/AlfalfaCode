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

    /// «No volver a preguntar por órdenes que empiecen por …», como el prefix_rule de Codex.
    func testRequestOffersAlwaysAndResolutionSaysSo() throws {
        let request = #"{"version":2,"tipo":"permiso","id":"p3","argv":["git","switch","-c","x"],"proposito":"Rama","motivo":"salir_sandbox","motivos":["salir_sandbox"],"para_sesion":true,"prefijo":["git","switch"]}"#
        guard case let .permission(permission)? = AlfalfaMessage.decode(line: request) else { return XCTFail("permiso") }
        XCTAssertEqual(permission.alwaysPrefix, ["git", "switch"])
        var timeline = Timeline()
        timeline.apply(.permission(permission))
        timeline.apply(try XCTUnwrap(AlfalfaMessage.decode(line:
            #"{"version":2,"tipo":"permiso_resuelto","id":"p3","aprobado":true,"motivo":"Respuesta del cliente","siempre":true}"#)))
        guard case let .permission(_, _, state)? = timeline.items.last else { return XCTFail("tarjeta") }
        XCTAssertEqual(state, .approvedAlways)
    }

    func testAlwaysIsOnlyOfferedWithAPrefix() {
        let line = #"{"version":2,"tipo":"permiso","id":"p4","argv":["git","push"],"proposito":"Subir","motivo":"red","prefijo":[]}"#
        guard case let .permission(permission)? = AlfalfaMessage.decode(line: line) else { return XCTFail("permiso") }
        XCTAssertNil(permission.alwaysPrefix)
    }

    /// La tarjeta de red y la del modo también lo ofrecen, y la ayuda del botón dice lo que concede:
    /// antes decía siempre «corren fuera del sandbox», también en la del modo, que sigue dentro.
    func testAlwaysSaysWhatItGrants() {
        func decode(_ extra: String) -> PermissionRequest? {
            let line = #"{"version":2,"tipo":"permiso","id":"p5","argv":["npm","install"],"proposito":"Instalar","prefijo":["npm","install"],"#
                + extra + "}"
            guard case let .permission(permission)? = AlfalfaMessage.decode(line: line) else { return nil }
            return permission
        }
        let network = decode(#""motivo":"red","motivos":["red"],"prefijo_alcance":"red""#)
        XCTAssertEqual(network?.alwaysScope, .network)
        XCTAssertTrue(network?.alwaysScope?.help("npm install").contains("dentro del sandbox con la red abierta") ?? false)
        XCTAssertEqual(decode(#""motivo":"modo","prefijo_alcance":"sandbox""#)?.alwaysScope, .sandbox)
        XCTAssertEqual(decode(#""motivo":"salir_sandbox","prefijo_alcance":"sin_sandbox""#)?.alwaysScope, .outsideSandbox)
        // Un motor sin `prefijo_alcance`: se deduce de los motivos.
        XCTAssertEqual(decode(#""motivo":"red""#)?.alwaysScope, .network)
        XCTAssertEqual(decode(#""motivo":"modo""#)?.alwaysScope, .sandbox)
        XCTAssertEqual(decode(#""motivo":"reintento""#)?.alwaysScope, .outsideSandbox)
        XCTAssertEqual(decode(#""motivo":"salir_sandbox","motivos":["salir_sandbox","red"]"#)?.alwaysScope, .outsideSandbox)
        XCTAssertTrue(AlwaysScope.sandbox.help("make test").contains("dentro del sandbox, sin red"))
        XCTAssertTrue(AlwaysScope.outsideSandbox.help("git push").contains("fuera del sandbox"))
    }

    func testAlwaysAnswerLine() {
        XCTAssertEqual(AlfalfaCommand.permission(id: "p3", approved: true, always: true).line,
                       #"{"aprobado":true,"id":"p3","siempre":true,"tipo":"permiso"}"# + "\n")
        XCTAssertEqual(AlfalfaCommand.permission(id: "p3", approved: false, always: true).line,
                       #"{"aprobado":false,"id":"p3","tipo":"permiso"}"# + "\n")
    }
}
