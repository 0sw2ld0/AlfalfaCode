import AlfalfaKit
import XCTest

final class FolderMenuTests: XCTestCase {

    func testOnlyRealFoldersHaveTheMenu() {
        XCTAssertTrue(FolderMenu.applies(folder: "/Users/ana/CuyWorld", title: nil))
        XCTAssertFalse(FolderMenu.applies(folder: "", title: "Fijadas"))
        XCTAssertFalse(FolderMenu.applies(folder: "Sin carpeta", title: nil))
    }

    func testTheConfirmationSaysWhatHappensAndThatFilesStay() {
        let many = FolderMenu.removalMessage(name: "CuyWorld", sessions: 3, running: 1)
        XCTAssertTrue(many.contains("Se archivan sus 3 sesiones"))
        XCTAssertTrue(many.contains("La que está trabajando se queda."))
        XCTAssertTrue(many.hasSuffix("La carpeta y sus archivos no se tocan."))
        XCTAssertTrue(FolderMenu.removalMessage(name: "x", sessions: 1, running: 0).contains("Se archiva su sesión"))
        XCTAssertFalse(FolderMenu.removalMessage(name: "x", sessions: 0, running: 0).contains("Se archiva"))
    }
}
