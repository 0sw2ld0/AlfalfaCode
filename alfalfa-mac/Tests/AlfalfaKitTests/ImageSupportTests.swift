@testable import AlfalfaKit
import XCTest

/// Las imágenes según el modelo: qué dice la lista de cada uno, a cuáles se puede cambiar y
/// qué pasa con un mensaje que el motor rechaza por traer imágenes que el modelo no ve.
final class ImageSupportTests: XCTestCase {

    // MARK: - Métodos públicos

    func testModelRowsSayWhetherTheySeeImages() {
        let line = #"{"version":2,"tipo":"modelos","modelos":["#
            + #"{"name":"qwen3-vl:8b","proveedor":"ollama","vision":true},"#
            + #"{"name":"glm-5.3:cloud","proveedor":"ollama","vision":false},"#
            + #"{"name":"openrouter/raro","proveedor":"openrouter","vision":null},"#
            + #"{"name":"viejo","proveedor":"ollama"}]}"#
        guard case let .models(rows)? = AlfalfaMessage.decode(line: line) else { return XCTFail("modelos") }
        XCTAssertEqual(rows.map(\.vision), [true, false, nil, nil])
        XCTAssertEqual(rows.map { ImageSupport.allows($0.vision) }, [true, false, true, true],
                       "con visión desconocida se deja adjuntar")
    }

    func testAlternativesAreUsableModelsWithVisionSameProviderFirst() {
        let models = [
            ModelInfo(name: "glm-5.3:cloud", vision: false),
            ModelInfo(name: "openrouter/x:free", label: "X gratis", provider: "openrouter", free: true,
                      missingKey: MissingKey(variable: "OPENROUTER_API_KEY", url: nil, text: "falta"), vision: true),
            ModelInfo(name: "alfalfa/gpt-6-luna", label: "GPT-6 Luna - Alfalfa", provider: "alfalfa", vision: true),
            ModelInfo(name: "nomic-embed-text", vision: true),
            ModelInfo(name: "qwen3-vl:8b", vision: true),
            ModelInfo(name: "desconocido"),
        ]
        XCTAssertEqual(ImageSupport.alternatives(to: "glm-5.3:cloud", in: models), [
            ModelChoice(model: "qwen3-vl:8b", label: "qwen3-vl:8b"),
            ModelChoice(model: "alfalfa/gpt-6-luna", label: "GPT-6 Luna - Alfalfa"),
        ])
        XCTAssertEqual(ImageSupport.alternatives(to: "glm-5.3:cloud", in: models, limit: 1).count, 1)
    }

    func testRejectedMessageDecodes() {
        let line = #"{"version":2,"tipo":"error","texto":"El modelo glm-5.3 no ve imágenes.","motivo":"sin_vision","#
            + #""rechazado":{"texto":"¿Qué falla?","imagenes":["/tmp/a.png"]},"#
            + #""con_vision":[{"modelo":"qwen3-vl:8b","nombre":"Qwen3 VL"}]}"#
        XCTAssertEqual(AlfalfaMessage.decode(line: line), .messageRejected(MessageRejection(
            message: "El modelo glm-5.3 no ve imágenes.", reason: "sin_vision", text: "¿Qué falla?",
            images: ["/tmp/a.png"], alternatives: [ModelChoice(model: "qwen3-vl:8b", label: "Qwen3 VL")])))
        // Un error normal sigue siendo un error.
        XCTAssertEqual(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"error","texto":"x"}"#), .error("x"))
    }

    func testRejectedMessageLeavesTheConversationAndTheSessionStaysReady() {
        var timeline = Timeline()
        timeline.apply(.session(SessionInfo(id: "s", cwd: "/p", model: "glm-5.3:cloud", effort: "low", mode: "auto")))
        timeline.addUser("Primero", queued: false)
        timeline.addUser(ImageTags.join("¿Qué falla?", images: ["/tmp/a.png"]), queued: true)
        timeline.apply(.messageRejected(MessageRejection(message: "El modelo glm-5.3 no ve imágenes.",
                                                         reason: "sin_vision", text: "¿Qué falla?",
                                                         images: ["/tmp/a.png"])))
        let users = timeline.items.compactMap { item -> String? in
            if case let .user(_, text, _) = item { return text } else { return nil }
        }
        XCTAssertEqual(users, ["Primero"])
        XCTAssertTrue(timeline.items.contains { if case .notice(_, "El modelo glm-5.3 no ve imágenes.") = $0 { true } else { false } })
        XCTAssertEqual(timeline.status, .idle, "sin turno en marcha, la sesión sigue lista")
    }
}
