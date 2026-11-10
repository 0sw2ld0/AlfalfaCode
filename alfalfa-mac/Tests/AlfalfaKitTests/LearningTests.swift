@testable import AlfalfaKit
import XCTest

/// Aprender de cada sesión (alfalfa/aprendizaje): lo que llega de `--memoria`, `--aprendido` y la revisión.
final class LearningTests: XCTestCase {
    func testDecodesMemoryStores() throws {
        let line = #"{"version":2,"tipo":"memoria","almacenes":[{"almacen":"usuario","titulo":"Usuario","ruta":"/h/m/USUARIO.md","#
            + #""limite":1375,"usados":27,"entradas":["Prefiere español.","Usa uv."]},{"almacen":"proyecto","#
            + #""titulo":"Proyecto","limite":2200,"usados":0,"entradas":[],"proyecto":"/repo"}],"#
            + #""ajustes":{"memoria":true,"habilidades":false,"revision":true,"aprobar_habilidades_usuario":true,"#
            + #""memoria_sin_preguntar":false,"modelo_revision":null},"proyecto":"/repo","aprende_aqui":false}"#
        guard case let .memory(state)? = AlfalfaMessage.decode(line: line) else { return XCTFail("memoria") }
        XCTAssertEqual(state.stores.map(\.store), ["usuario", "proyecto"])
        XCTAssertEqual(state.stores[0].text, "Prefiere español.\n§\nUsa uv.")
        XCTAssertEqual(state.stores[1].project, "/repo")
        XCTAssertFalse(state.settings.skills)
        XCTAssertNil(state.settings.reviewModel)
        XCTAssertFalse(state.learnsHere)
    }

    func testCountsCharactersLikeTheEngine() {
        XCTAssertEqual(MemoryStore.characters(of: "Prefiere español.\n§\nUsa uv."), 27)
        XCTAssertEqual(MemoryStore.characters(of: "  uno  \n§\n\n§\n dos "), "uno\n§\ndos".count)
        XCTAssertEqual(MemoryStore.characters(of: ""), 0)
    }

    func testDecodesLearnedSkillsAndProposals() throws {
        let line = #"{"version":2,"tipo":"aprendido","habilidades":[{"clave":"usuario:pasar-pruebas","#
            + #""nombre":"pasar-pruebas","alcance":"usuario","descripcion":"Cómo pasar las pruebas.","sesion":"abc","#
            + #""turno":"t9","origen":"revision","bloqueada":true,"estado":"cambiada","de_esta_carpeta":true,"ruta":"/h"}],"#
            + #""pendientes":[{"id":"0123456789ab","clase":"memoria","almacen":"usuario","motivo":"Lo dijo.","#
            + #""diff":"+Prefiere pytest -q."}],"ajustes":{},"proyecto":"/repo","aprende_aqui":true,"texto":"Bloqueada."}"#
        guard case let .learned(state)? = AlfalfaMessage.decode(line: line) else { return XCTFail("aprendido") }
        let skill = try XCTUnwrap(state.skills.first)
        XCTAssertEqual(skill.reference, "usuario:pasar-pruebas")
        XCTAssertTrue(skill.locked)
        XCTAssertEqual(skill.originText, "Propuesta al cerrar un turno · sesión abc · turno t9")
        XCTAssertEqual(skill.stateText, "Cambiada a mano: el agente ya no la toca")
        XCTAssertEqual(state.proposals.map(\.title), ["Guardar en la memoria usuario"])
        XCTAssertEqual(state.message, "Bloqueada.")
        XCTAssertTrue(state.settings.review)
        XCTAssertEqual(LearningSettings.Key.allCases.map(\.rawValue), ["memoria", "habilidades", "revision"])
    }

    func testDecodesSkillFiles() {
        let line = #"{"version":2,"tipo":"habilidad","habilidad":{"alcance":"proyecto","nombre":"x","#
            + #""archivos":{"SKILL.md":"---\nname: x\n---","refs/a.md":null}}}"#
        XCTAssertEqual(AlfalfaMessage.decode(line: line),
                       .learnedSkillFiles(reference: "proyecto:x", files: ["SKILL.md": "---\nname: x\n---"]))
    }

    func testProposalCardIsResolvedByItsAnswer() {
        var timeline = Timeline()
        let propuesta = #"{"version":2,"tipo":"aprendizaje_propuesto","id":"aaaaaaaaaaaa","clase":"habilidad","#
            + #""accion":"crear","alcance":"proyecto","nombre":"pasar-pruebas","motivo":"Repetible.","diff":"+x"}"#
        let otra = #"{"version":2,"tipo":"aprendizaje_propuesto","id":"bbbbbbbbbbbb","clase":"memoria","almacen":"usuario"}"#
        for line in [propuesta, otra] { timeline.apply(AlfalfaMessage.decode(line: line)!) }
        guard case let .learning(_, proposal, .pending) = timeline.items[0] else { return XCTFail("tarjeta") }
        XCTAssertEqual(proposal.title, "Crear la habilidad pasar-pruebas del proyecto")
        timeline.apply(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"aprendizaje_resuelto","#
            + #""id":"aaaaaaaaaaaa","aplicado":true,"texto":"Guardada la habilidad proyecto:pasar-pruebas."}"#)!)
        timeline.apply(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"aprendizaje_resuelto","#
            + #""id":"bbbbbbbbbbbb","aplicado":false,"descartada":true,"texto":"Hecho."}"#)!)
        // Reabrir la sesión vuelve a mandar las que esperan: no se duplican.
        timeline.apply(AlfalfaMessage.decode(line: propuesta)!)
        XCTAssertEqual(timeline.items.count, 2)
        timeline.apply(.learningResolved(id: "cccccccccccc", applied: false, text: "No existe."))
        guard case .learning(_, _, .applied("Guardada la habilidad proyecto:pasar-pruebas.")) = timeline.items[0],
              case .learning(_, _, .discarded) = timeline.items[1] else { return XCTFail("estados") }
        XCTAssertTrue(timeline.markdown(title: "x").contains("Aprendizaje propuesto"))
    }

    func testPermissionCarriesTheDiff() {
        let line = #"{"version":2,"tipo":"permiso","id":"p1","argv":["habilidad","crear","usuario:x"],"#
            + #""proposito":"Guardar la habilidad aprendida «x»","motivo":"aprendizaje","motivos":["aprendizaje"],"#
            + #""diff":"--- a\n+++ b\n+hola"}"#
        guard case let .permission(request)? = AlfalfaMessage.decode(line: line) else { return XCTFail("permiso") }
        XCTAssertEqual(request.reason, .learn)
        XCTAssertEqual(request.diff, "--- a\n+++ b\n+hola")
        XCTAssertTrue(request.scope?.contains("fuera del sandbox") ?? false)
    }

    func testAnswersTheCard() {
        XCTAssertEqual(AlfalfaCommand.learning(id: "aaaaaaaaaaaa", apply: true).line,
                       #"{"aplicar":true,"id":"aaaaaaaaaaaa","tipo":"aprendizaje"}"# + "\n")
    }

    func testSlashLearn() {
        XCTAssertEqual(SlashCommand.named("learn"), .aprender)
        XCTAssertTrue(SlashCommand.aprender.takesArgument)
        XCTAssertFalse(SlashCommand.aprender.available(running: true, dormant: false))
    }
}
