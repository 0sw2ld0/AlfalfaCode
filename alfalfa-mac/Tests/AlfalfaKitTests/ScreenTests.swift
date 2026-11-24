@testable import AlfalfaKit
import XCTest

/// Las pantallas del perfil `ui` de PACAI: leer los bloques, aplicarlos, los eventos y lo que vuelve al agente.
final class ScreenTests: XCTestCase {

    // MARK: - Constantes

    private static let tickets: [String: Any] = [
        "view": "tickets", "op": "patch", "rev": 1, "placement": "latest", "state": ["priority": "medium"],
        "nodes": [
            ["id": "root", "component": "Stack", "children": ["titulo", "falta", "crear", "titulo"]],
            ["id": "titulo", "component": "Text", "props": ["text": ["$": "count"]]],
            ["id": "crear", "component": "Button", "props": ["label": "Crear ticket"],
             "events": ["press": [["set": ["abierto": true]], ["send": "crear", "with": ["priority"]]]]],
            ["component": "Text"]
        ]
    ]

    // MARK: - Modelo

    func testAnUpdateIsReadFromItsBlockAndTheRestIsIgnored() throws {
        let blocks: [[String: Any]] = [
            ["type": "text", "text": "Hola"],
            ["type": "structured", "schema": "otra/1", "data": ["view": "x", "op": "patch", "rev": 1]],
            ["type": "structured", "schema": "pacai.ui/1", "data": Self.tickets]
        ]
        let updates = ScreenUpdate.list(ScreenUpdate.blocks(blocks))
        let update = try XCTUnwrap(updates.first)
        XCTAssertEqual(updates.count, 1)
        XCTAssertEqual(update.view, "tickets")
        XCTAssertEqual(update.nodes.map(\.id), ["root", "titulo", "crear"])
        XCTAssertEqual(update.state, ["priority": .string("medium")])
        XCTAssertEqual(update.placement, .latest)
        XCTAssertEqual(update.nodes[2].events["press"], [.set(["abierto": .bool(true)]),
                                                         .send(name: "crear", keys: ["priority"])])
        // Sin `view`, `op` o `rev` no es un bloque; un `open_url` sin https no es una acción.
        XCTAssertNil(ScreenUpdate(json: ["view": "x", "rev": 1]))
        XCTAssertEqual(ScreenAction.list(["open_url": "http://ejemplo.com"]), [])
    }

    func testPatchesMergeReplaceStartsOverAndOldRevisionsAreIgnored() throws {
        let first = try XCTUnwrap(Screen(name: "tickets").applying(XCTUnwrap(ScreenUpdate(json: Self.tickets))))
        let patch = ScreenUpdate(view: "tickets", rev: 2, nodes: [ScreenNode(id: "titulo", component: "Text",
                                                                             props: ["text": .string("4")])],
                                 remove: ["crear"], state: ["title": .string("")])
        let second = try XCTUnwrap(first.applying(patch))
        XCTAssertEqual(second.nodes["titulo"]?.props["text"], .string("4"))
        XCTAssertNil(second.nodes["crear"])
        XCTAssertEqual(second.state, ["priority": .string("medium"), "title": .string("")])
        XCTAssertNil(second.applying(patch), "la misma revisión otra vez no cuenta")
        XCTAssertNil(second.applying(ScreenUpdate(view: "otra", rev: 9)))
        let replaced = try XCTUnwrap(second.applying(ScreenUpdate(view: "tickets", replace: true, rev: 3,
                                                                  nodes: [ScreenNode(id: "root", component: "Stack")],
                                                                  alt: "Vacía")))
        XCTAssertEqual(Array(replaced.nodes.keys), ["root"])
        XCTAssertEqual(replaced.state, [:])
        XCTAssertEqual(replaced.alt, "Vacía")
        XCTAssertEqual(replaced.placement, .latest, "la pista se mantiene hasta que llegue otra")
    }

    func testPropsReadTheStateAndChildrenComeInOrderOnce() throws {
        let screen = try XCTUnwrap(Screen(name: "tickets").applying(XCTUnwrap(ScreenUpdate(json: Self.tickets))))
        let root = try XCTUnwrap(screen.nodes["root"])
        XCTAssertEqual(screen.children(of: root).map(\.id), ["titulo", "crear"], "sin el que falta ni el repetido")
        let title = try XCTUnwrap(screen.nodes["titulo"])
        XCTAssertEqual(title.text("text", in: ["count": .number(3)]), "3")
        XCTAssertNil(title.text("text", in: [:]))
    }

    // MARK: - Eventos

    func testAnEventRunsItsSetsAndOneSendWithTheStateAfterThem() throws {
        let screen = try XCTUnwrap(Screen(name: "tickets").applying(XCTUnwrap(ScreenUpdate(json: Self.tickets))))
        let button = try XCTUnwrap(screen.nodes["crear"])
        let result = screen.handle("press", on: button, state: ["priority": .string("high")])
        XCTAssertEqual(result.state, ["priority": .string("high"), "abierto": .bool(true)])
        let request = try XCTUnwrap(result.request)
        XCTAssertEqual(request.values, ["priority": .string("high")])
        XCTAssertEqual(request.summary, "Pulsé «Crear ticket»")
        let data = request.data
        XCTAssertEqual(data["view"] as? String, "tickets")
        XCTAssertEqual(data["rev"] as? Int, 1)
        XCTAssertEqual(data["name"] as? String, "crear")
        XCTAssertNil(data["item"])
        XCTAssertNil(screen.handle("submit", on: button, state: [:]).request, "un evento que el nodo no tiene")
    }

    func testOnlyTheFirstSendLeavesAndRowsTravelAsTheItem() throws {
        let row: ScreenValue = .object(["id": .string("T-12"), "title": .string("Login")])
        let table = ScreenNode(id: "tabla", component: "Table", events: ["row_press": [
            .send(name: "abrir", keys: []), .send(name: "otra", keys: nil), .openURL(URL(string: "https://a.b")!)
        ]])
        let screen = Screen(name: "v", rev: 4, nodes: ["tabla": table])
        let result = screen.handle("row_press", on: table, state: ["x": .number(1)], item: row)
        XCTAssertEqual(result.request?.name, "abrir")
        XCTAssertEqual(result.request?.values, [:])
        XCTAssertEqual(result.request?.summary, "Elegí «T-12 · Login»")
        XCTAssertEqual(result.urls, [URL(string: "https://a.b")!])
        XCTAssertEqual((result.request?.data["item"] as? [String: Any])?["id"] as? String, "T-12")
    }

    func testValuesGoBackToJSONWithoutLosingTheirType() {
        let value = ScreenValue(json: ["n": 3, "x": 2.5, "b": true, "s": "t", "l": [1, "a"], "z": NSNull()])
        let json = value.json as? [String: Any]
        XCTAssertEqual(json?["n"] as? Int, 3)
        XCTAssertEqual(json?["x"] as? Double, 2.5)
        XCTAssertEqual(json?["b"] as? Bool, true)
        XCTAssertEqual(value.object?["b"], .bool(true))
        XCTAssertEqual(value.object?["n"]?.text, "3")
        XCTAssertEqual(value.object?["l"]?.text, "1, a")
    }
}
