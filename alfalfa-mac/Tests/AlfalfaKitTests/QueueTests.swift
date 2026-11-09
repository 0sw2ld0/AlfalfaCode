@testable import AlfalfaKit
import XCTest

/// Mensajes en cola: el eco del motor los entrega, y «Enviar ahora» o «Redirigir» los adelantan.
final class QueueTests: XCTestCase {
    func testCommands() {
        XCTAssertEqual(AlfalfaCommand.sendNow("Para").line, #"{"texto":"Para","tipo":"enviar_ahora"}"# + "\n")
        XCTAssertEqual(AlfalfaCommand.redirect.line, #"{"tipo":"redirigir"}"# + "\n")
    }

    func testSendNowCarriesTheQueuedMessageID() {
        var timeline = Timeline()
        timeline.apply(.event(AgentEvent(role: "user", text: "primero", time: 1)))
        _ = timeline.addUser("Igual", queued: true, queueID: "q1")
        let second = timeline.addUser("Igual", queued: true, queueID: "q2")
        // Dos mensajes con el mismo texto: «Enviar ahora» del segundo lleva su id, no el del primero.
        let id = timeline.queueIDs[second]
        XCTAssertEqual(id, "q2")
        XCTAssertEqual(AlfalfaCommand.sendNow("Igual", id: id).line,
                       #"{"id":"q2","texto":"Igual","tipo":"enviar_ahora"}"# + "\n")
        // Ya entregado no tiene id de cola: «Enviar ahora» no tendría a quién adelantar.
        timeline.apply(.event(AgentEvent(role: "user", text: "Igual", time: 2)))
        timeline.apply(.event(AgentEvent(role: "user", text: "Igual", time: 3)))
        XCTAssertNil(timeline.queueIDs[second])
    }

    func testEchoDeliversTheQueuedReplyAndACutTurnLeavesTheSessionReady() {
        var timeline = Timeline()
        timeline.apply(.event(AgentEvent(role: "user", text: "primero", time: 1)))
        timeline.apply(.turnFinished(Outcome(state: "completed", summary: "", exitCode: 0, tokens: 1)))
        timeline.addUser("No, otra cosa", queued: true)
        timeline.apply(.event(AgentEvent(role: "user", text: "No, otra cosa", time: 2)))
        XCTAssertEqual(timeline.status, .working)
        guard case let .user(_, text, queued)? = timeline.items.last else { return XCTFail("mensaje") }
        XCTAssertEqual(text, "No, otra cosa")
        XCTAssertFalse(queued, "el eco del motor lo marca entregado")
        XCTAssertEqual(timeline.items.filter { if case .user = $0 { return true } else { return false } }.count, 2)
        // «Enviar ahora» corta el turno: la sesión queda lista, no interrumpida.
        timeline.apply(.turnFinished(Outcome(state: "cortado", summary: "Turno interrumpido por el usuario.",
                                             exitCode: 1, tokens: 1)))
        XCTAssertEqual(timeline.status, .idle)
        XCTAssertTrue(timeline.status.isActive)
    }

    // MARK: - Quitar y Editar

    func testMessageWithIDAndUnqueueCommand() {
        XCTAssertEqual(AlfalfaCommand.message("hola", id: "q1").line,
                       #"{"id":"q1","texto":"hola","tipo":"mensaje"}"# + "\n")
        XCTAssertEqual(AlfalfaCommand.message("", images: ["/a.png"], id: "q2").line,
                       #"{"id":"q2","imagenes":["/a.png"],"texto":"","tipo":"mensaje"}"# + "\n")
        // Sin id, como antes.
        XCTAssertEqual(AlfalfaCommand.message("hola").line, #"{"texto":"hola","tipo":"mensaje"}"# + "\n")
        XCTAssertEqual(AlfalfaCommand.unqueue("q1").line, #"{"id":"q1","tipo":"quitar_de_cola"}"# + "\n")
    }

    func testDecodesUnqueued() {
        XCTAssertEqual(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"quitado_de_cola","id":"q1","quitado":true}"#),
                       .unqueued(id: "q1", removed: true, reason: nil))
        XCTAssertEqual(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"quitado_de_cola","id":"q1","quitado":false,"#
                                             + #""motivo":"entregado"}"#),
                       .unqueued(id: "q1", removed: false, reason: "entregado"))
        XCTAssertNil(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"quitado_de_cola","quitado":true}"#))
        XCTAssertNil(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"quitado_de_cola","id":"q1"}"#))
    }

    private func running() -> Timeline {
        var timeline = Timeline()
        timeline.apply(.event(AgentEvent(role: "user", text: "primero", time: 1)))
        return timeline
    }

    private func users(_ timeline: Timeline) -> [String] {
        timeline.items.compactMap { if case let .user(_, text, _) = $0 { return text } else { return nil } }
    }

    func testRemoveBeforeItEntersTakesItOutWithItsText() {
        var timeline = running()
        let item = timeline.addUser(ImageTags.join("Mira esto", images: ["/tmp/a.png"]), queued: true, queueID: "q1")
        XCTAssertEqual(timeline.requestUnqueue(item: item, action: .edit), "q1")
        XCTAssertTrue(timeline.isUnqueuing(item: item))
        // Un segundo clic no vuelve a pedirlo.
        XCTAssertNil(timeline.requestUnqueue(item: item, action: .remove))
        let gone = timeline.unqueued(id: "q1", removed: true, reason: nil)
        XCTAssertEqual(gone?.action, .edit)
        XCTAssertEqual(gone.map { ImageTags.split($0.text).text }, "Mira esto")
        XCTAssertEqual(gone.map { ImageTags.split($0.text).images }, ["/tmp/a.png"])
        XCTAssertEqual(users(timeline), ["primero"])
        XCTAssertFalse(timeline.isUnqueuing(item: item))
        // Una respuesta repetida no hace nada.
        XCTAssertNil(timeline.unqueued(id: "q1", removed: true, reason: nil))
    }

    func testAlreadyReadStaysInTheConversationWithANotice() {
        var timeline = running()
        let item = timeline.addUser("Céntrate", queued: true, queueID: "q1")
        XCTAssertEqual(timeline.requestUnqueue(item: item, action: .remove), "q1")
        // El eco del motor llega antes que la respuesta: ya no está en cola.
        timeline.apply(.event(AgentEvent(role: "user", text: "Céntrate", time: 2)))
        XCTAssertNil(timeline.unqueued(id: "q1", removed: false, reason: "entregado"))
        XCTAssertEqual(users(timeline), ["primero", "Céntrate"])
        guard case let .notice(_, text)? = timeline.items.last else { return XCTFail("aviso") }
        XCTAssertEqual(text, Timeline.alreadyRead)
        XCTAssertTrue(text.hasPrefix("Ya lo leyó el agente"))
    }

    func testAlreadyReadBeforeTheEchoKeepsItQueuedUntilItArrives() {
        var timeline = running()
        let item = timeline.addUser("Otra", queued: true, queueID: "q1")
        _ = timeline.requestUnqueue(item: item, action: .edit)
        XCTAssertNil(timeline.unqueued(id: "q1", removed: false, reason: "entregado"))
        XCTAssertFalse(timeline.isUnqueuing(item: item))
        XCTAssertEqual(users(timeline), ["primero", "Otra"])
    }

    func testUnknownToTheEngineIsRemovedHere() {
        var timeline = running()
        let item = timeline.addUser("Perdido", queued: true, queueID: "q1")
        _ = timeline.requestUnqueue(item: item, action: .remove)
        XCTAssertEqual(timeline.unqueued(id: "q1", removed: false, reason: "desconocido")?.text, "Perdido")
        XCTAssertEqual(users(timeline), ["primero"])
    }

    func testWithoutIDOrDeliveredThereIsNothingToAsk() {
        var timeline = running()
        let withoutID = timeline.addUser("Sin id", queued: true)
        XCTAssertNil(timeline.requestUnqueue(item: withoutID, action: .remove))
        let delivered = timeline.addUser("Entregado", queued: true, queueID: "q2")
        timeline.apply(.event(AgentEvent(role: "user", text: "Entregado", time: 2)))
        XCTAssertNil(timeline.requestUnqueue(item: delivered, action: .remove))
        XCTAssertNil(timeline.removeQueued(item: delivered), "lo entregado no se quita")
    }

    func testProcessGoneFreesPendingRequestsAndRemovesLocally() {
        var timeline = running()
        let item = timeline.addUser("Luego", queued: true, queueID: "q1")
        _ = timeline.requestUnqueue(item: item, action: .remove)
        timeline.processExited(code: 1)
        XCTAssertFalse(timeline.isUnqueuing(item: item))
        XCTAssertEqual(timeline.removeQueued(item: item), "Luego")
        XCTAssertEqual(users(timeline), ["primero"])
    }
}
