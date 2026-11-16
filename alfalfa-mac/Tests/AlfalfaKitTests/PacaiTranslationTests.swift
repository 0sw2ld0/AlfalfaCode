@testable import AlfalfaKit
import XCTest

/// Cada evento v2 que la app lee en una sesión, pasado por el traductor de `--pacai`
/// (`scripts/transcripciones_pacai_app.py traducciones`): el cliente PACAI de la app lo devuelve igual que el
/// decodificador v2, con sus valores enumerados (estados de la cola, de la revisión, de los subagentes, motivos…).
final class PacaiTranslationTests: XCTestCase {

    // MARK: - Métodos

    func testEveryV2EventComesBackTheSameThroughPacai() throws {
        let url = try XCTUnwrap(Bundle.module.url(forResource: "traducciones", withExtension: "json",
                                                  subdirectory: "Fixtures/pacai"))
        let object = try XCTUnwrap(try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
        let cases = try XCTUnwrap(object["casos"] as? [[String: Any]])
        let client = PacaiClient()
        var compared = 0
        for item in cases {
            let line = String(decoding: try JSONSerialization.data(withJSONObject: item["v2"] as Any), as: UTF8.self)
            let v2 = AlfalfaMessage.decode(line: line).map { [$0] } ?? []
            var pacai: [AlfalfaMessage] = []
            for message in item["pacai"] as? [[String: Any]] ?? [] {
                let text = String(decoding: try JSONSerialization.data(withJSONObject: message), as: UTF8.self)
                pacai += client.receive(text).events
            }
            XCTAssertEqual(PacaiTranscript.normalized(pacai), PacaiTranscript.normalized(v2), "\(item["v2"] ?? "")")
            compared += v2.count
        }
        XCTAssertGreaterThan(compared, 45)
    }
}
