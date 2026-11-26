@testable import AlfalfaKit
import XCTest

/// Una pantalla como texto para copiarla: el markdown legible y el bloque `pacai.ui/1` en JSON.
final class ScreenTextTests: XCTestCase {

    // MARK: - Markdown

    func testATableIsAMarkdownTableAndTheTitleAHeading() throws {
        let screen = try Self.screen([
            ["id": "root", "component": "Stack", "children": ["titulo", "tabla", "crear"]],
            ["id": "titulo", "component": "Text", "props": ["text": "3 tickets abiertos", "style": "title"]],
            ["id": "tabla", "component": "Table", "props": [
                "columns": [["key": "id", "label": "Id"], ["key": "title", "label": "Título"], ["key": "n"]],
                "rows": ["$": "tickets"]
            ]],
            ["id": "crear", "component": "Button", "props": ["label": "Crear ticket"]]
        ], state: ["tickets": [["id": "T-1", "title": "Falla | Safari", "n": 2], ["id": "T-2", "title": "Lento\nya"]]])
        XCTAssertEqual(screen.markdown(), """
        ## 3 tickets abiertos

        | Id | Título | n |
        | --- | --- | --- |
        | T-1 | Falla \\| Safari | 2 |
        | T-2 | Lento ya |  |
        """, "los botones no salen; las barras se escapan y los saltos de línea no rompen la fila")
    }

    func testCardsInAGridAreAListWithTitleMetaAndDetail() throws {
        let screen = try Self.screen([
            ["id": "root", "component": "Stack", "props": ["direction": "grid"], "children": ["a", "b"]],
            ["id": "a", "component": "Card", "props": [
                "title": "Sale Swift 7", "subtitle": "swift.org", "meta": "Hoy · Lenguajes", "tags": ["Swift", "Apple"],
                "detail": "Trae macros nuevas.\nY más."
            ], "children": ["abrir"]],
            ["id": "abrir", "component": "Button", "props": ["label": "Abrir fuente"]],
            ["id": "b", "component": "Card", "props": ["title": "Python 4"]]
        ])
        XCTAssertEqual(screen.markdown(), """
        - **Sale Swift 7** · swift.org
          Hoy · Lenguajes
          `Swift` `Apple`
          Trae macros nuevas.
          Y más.
        - **Python 4**
        """)
    }

    func testARootCardIsTheHeadingOfTheScreen() throws {
        let screen = try Self.screen([
            ["id": "root", "component": "Card", "props": ["title": "Resumen", "subtitle": "Semana 48"],
             "children": ["nota"]],
            ["id": "nota", "component": "Text", "props": ["text": "Todo en orden.", "style": "muted"]]
        ])
        XCTAssertEqual(screen.markdown(), "## Resumen\nSemana 48\n\n*Todo en orden.*")
    }

    func testAFormShowsEachFieldWithItsLabelAndCurrentValue() throws {
        let screen = try Self.screen([
            ["id": "root", "component": "Form", "props": ["submit_label": "Crear"],
             "children": ["titulo", "prioridad", "etiquetas", "notas"]],
            ["id": "titulo", "component": "Input", "props": ["name": "title", "label": "Título", "required": true]],
            ["id": "prioridad", "component": "Select", "props": [
                "name": "priority", "label": "Prioridad",
                "options": [["value": "low", "label": "Baja"], ["value": "high", "label": "Alta"]]
            ]],
            ["id": "etiquetas", "component": "Select", "props": [
                "name": "tags", "label": "Etiquetas", "multiple": true,
                "options": [["value": "ui", "label": "Interfaz"], ["value": "api", "label": "API"]]
            ]],
            ["id": "notas", "component": "Input", "props": ["name": "notes", "label": "Notas", "kind": "multiline"]]
        ], state: ["priority": "low"])
        XCTAssertEqual(screen.markdown(), """
        **Título:** —

        **Prioridad:** Baja

        **Etiquetas:** —

        **Notas:** —
        """)
        // Lo que escribió el usuario manda sobre el estado que dio el agente.
        let typed: [String: ScreenValue] = ["title": .string("Login roto"), "priority": .string("high"),
                                            "tags": .array([.string("ui"), .string("api")]),
                                            "notes": .string("Pasa en Safari.\nY en Firefox.")]
        XCTAssertEqual(screen.markdown(state: typed), """
        **Título:** Login roto

        **Prioridad:** Alta

        **Etiquetas:** Interfaz, API

        **Notas:**
        Pasa en Safari.
        Y en Firefox.
        """)
    }

