@testable import AlfalfaKit
import XCTest

final class CodeActionsTests: XCTestCase {
    func testOnlyShellBlocksCanRun() {
        XCTAssertTrue(CodeActions.isRunnable(language: "bash", code: "swift run"))
        XCTAssertTrue(CodeActions.isRunnable(language: "Zsh", code: "$ ls"))
        XCTAssertFalse(CodeActions.isRunnable(language: "swift", code: "let x = 1"))
        XCTAssertFalse(CodeActions.isRunnable(language: nil, code: "ls"))
        XCTAssertFalse(CodeActions.isRunnable(language: "bash", code: "  \n"))
        XCTAssertEqual(CodeActions.command(from: "$ cd x\n$ ls\n\n"), "cd x\nls")
    }

    func testTerminalScriptQuotesFolderAndCommand() {
        let script = CodeActions.terminalScript(command: #"echo "hola""#, folder: "/tmp/it's")
        XCTAssertTrue(script.contains(#"do script "cd '/tmp/it'\\''s' && echo \"hola\"""#), script)
    }

    func testImageTagsRoundTrip() {
        let text = ImageTags.join("¿Qué falla?", images: ["/a.png", "/b b.jpg"])
        XCTAssertEqual(text, "<image name=[Image #1] path=\"/a.png\"></image>\n<image name=[Image #2] path=\"/b b.jpg\"></image>\n¿Qué falla?")
        let parts = ImageTags.split(text)
        XCTAssertEqual(parts.images, ["/a.png", "/b b.jpg"])
        XCTAssertEqual(parts.text, "¿Qué falla?")
        XCTAssertEqual(ImageTags.split("sin imágenes").text, "sin imágenes")
    }

    func testFileTagsRoundTripLikeTheHarness() {
        // Como alfalfa.adjuntos.con_etiquetas sobre alfalfa.imagenes.con_etiquetas: archivos delante.
        let text = ImageTags.join("Resume", images: ["/a.png", "/d/Informe \"Q3\".pdf", "/d/main.py"])
        XCTAssertEqual(text, "<file name=\"Informe _Q3_.pdf\" path=\"/d/Informe \"Q3\".pdf\"></file>\n"
                       + "<file name=\"main.py\" path=\"/d/main.py\"></file>\n"
                       + "<image name=[Image #1] path=\"/a.png\"></image>\nResume")
        let harness = "<file name=\"informe.pdf\" path=\"/d/informe.pdf\"></file>\n"
            + "<image name=[Image #1] path=\"/a.png\"></image>\nResume"
        let parts = ImageTags.split(harness)
        XCTAssertEqual(parts.images, ["/a.png"])
        XCTAssertEqual(parts.files, ["/d/informe.pdf"])
        XCTAssertEqual(parts.attachments, ["/a.png", "/d/informe.pdf"])
        XCTAssertEqual(parts.text, "Resume")
    }

    func testFilesGoAsArchivos() {
        XCTAssertEqual(AlfalfaCommand.message("x", images: ["/a.png", "/b.pdf"]).line,
                       #"{"archivos":["/b.pdf"],"imagenes":["/a.png"],"texto":"x","tipo":"mensaje"}"# + "\n")
        XCTAssertEqual(Launcher.stdioArguments(task: "x", resume: nil, model: nil, effort: nil, readOnly: false,
                                               images: ["/a.png", "/b.docx"]),
                       ["--stdio", "--imagen", "/a.png", "--archivo", "/b.docx", "--", "x"])
        XCTAssertTrue(Attachments.isFile(URL(fileURLWithPath: "/x/Informe.PDF")))
        XCTAssertTrue(Attachments.isFile(URL(fileURLWithPath: "/x/Makefile")))
        XCTAssertFalse(Attachments.isFile(URL(fileURLWithPath: "/x/app.exe")))
        XCTAssertFalse(Attachments.isFile(URL(fileURLWithPath: "/x/foto.png")))
        XCTAssertEqual(Attachments.placeholder(for: ["/b.pdf"]), "Lee el archivo adjunto.")
        XCTAssertEqual(Attachments.placeholder(for: ["/b.pdf", "/a.png"]), "Mira la imagen adjunta.")
    }

    func testMessageWithImagesAndLauncherArguments() {
        XCTAssertEqual(AlfalfaCommand.message("hola").line, #"{"texto":"hola","tipo":"mensaje"}"# + "\n")
        XCTAssertEqual(AlfalfaCommand.message("", images: ["/a.png"]).line,
                       #"{"imagenes":["/a.png"],"texto":"","tipo":"mensaje"}"# + "\n")
        XCTAssertEqual(Launcher.stdioArguments(task: "x", resume: nil, model: nil, effort: nil, readOnly: false, images: ["/a.png"]),
                       ["--stdio", "--imagen", "/a.png", "--", "x"])
        XCTAssertTrue(Attachments.isImage(URL(fileURLWithPath: "/x/Captura.PNG")))
        XCTAssertFalse(Attachments.isImage(URL(fileURLWithPath: "/x/nota.txt")))
    }

    func testSessionMarksAndChanges() {
        let row = #"{"version":2,"tipo":"sesiones","sesiones":[{"id":"a","estado":"completed","encargo":"Arreglar","titulo":"Mío","fijada":true,"fecha":1}]}"#
        guard case let .sessions(rows)? = AlfalfaMessage.decode(line: row) else { return XCTFail("sesiones") }
        XCTAssertEqual(rows[0].displayTitle, "Mío")
        XCTAssertTrue(rows[0].pinned)
        XCTAssertFalse(rows[0].archived)
        XCTAssertEqual(AlfalfaMessage.decode(line: #"{"version":2,"tipo":"sesion_cambiada","id":"b","bifurcada_de":"a"}"#),
                       .sessionChanged(id: "b", forkedFrom: "a"))
    }
}
