@testable import AlfalfaKit
import XCTest

final class SessionLogTests: XCTestCase {
    private var folder: URL!

    override func setUpWithError() throws {
        folder = FileManager.default.temporaryDirectory.appendingPathComponent("transcripcion-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: folder) }

    private func line(_ row: [String: Any]) -> String {
        String(data: try! JSONSerialization.data(withJSONObject: row), encoding: .utf8)! + "\n"
    }

    private func append(_ text: String, to url: URL) throws {
        if !FileManager.default.fileExists(atPath: url.path) { FileManager.default.createFile(atPath: url.path, contents: nil) }
        let handle = try FileHandle(forWritingTo: url)
        try handle.seekToEnd()
        try handle.write(contentsOf: Data(text.utf8))
        try handle.close()
    }

    func testAnEventIsReadLikeTheSavedHistory() {
        let event = SessionLog.event(fromLine: line(["role": "agent", "text": "Hola", "time": 10.5, "turno": "t1"]))
        XCTAssertEqual(event?.role, "agent")
        XCTAssertEqual(event?.text, "Hola")
        XCTAssertEqual(event?.time, 10.5)
        XCTAssertEqual(event?.turn, "t1")
        let tool = SessionLog.event(fromLine: line([
            "role": "tool", "text": "Inicio read_file", "time": 11,
            "herramienta": ["nombre": "read_file", "fase": "inicio", "descripcion": "Leer x"]]))
        XCTAssertEqual(tool?.tool?.name, "read_file")
        XCTAssertEqual(tool?.tool?.phase, .start)
        XCTAssertEqual(SessionLog.event(fromLine: line(["text": String(repeating: "a", count: 25_000), "time": 1]))?
            .text.count, 20_000)
    }

    func testSkipsWhatTheHistorySkips() {
        XCTAssertNil(SessionLog.event(fromLine: line([
            "role": "system", "time": 1,
            "text": "Anthropic transporte: intento 1/3 falló (sin respuesta a tiempo); reintento en 1 s."])))
        // El aviso final de agotar los intentos sí se enseña, como en --historial.
        XCTAssertNotNil(SessionLog.event(fromLine: line([
            "role": "system", "time": 1, "text": "Anthropic transporte: agotados 3 intentos; la petición no se completó."])))
        XCTAssertNil(SessionLog.event(fromLine: line(["role": "system", "text": "Compactando", "time": 1,
                                                      "compactacion": ["estado": "en_curso"]])))
        XCTAssertNil(SessionLog.event(fromLine: "no es json"))
        XCTAssertNil(SessionLog.event(fromLine: line(["role": "agent", "time": 1])))
    }

    func testTheTailReadsOnlyWhatIsNewAndWaitsForWholeLines() throws {
        let url = SessionLog.url(sessionID: "abc", root: folder)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try append(line(["role": "agent", "text": "uno", "time": 1]) + line(["role": "agent", "text": "dos", "time": 2]),
                   to: url)
        let tail = SessionLogTail(url: url, offset: 0)
        XCTAssertEqual(tail.read(), .events([AgentEvent(role: "agent", text: "uno", time: 1),
                                             AgentEvent(role: "agent", text: "dos", time: 2)]))
        XCTAssertEqual(tail.read(), .events([]))
        let tres = line(["role": "agent", "text": "tres", "time": 3])
        try append(String(tres.prefix(10)), to: url)
        XCTAssertEqual(tail.read(), .events([]))
        try append(String(tres.dropFirst(10)), to: url)
        XCTAssertEqual(tail.read(), .events([AgentEvent(role: "agent", text: "tres", time: 3)]))
    }

    func testSkipsWhatTheHistoryAlreadyShowedAndRestartsIfTheFileShrinks() throws {
        let url = folder.appendingPathComponent("s.jsonl")
        try append(line(["role": "agent", "text": "ya pintado", "time": 5]) + line(["role": "agent", "text": "nuevo", "time": 6]),
                   to: url)
        let tail = SessionLogTail(url: url, offset: 0, after: 5)
        XCTAssertEqual(tail.read(), .events([AgentEvent(role: "agent", text: "nuevo", time: 6)]))
        try Data().write(to: url)
        XCTAssertEqual(tail.read(), .restart)
    }

    func testARunningSessionKeepsItsOpenToolUntilItsEndArrives() {
        let start = AgentEvent(role: "tool", text: "Inicio read_file", time: 2,
                               tool: ToolInfo.parse(["nombre": "read_file", "fase": "inicio", "descripcion": "Leer x"]))
        let end = AgentEvent(role: "tool", text: "Fin read_file", time: 3,
                             tool: ToolInfo.parse(["nombre": "read_file", "fase": "fin"]))
        let history = [AgentEvent(role: "user", text: "Haz algo", time: 1), start]
        func open(_ timeline: Timeline) -> Int {
            timeline.items.reduce(0) { count, item in
                guard case let .events(_, events) = item else { return count }
                return count + ToolCalls.open(in: events).count
            }
        }
        var running = Timeline(history: history, wrapUp: nil, running: true)
        XCTAssertEqual(open(running), 1)
        running.appendSaved([end])
        XCTAssertEqual(open(running), 0)
        running.updateSavedTotals(tokens: 1234, cost: 0.5)
        XCTAssertEqual(running.totalTokens, 1234)
        XCTAssertEqual(running.totalCost, 0.5)
        // Guardada y sin proceso: lo que quedó abierto, quedó cortado.
        XCTAssertEqual(open(Timeline(history: history, wrapUp: nil)), 0)
    }
}
