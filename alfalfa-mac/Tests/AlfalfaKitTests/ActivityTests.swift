@testable import AlfalfaKit
import XCTest

/// La fila viva dice qué hace el agente («Leyendo WorkLog.swift», «Pensando») y cuánto lleva,
/// con las horas de los eventos; la hoja se mueve según la fase.
final class ActivityTests: XCTestCase {
    private func line(_ object: [String: Any]) -> String {
        var row = object
        row["version"] = protocolVersion
        row["tipo"] = "evento"
        let data = (try? JSONSerialization.data(withJSONObject: row)) ?? Data()
        return String(decoding: data, as: UTF8.self)
    }

    private func apply(_ timeline: inout Timeline, _ rows: [[String: Any]]) {
        for row in rows {
            guard let message = AlfalfaMessage.decode(line: line(row)) else { return XCTFail("no se leyó \(row)") }
            timeline.apply(message)
        }
    }

    private func start(_ name: String, _ description: String, ongoing: String?, at time: Double) -> [String: Any] {
        var tool: [String: Any] = ["nombre": name, "fase": "inicio", "descripcion": description]
        if let ongoing { tool["en_curso"] = ongoing }
        return ["rol": "tool", "texto": "Inicio \(name) · {}", "hora": time, "herramienta": tool]
    }

    private func end(_ name: String, at time: Double) -> [String: Any] {
        ["rol": "tool", "texto": name + "\n{\"output\": \"ok\"}", "hora": time,
         "herramienta": ["nombre": name, "fase": "fin"]]
    }

    private func group(_ timeline: Timeline) -> [ToolEvent] {
        for item in timeline.items.reversed() { if case let .events(_, events) = item { return events } }
        return []
    }

    // MARK: - Tiempo

    func testElapsedTextIsShortLikeClaude() {
        XCTAssertEqual(ElapsedTime.text(0), "0s")
        XCTAssertEqual(ElapsedTime.text(0.9), "0s")
        XCTAssertEqual(ElapsedTime.text(38), "38s")
        XCTAssertEqual(ElapsedTime.text(59.99), "59s")
        XCTAssertEqual(ElapsedTime.text(60), "1min")
        XCTAssertEqual(ElapsedTime.text(67), "1min 7s")
        XCTAssertEqual(ElapsedTime.text(3599), "59min 59s")
        XCTAssertEqual(ElapsedTime.text(3600), "1h")
        XCTAssertEqual(ElapsedTime.text(3600 + 125), "1h 2min")
        XCTAssertEqual(ElapsedTime.text(-5), "0s")
    }

    func testElapsedSpokenForVoiceOver() {
        XCTAssertEqual(ElapsedTime.spoken(1), "1 segundo")
        XCTAssertEqual(ElapsedTime.spoken(38), "38 segundos")
        XCTAssertEqual(ElapsedTime.spoken(60), "1 minuto")
        XCTAssertEqual(ElapsedTime.spoken(67), "1 minuto 7 segundos")
        XCTAssertEqual(ElapsedTime.spoken(125), "2 minutos 5 segundos")
        XCTAssertEqual(ElapsedTime.spoken(3600), "1 hora")
        XCTAssertEqual(ElapsedTime.spoken(7200 + 60), "2 horas 1 minuto")
    }

    func testSpokenLabelJoinsPhraseAndTime() {
        let activity = LiveActivity.thinking(since: 1000)
        XCTAssertEqual(activity.spoken(at: Date(timeIntervalSince1970: 1038.6)), "Pensando, 38 segundos")
        XCTAssertEqual(LiveActivity.thinking(since: nil).spoken(at: Date()), "Pensando")
        XCTAssertNil(LiveActivity.thinking(since: nil).elapsed(at: Date()))
    }

    // MARK: - Fases

    func testWorkingUsesTheEnginePhraseAndTheStartTime() {
        var timeline = Timeline()
        timeline.addUser("Mira el registro", queued: false)
        apply(&timeline, [start("read_file", "Leer WorkLog.swift", ongoing: "Leyendo WorkLog.swift", at: 1000)])
        let activity = LiveActivity.of(group: group(timeline))
        XCTAssertEqual(activity, LiveActivity(phase: .working, phrase: "Leyendo WorkLog.swift", since: 1000))
        XCTAssertEqual(activity.elapsed(at: Date(timeIntervalSince1970: 1067)), 67)
    }

    func testBetweenCallsItThinksSinceTheLastResult() {
        var timeline = Timeline()
        timeline.addUser("Mira el registro", queued: false)
        apply(&timeline, [start("read_file", "Leer a.py", ongoing: "Leyendo a.py", at: 1000),
                          end("read_file", at: 1004)])
        XCTAssertEqual(LiveActivity.of(group: group(timeline)), .thinking(since: 1004))
        // Y la llamada terminada lleva lo que tardó.
        XCTAssertEqual(ToolCalls.calls(from: group(timeline)).first?.duration, 4)
    }

