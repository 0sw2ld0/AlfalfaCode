@testable import AlfalfaKit
import XCTest

final class InterfaceProofTests: XCTestCase {

    // MARK: - Propiedades privadas

    private let prefix = "/private/tmp/demo/capturas/navegador-interfaz-" + String(repeating: "a", count: 32)

    private var comparison: [String: Any] {
        ["medio": "web", "titulo": "Navegador · demo.test", "antes": prefix + "-antes.png",
         "despues": prefix + "-despues.png"]
    }

    // MARK: - Métodos públicos

    func testClosingProtocolAndHistoryKeepBothImagesAndOtherClosingFields() throws {
        let close: [String: Any] = ["resumen": "Botón actualizado", "sugerencia": "Mira el botón.",
                                   "prueba_interfaz": ["estado": "completa", "comparaciones": [comparison]],
                                   "revision_codigo": ["turno": "t1", "estado": "disponible", "autor": "autor"]]
        let data = try JSONSerialization.data(withJSONObject: ["version": 2, "tipo": "turno_terminado",
                                                               "estado": "completed", "cierre": close])
        guard case let .turnFinished(outcome)? = AlfalfaMessage.decode(line: String(decoding: data, as: UTF8.self))
        else { return XCTFail("Falta el cierre del turno") }
        let wrap = try XCTUnwrap(outcome.wrapUp)
        let proof = try XCTUnwrap(wrap.interfaceProof)
        XCTAssertEqual(proof, WrapUp(cierre: close)?.interfaceProof)
        XCTAssertTrue(proof.complete)
        XCTAssertEqual(proof.comparisons.first?.images.map(\.alt), ["Antes", "Después"])
        XCTAssertEqual(wrap.suggestion, "Mira el botón.")
        XCTAssertEqual(wrap.review?.state, "disponible")
        // El parser no exige que sigan en disco: la galería puede enseñar «Imagen no disponible» tras la poda.
        XCTAssertEqual(proof.comparisons.first?.before.path, prefix + "-antes.png")
    }

    func testIncompleteComparisonHasNotesAndNoInventedImages() throws {
        let proof = try XCTUnwrap(InterfaceProof(value: ["estado": "incompleta", "comparaciones": [],
                                                        "notas": ["Falta la captura anterior."]]))
        XCTAssertFalse(proof.complete)
        XCTAssertTrue(proof.comparisons.isEmpty)
        XCTAssertEqual(proof.notes, ["Falta la captura anterior."])
        XCTAssertNil(InterfaceProof(value: ["estado": "aprobada"]))
        XCTAssertNil(WrapUp(cierre: ["resumen": "Sólo texto"])?.interfaceProof)
    }

    func testRejectsRemotePathsTraversalAndUnrelatedPairs() throws {
        for bad in ["https://demo.test/antes.png", "file:///etc/hosts", "~/antes.png", "/etc/hosts",
                    "/private/tmp/demo/../capturas/" + (prefix as NSString).lastPathComponent + "-antes.png",
                    prefix.replacingOccurrences(of: "/capturas/", with: "/otra/") + "-antes.png"] {
            var item = comparison
            item["antes"] = bad
            let proof = try XCTUnwrap(InterfaceProof(value: ["estado": "completa", "comparaciones": [item]]))
            XCTAssertTrue(proof.comparisons.isEmpty, bad)
            XCTAssertFalse(proof.complete)
        }
        var other = comparison
        other["despues"] = prefix.replacingOccurrences(of: String(repeating: "a", count: 32),
                                                       with: String(repeating: "b", count: 32)) + "-despues.png"
        let proof = try XCTUnwrap(InterfaceProof(value: ["estado": "completa", "comparaciones": [other]]))
        XCTAssertTrue(proof.comparisons.isEmpty)
    }
}
