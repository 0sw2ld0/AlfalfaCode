@testable import AlfalfaKit
import XCTest

/// Volver a un mensaje con el código y rehacer: las líneas son las que emite `alfalfa --volver/--rehacer --json`.
final class UndoTests: XCTestCase {
    private let preview = #"{"version": 2, "tipo": "deshacer_vista", "sesion": "s1", "alcance": "codigo", "#
        + #""turno": "d9b03a99c23e471c", "disponible": true, "restaurables": [{"ruta": "b.py", "accion": "borrar"}], "#
        + #""conflictos": [{"ruta": "app.py", "accion": "restaurar", "motivo": "editado_despues"}], "grandes": [], "#
        + #""irreversibles": [{"tipo": "publicacion", "titulo": "Publicó fuera de tu Mac", "#
        + #""ordenes": ["git push origin main"]}, {"tipo": "instalacion", "titulo": "Instaló o actualizó paquetes", "#
        + #""ordenes": ["npm install left-pad"]}], "primer_turno": true}"#

    private let undone = #"{"version": 2, "tipo": "deshecho", "sesion": "s1", "texto": "Deshecho: 2 archivos vuelven "#
        + #"a antes del mensaje.", "id": "6e20e183c86e", "paso": "deshacer", "turno": "d9b03a99c23e471c", "de": null, "#
        + #""restaurados": ["b.py", "app.py"], "conservados": [], "fallos": [], "irreversibles": [], "grandes": []}"#

    func testPreviewSaysWhatComesBackWhatStaysAndWhatCannotBeUndone() {
        guard case let .undoPreview(value)? = AlfalfaMessage.decode(line: preview) else { return XCTFail("vista") }
        XCTAssertTrue(value.available)
        XCTAssertTrue(value.firstTurn)
        XCTAssertEqual(value.restorable, [UndoFile(path: "b.py", action: "borrar")])
        XCTAssertEqual(value.restorable.first?.actionLabel, "se borra")
        XCTAssertEqual(value.conflicts.first?.reasonLabel, "Lo cambiaste después del turno")
        XCTAssertEqual(value.warnings.map(\.kind), ["publicacion", "instalacion"])
        XCTAssertEqual(value.warnings.first?.commands, ["git push origin main"])
        XCTAssertFalse(value.nothingToDo)
    }

    func testUnavailablePreviewKeepsItsReason() {
        let line = #"{"version":2,"tipo":"deshacer_vista","sesion":"s1","turno":"t","disponible":false,"#
            + #""motivo":"Ese turno no tiene instantánea del código.","restaurables":[],"conflictos":[]}"#
        guard case let .undoPreview(value)? = AlfalfaMessage.decode(line: line) else { return XCTFail("vista") }
        XCTAssertFalse(value.available)
        XCTAssertEqual(value.reason, "Ese turno no tiene instantánea del código.")
    }

    func testRedoPreviewNamesTheUndo() {
        let line = #"{"version": 2, "tipo": "rehacer_vista", "sesion": "s1", "deshecho": "6e20e183c86e", "#
            + #""turno": "d9b03a99c23e471c", "disponible": true, "restaurables": [{"ruta": "app.py", "#
            + #""accion": "restaurar"}, {"ruta": "b.py", "accion": "recrear"}], "conflictos": [], "grandes": [], "#
            + #""irreversibles": []}"#
        guard case let .undoPreview(value)? = AlfalfaMessage.decode(line: line) else { return XCTFail("vista") }
        XCTAssertEqual(value.redoOf, "6e20e183c86e")
        XCTAssertEqual(value.restorable.map(\.actionLabel), ["se restaura", "vuelve a estar"])
    }

    func testUndoneAndRedoMarksInTheTimeline() {
        guard case let .undone(step)? = AlfalfaMessage.decode(line: undone) else { return XCTFail("deshecho") }
        XCTAssertEqual(step.session, "s1")
        XCTAssertEqual(step.restored, ["b.py", "app.py"])
        XCTAssertFalse(step.isRedo)
        var timeline = Timeline()
        timeline.apply(.undone(step))
        XCTAssertEqual(timeline.redoableUndo?.id, "6e20e183c86e")
        let redo = UndoStep(id: "r1", kind: "rehacer", redoOf: "6e20e183c86e", restored: ["app.py"], text: "Rehecho")
        timeline.addUndo(redo)
        guard case let .undo(_, first, redone) = timeline.items[0] else { return XCTFail("marca") }
        XCTAssertEqual(first.id, "6e20e183c86e")
        XCTAssertTrue(redone, "el rehacer deja sin botón el deshacer que devuelve")
        XCTAssertNil(timeline.redoableUndo)
        XCTAssertTrue(timeline.markdown(title: "x").contains("> Rehecho"))
    }

    func testHistoryRowCarriesTheUndo() {
        let line = #"{"version":2,"tipo":"historial","sesion":"s1","eventos":["#
            + #"{"rol":"user","texto":"uno","hora":1,"turno":"t1"},"#
            + #"{"rol":"system","texto":"Deshecho: 1 archivo vuelve a antes del mensaje.","hora":2,"turno":"t1","#
            + #""deshacer":{"id":"u1","paso":"deshacer","turno":"t1","de":null,"restaurados":["a.py"],"#
            + #""conservados":["b.py"],"fallos":[{"ruta":"c.py","error":"x"}],"irreversibles":[],"grandes":[]}}]}"#
        guard case let .history(_, events, _, _, _, _)? = AlfalfaMessage.decode(line: line) else {
            return XCTFail("historial")
        }
        let timeline = Timeline(history: events, wrapUp: nil)
        guard case let .undo(_, step, false)? = timeline.items.last else { return XCTFail("marca") }
        XCTAssertEqual(step.kept, ["b.py"])
        XCTAssertEqual(step.failed, ["c.py"])
        XCTAssertEqual(timeline.redoableUndo?.id, "u1")
    }

    func testScopes() {
        XCTAssertEqual(UndoScope.allCases.map(\.title), ["Conversación y código", "Sólo conversación", "Sólo código"])
        XCTAssertFalse(UndoScope.conversation.touchesCode)
        XCTAssertEqual(UndoScope.code.rawValue, "codigo")
    }
}