    func testOlderEnginesWithoutOngoingUseTheDescription() {
        var timeline = Timeline()
        timeline.addUser("Hola", queued: false)
        apply(&timeline, [start("run_command", "Pasar las pruebas", ongoing: nil, at: 10)])
        XCTAssertEqual(LiveActivity.of(group: group(timeline)).phrase, "Pasar las pruebas")
    }

    func testWaitingForPermissionNamesWhatItWaitsFor() {
        var timeline = Timeline()
        timeline.addUser("Compila", queued: false)
        apply(&timeline, [start("run_command", "Pasar las pruebas", ongoing: "Pasando las pruebas", at: 50)])
        let waiting = LiveActivity.of(group: group(timeline), waiting: true)
        XCTAssertEqual(waiting, LiveActivity(phase: .waiting, phrase: "Esperando tu permiso para pasar las pruebas",
                                             since: 50))
        // Una descripción que el motor no pudo pasar a gerundio (otro idioma) no se encaja en la frase.
        var english = Timeline()
        english.addUser("Build", queued: false)
        apply(&english, [start("run_command", "Build the app", ongoing: "Build the app", at: 50)])
        XCTAssertEqual(LiveActivity.of(group: group(english), waiting: true).phrase, "Esperando tu permiso")
    }

    func testThinkingSinceTheLastMessage() {
        var timeline = Timeline()
        apply(&timeline, [["rol": "user", "texto": "Arregla la suma", "hora": 500]])
        XCTAssertEqual(timeline.thinkingSince, 500)
        apply(&timeline, [["rol": "agent", "texto": "Voy a mirar.", "hora": 520]])
        XCTAssertEqual(timeline.thinkingSince, 520)
        apply(&timeline, [start("read_file", "Leer a.py", ongoing: "Leyendo a.py", at: 530), end("read_file", at: 531)])
        XCTAssertEqual(timeline.thinkingSince, 531)
        // Un mensaje en cola no cuenta: el agente aún no lo ha leído.
        timeline.addUser("Y otra cosa", queued: true)
        XCTAssertEqual(timeline.thinkingSince, 531)
    }

    func testDecodesOngoingPhrase() {
        let message = AlfalfaMessage.decode(line: line(start("read_file", "Leer a.py", ongoing: "Leyendo a.py", at: 1)))
        guard case let .event(event)? = message else { return XCTFail("evento") }
        XCTAssertEqual(event.tool?.ongoing, "Leyendo a.py")
        XCTAssertEqual(event.tool?.description, "Leer a.py")
    }

    // MARK: - La hoja

    func testStillPhasesDoNotMove() {
        for phase in [LiveActivity.Phase.waiting, .done] {
            XCTAssertFalse(LeafMotion.isAnimated(phase))
            for time in stride(from: 0.0, to: 3, by: 0.25) {
                XCTAssertEqual(LeafMotion.pose(phase, leaflet: 1, time: time, reduceMotion: false), .still)
                XCTAssertEqual(LeafMotion.sway(phase, time: time, reduceMotion: false), 0)
            }
        }
    }

    func testLeafletsTakeTurns() {
        for phase in [LiveActivity.Phase.thinking, .working] {
            let poses = (0..<3).map { LeafMotion.pose(phase, leaflet: $0, time: 0.5, reduceMotion: false) }
            XCTAssertEqual(Set(poses.map(\.opacity)).count, 3, "cada foliolo en su momento de la ola")
            for pose in poses {
                XCTAssertGreaterThanOrEqual(pose.opacity, 0.35)
                XCTAssertLessThanOrEqual(pose.opacity, 1)
            }
        }
        // Trabajando, la hoja se mece; pensando, no.
        XCTAssertNotEqual(LeafMotion.sway(.working, time: 0.4, reduceMotion: false), 0)
        XCTAssertEqual(LeafMotion.sway(.thinking, time: 0.4, reduceMotion: false), 0)
    }

    func testReduceMotionOnlyFadesTheWholeLeaf() {
        for phase in [LiveActivity.Phase.thinking, .working] {
            for time in stride(from: 0.0, to: 3, by: 0.2) {
                let poses = (0..<3).map { LeafMotion.pose(phase, leaflet: $0, time: time, reduceMotion: true) }
                XCTAssertEqual(Set(poses).count, 1, "los tres iguales")
                XCTAssertEqual(poses[0].scale, 1)
                XCTAssertGreaterThanOrEqual(poses[0].opacity, 0.6)
                XCTAssertEqual(LeafMotion.sway(phase, time: time, reduceMotion: true), 0)
            }
        }
    }
}
