@testable import AlfalfaKit
import Foundation
import XCTest

final class BrowserExportTests: XCTestCase {

    // MARK: - Propiedades privadas

    private let id = "67e2dd44627b4540a443019008bf7a87"
    private let path = "tests/e2e/pedido.spec.ts"

    private var offer: [String: Any] {
        ["id": id, "ruta": path, "pasos": 3, "variables": [
            ["clave": "ALFALFA_E2E_BASE_URL", "paso": 1, "uso": "URL inicial del entorno"],
            ["clave": "ALFALFA_E2E_NOMBRE_2", "paso": 2, "uso": "Nombre accesible exacto", "rol": "button"],
            ["clave": "ALFALFA_E2E_TEXTO_3", "paso": 3, "uso": "Texto exacto observado"]
        ]]
    }

    // MARK: - Métodos públicos

    func testCierreConOfertaYSuComandoNoPierdeLaPruebaDeInterfaz() throws {
        let wrap = try XCTUnwrap(WrapUp(cierre: [
            "prueba_navegador": offer, "prueba_interfaz": ["estado": "incompleta", "notas": ["Falta captura final."]]
        ]))
        guard case let .ready(ident, destination, steps, variables)? = wrap.browserExport else {
            return XCTFail("No hay oferta de prueba.")
        }
        XCTAssertEqual(ident, id)
        XCTAssertEqual(destination, path)
        XCTAssertEqual(steps, 3)
        XCTAssertEqual(variables.map(\.step), [1, 2, 3])
        XCTAssertEqual(variables[1].role, "button")
        XCTAssertEqual(wrap.interfaceProof?.notes, ["Falta captura final."])
        let command = AlfalfaCommand.exportBrowserTest(id: ident, path: destination)
        let fields = try JSONSerialization.jsonObject(with: Data(command.line.utf8)) as? [String: String]
        XCTAssertEqual(fields, ["tipo": "exportar_prueba_navegador", "id": id, "ruta": path])
    }

    func testExplicaElPasoManualYDescartaRutaOFichaInvalida() {
        let reason = "El flujo requiere un relevo manual."
        let manual = WrapUp(cierre: ["prueba_navegador": ["motivo": reason]])
        XCTAssertEqual(manual?.browserExport, .manual(reason: reason))
        XCTAssertNil(manual?.browserExport?.id)
        var invalid = offer
        invalid["ruta"] = "tests/e2e/../fuera.spec.ts"
        XCTAssertNil(WrapUp(cierre: ["prueba_navegador": invalid]))
        invalid["ruta"] = path
        invalid["id"] = "del-otro-turno"
        XCTAssertNil(WrapUp(cierre: ["prueba_navegador": invalid]))
    }
}