    func testAChartIsATableOfItsData() throws {
        let screen = try Self.screen([
            ["id": "root", "component": "Chart", "props": [
                "kind": "bar", "labels": ["Ene", "Feb", "Mar"],
                "series": [["name": "Ventas", "values": [10, 12.5, 9]], ["values": [3, 4]]]
            ]]
        ])
        XCTAssertEqual(screen.markdown(), """
        | Etiqueta | Ventas | Serie 2 |
        | --- | --- | --- |
        | Ene | 10 | 3 |
        | Feb | 12.5 | 4 |
        | Mar | 9 |  |
        """)
    }

    func testListsImagesAndCode() throws {
        let screen = try Self.screen([
            ["id": "root", "component": "Stack", "children": ["lista", "foto", "local", "codigo"]],
            ["id": "lista", "component": "List", "props": ["items": [
                ["id": "1", "title": "Uno", "subtitle": "primero"], ["id": "2", "title": "Dos"]
            ]]],
            ["id": "foto", "component": "Image", "props": ["src": "https://ejemplo.com/a.png", "alt": "Un gato"]],
            ["id": "local", "component": "Image", "props": ["src": "data:image/png;base64,AAAA", "alt": "Un perro"]],
            ["id": "codigo", "component": "Code", "props": ["code": "print(1)", "language": "python"]]
        ])
        XCTAssertEqual(screen.markdown(), """
        - Uno — primero
        - Dos

        ![Un gato](https://ejemplo.com/a.png)

        *Imagen:* Un perro

        ```python
        print(1)
        ```
        """)
    }

    func testWithoutARootTheTextIsItsAlt() {
        XCTAssertEqual(Screen(name: "x", alt: "Cargando tickets").markdown(), "Cargando tickets")
    }

    // MARK: - JSON

    func testTheRawJSONIsAReplaceBlockThatRebuildsTheSameScreen() throws {
        let screen = try Self.screen([
            ["id": "root", "component": "Stack", "children": ["crear", "titulo"]],
            ["id": "titulo", "component": "Text", "props": ["text": ["$": "count"]]],
            ["id": "crear", "component": "Button", "props": ["label": "Crear"],
             "events": ["press": [["set": ["abierto": true]], ["send": "crear", "with": ["count"]],
                                  ["open_url": "https://ejemplo.com"]]]],
            ["id": "suelto", "component": "Text"]
        ], state: ["count": 3], alt: "Tickets")
        let json = screen.rawJSON(state: ["count": .number(4)])
        let data = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any])
        XCTAssertEqual(data["op"] as? String, "replace")
        XCTAssertEqual((data["nodes"] as? [[String: Any]])?.compactMap { $0["id"] as? String },
                       ["root", "crear", "titulo", "suelto"], "en el orden del árbol; lo suelto, al final")
        XCTAssertTrue(json.contains("\"open_url\" : \"https://ejemplo.com\""), "sin barras escapadas")
        // Leído otra vez, da la misma pantalla con el estado que se veía.
        let rebuilt = try XCTUnwrap(Screen(name: "tickets").applying(XCTUnwrap(ScreenUpdate(json: data))))
        XCTAssertEqual(rebuilt.nodes, screen.nodes)
        XCTAssertEqual(rebuilt.state, ["count": .number(4)])
        XCTAssertEqual(rebuilt.alt, "Tickets")
    }

    func testTheExportedConversationKeepsWhatTheScreenShowed() throws {
        let update = try XCTUnwrap(ScreenUpdate(json: [
            "view": "v", "op": "replace", "rev": 1, "alt": "Un saludo",
            "nodes": [["id": "root", "component": "Text", "props": ["text": "Hola"]]]
        ]))
        var timeline = Timeline()
        timeline.apply(.event(AgentEvent(role: "agent", text: "Mira:", time: 1, screens: [update])))
        XCTAssertTrue(timeline.markdown(title: "t").contains("> *Pantalla:* Un saludo\n\nHola"))
    }

    // MARK: - Ayudantes

    private static func screen(_ nodes: [[String: Any]], state: [String: Any] = [:], alt: String = "") throws
        -> Screen {
        var data: [String: Any] = ["view": "tickets", "op": "replace", "rev": 1, "nodes": nodes, "state": state]
        if !alt.isEmpty { data["alt"] = alt }
        return try XCTUnwrap(Screen(name: "tickets").applying(XCTUnwrap(ScreenUpdate(json: data))))
    }
}
