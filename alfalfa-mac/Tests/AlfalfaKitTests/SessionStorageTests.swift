@testable import AlfalfaKit
import XCTest

final class SessionStorageTests: XCTestCase {
    private let listing = #"{"version":2,"tipo":"espacio_sesiones","nota":"ya no se podrán retomar","#
        + #""totales":{"sesiones":3000,"indice":200,"instantaneas":5000},"sesiones":["#
        + #"{"id":"grande","titulo":"Imágenes","cwd":"/repo","bytes":2000,"imagenes":1500,"#
        + #""ultimo_uso":1000,"dias_sin_usar":40,"en_uso":false,"fijada":false,"estado":"completed"},"#
        + #"{"id":"fijada","titulo":null,"cwd":"/repo","bytes":500,"ultimo_uso":1000,"dias_sin_usar":40,"#
        + #""en_uso":false,"fijada":true},"#
        + #"{"id":"trabajando","bytes":300,"ultimo_uso":1000,"en_uso":true},"#
        + #"{"id":"nueva","bytes":200,"ultimo_uso":4000000,"dias_sin_usar":0}]}"#

    func testDecodesTheListing() throws {
        guard case let .sessionStorage(storage)? = AlfalfaMessage.decode(line: listing) else {
            return XCTFail("listado")
        }
        XCTAssertEqual(storage.sessions.map(\.id), ["grande", "fijada", "trabajando", "nueva"])
        XCTAssertEqual(storage.sessionsBytes, 3000)
        XCTAssertEqual(storage.indexBytes, 200)
        XCTAssertEqual(storage.snapshotsBytes, 5000)
        XCTAssertEqual(storage.notice, "ya no se podrán retomar")
        let big = storage.sessions[0]
        XCTAssertEqual(big.images, 1500)
        XCTAssertEqual(big.daysUnused, 40)
        XCTAssertEqual(big.lastUsed, Date(timeIntervalSince1970: 1000))
        XCTAssertEqual(storage.sessions[1].displayTitle, "Sesión fijada")
        XCTAssertTrue(storage.sessions[2].inUse)
    }

    func testUnusedSkipsPinnedBusyAndOpenSessions() throws {
        guard case let .sessionStorage(storage)? = AlfalfaMessage.decode(line: listing) else {
            return XCTFail("listado")
        }
        let now = Date(timeIntervalSince1970: 1000 + 31 * 86_400)
        XCTAssertEqual(storage.unused(moreThan: 30, open: [], now: now), ["grande"])
        XCTAssertEqual(storage.unused(moreThan: 30, open: ["grande"], now: now), [])
        XCTAssertEqual(storage.unused(moreThan: 60, open: [], now: now), [])
        XCTAssertFalse(storage.freeable(storage.sessions[2], open: []))
        XCTAssertEqual(storage.bytes(of: ["grande", "nueva"]), 2200)
    }

    func testSortsBySizeOrLastUse() throws {
        guard case let .sessionStorage(storage)? = AlfalfaMessage.decode(line: listing) else {
            return XCTFail("listado")
        }
        XCTAssertEqual(storage.sorted(by: .size).map(\.id), ["grande", "fijada", "trabajando", "nueva"])
        XCTAssertEqual(storage.sorted(by: .lastUse).last?.id, "nueva")
    }

    func testDecodesWhatWasFreed() {
        let line = #"{"version":2,"tipo":"sesiones_liberadas","nota":"x","liberado":2000000,"#
            + #""movidas":[{"id":"grande","bytes":2000000,"papelera":"/Users/u/.Trash/alfalfa-grande"}],"#
            + #""saltadas":[{"id":"trabajando","motivo":"en_uso"}],"#
            + #""indice":{"antes":300,"despues":100},"instantaneas":{"antes":5000,"despues":1000,"refs":2}}"#
        guard case let .sessionsFreed(freed)? = AlfalfaMessage.decode(line: line) else { return XCTFail("liberadas") }
        XCTAssertEqual(freed.moved, ["grande"])
        XCTAssertEqual(freed.skipped, [SessionsFreed.Skipped(id: "trabajando", reason: "en_uso")])
        XCTAssertEqual(freed.freed, 2_000_000)
        XCTAssertEqual(freed.indexFreed, 200)
        XCTAssertEqual(freed.snapshotsFreed, 4000)
        XCTAssertTrue(freed.summary.hasPrefix("1 sesión movida a la Papelera"))
        XCTAssertTrue(freed.summary.contains("1 estaba en uso y no se tocó."))
    }

    func testFreedWithoutIndexStillDecodes() {
        let line = #"{"version":2,"tipo":"sesiones_liberadas","movidas":[],"indice":null,"liberado":0}"#
        guard case let .sessionsFreed(freed)? = AlfalfaMessage.decode(line: line) else { return XCTFail("liberadas") }
        XCTAssertEqual(freed.indexFreed, 0)
        XCTAssertTrue(freed.moved.isEmpty)
    }
}
