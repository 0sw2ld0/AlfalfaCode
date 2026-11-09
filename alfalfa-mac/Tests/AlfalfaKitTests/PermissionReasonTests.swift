@testable import AlfalfaKit
import XCTest

/// Motivos de la política on-request en el evento `permiso` de `alfalfa --stdio`.
final class PermissionReasonTests: XCTestCase {
    private func requests() throws -> [PermissionRequest] {
        try fixtureLines("permisos_bajo_demanda").map { line in
            guard case let .permission(request)? = AlfalfaMessage.decode(line: line) else {
                XCTFail("no es un permiso: \(line)")
                return PermissionRequest(id: "", argv: [], purpose: "")
            }
            return request
        }
    }

    func testDecodesTheFourReasonsWithTheirFields() throws {
        let rows = try requests()
        XCTAssertEqual(rows[0], PermissionRequest(id: "p1", argv: ["rm", "-rf", "basura"], purpose: "Borrar la carpeta desechable",
                                                  reason: .dangerous, rule: "rm con opción de fuerza"))
        XCTAssertEqual(rows[1].reason, .network)
        XCTAssertEqual(rows[1].reasons, [.network, .dangerous])
        XCTAssertEqual(rows[1].justification, "Necesita la especificación publicada")
        XCTAssertEqual(rows[2].reason, .leaveSandbox)
        XCTAssertEqual(rows[2].justification, "Escribe fuera del proyecto")
        XCTAssertEqual(rows[3].reason, .retry)
        XCTAssertEqual(rows[3].retryOf, "a3")
        XCTAssertEqual(rows[3].detail, "El intento a3 terminó con código 1 y una salida típica del sandbox.")
        XCTAssertEqual(rows.prefix(4).map(\.reason), Array(PermissionReason.allCases.prefix(4)))
    }

    func testEventsWithoutReasonStayCompatible() throws {
        let legacy = try requests()[4]
        XCTAssertEqual(legacy, PermissionRequest(id: "p5", argv: ["ls"], purpose: "Listar"))
        XCTAssertNil(legacy.reason)
        XCTAssertEqual(legacy.reasons, [])
        // Las grabaciones anteriores siguen decodificándose igual.
        for name in ["salidas", "sesion_con_permisos", "sesion_real"] {
            for line in try fixtureLines(name) {
                if case let .permission(request)? = AlfalfaMessage.decode(line: line) {
                    XCTAssertNil(request.reason, name)
                    XCTAssertNil(request.justification, name)
                }
            }
        }
    }

    func testUnknownReasonIsIgnoredAndWrongTypesAreDropped() throws {
        let future = try requests()[5]
        XCTAssertNil(future.reason)
        XCTAssertEqual(future.reasons, [.network])
        XCTAssertNil(future.justification)
    }

    func testEachReasonExplainsItsScope() {
        // modo y edicion los añaden los modos Manual y Aceptar ediciones; app, el control de apps.
        XCTAssertEqual(PermissionReason.allCases.map(\.rawValue),
                       ["peligroso", "red", "salir_sandbox", "reintento", "modo", "edicion", "app"])
        XCTAssertEqual(Set(PermissionReason.allCases.map(\.title)).count, 7)
        XCTAssertEqual(PermissionReason.allCases.filter(\.leavesSandbox), [.leaveSandbox, .retry])
        func scope(_ reasons: [PermissionReason]) -> String? {
            PermissionRequest(id: "x", argv: ["ls"], purpose: "", reason: reasons.first, reasons: reasons).scope
        }
        XCTAssertEqual(scope([.dangerous]), "Se ejecutará dentro del sandbox, sin red.")
        XCTAssertTrue(scope([.network])!.contains("escritura sigue limitada"))
        XCTAssertTrue(scope([.leaveSandbox])!.contains("sin sandbox"))
        XCTAssertTrue(scope([.retry])!.contains("una vez"))
        XCTAssertNil(scope([]))
    }

    /// Una sola decisión, un solo alcance: el del motivo más amplio, nunca el de «peligroso» si hay más.
    func testCombinedReasonsShowTheWidestScope() throws {
        let combined: [[PermissionReason]: String] = [
            [.network, .dangerous]: "red abierta",
            [.leaveSandbox, .dangerous]: "sin sandbox",
            [.leaveSandbox, .network, .dangerous]: "sin sandbox",
            [.retry, .dangerous]: "sin sandbox"
        ]
        for (reasons, expected) in combined {
            let scope = try XCTUnwrap(PermissionRequest(id: "x", argv: ["rm", "-rf", "x"], purpose: "",
                                                        reason: reasons.first, reasons: reasons).scope)
            XCTAssertTrue(scope.contains(expected), "\(reasons): \(scope)")
            XCTAssertFalse(scope.contains("sin red"), "\(reasons): \(scope)")
            XCTAssertFalse(scope.contains("dentro del sandbox") && reasons.contains(where: \.leavesSandbox), "\(reasons)")
        }
        XCTAssertTrue(try requests()[1].scope!.contains("red abierta"))
    }

    func testTimelineKeepsReasonAndOnlyExplicitAnswerResolves() throws {
        var timeline = Timeline()
        for line in try fixtureLines("permisos_bajo_demanda").prefix(4) {
            if let message = AlfalfaMessage.decode(line: line) { timeline.apply(message) }
        }
        XCTAssertEqual(timeline.status, .waitingPermission)
        XCTAssertEqual(timeline.pendingPermissions.map(\.reason), Array(PermissionReason.allCases.prefix(4)))
        timeline.apply(.permissionResolved(id: "p4", approved: false, reason: "Respuesta del cliente"))
        XCTAssertEqual(timeline.pendingPermissions.map(\.id), ["p1", "p2", "p3"])
    }
}
