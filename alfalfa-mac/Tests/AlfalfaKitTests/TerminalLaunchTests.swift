@testable import AlfalfaKit
import XCTest

final class TerminalLaunchTests: XCTestCase {
    private let source = ["HOME": "/Users/cuy", "USER": "cuy", "LOGNAME": "cuy", "SSH_AUTH_SOCK": "/tmp/agente",
                          "OPENAI_API_KEY": "sk-secreto", "OLLAMA_API_KEY": "clave", "ALFALFA_HOME": "/x",
                          "PATH": "/opt/raro/bin", "SHELL": "/bin/bash"]

    func testStartsTheLoginShellInTheSessionFolder() {
        let launch = TerminalLaunch(folder: NSTemporaryDirectory(), shell: "/bin/zsh", environment: source)
        XCTAssertEqual(launch.executable, "/bin/zsh")
        XCTAssertEqual(launch.execName, "-zsh")
        XCTAssertEqual(launch.directory, NSTemporaryDirectory())
    }

    func testProviderKeysAndAppVariablesDoNotReachTheShell() {
        let launch = TerminalLaunch(folder: "/", shell: "/bin/zsh", environment: source)
        let names = Set(launch.environment.map { String($0.prefix { $0 != "=" }) })
        XCTAssertFalse(names.contains("OPENAI_API_KEY"))
        XCTAssertFalse(names.contains("OLLAMA_API_KEY"))
        XCTAssertFalse(names.contains("ALFALFA_HOME"))
        // El PATH lo pone la shell de inicio (path_helper), no la app.
        XCTAssertFalse(names.contains("PATH"))
        XCTAssertTrue(launch.environment.contains("SSH_AUTH_SOCK=/tmp/agente"))
        XCTAssertTrue(launch.environment.contains("HOME=/Users/cuy"))
        XCTAssertTrue(launch.environment.contains("TERM=xterm-256color"))
        XCTAssertTrue(launch.environment.contains("SHELL=/bin/zsh"))
    }

    func testSpanishLocaleWhenTheMacIsInSpanish() {
        let spanish = TerminalLaunch(folder: "/", shell: "/bin/zsh", environment: source, language: "es") { _ in true }
        XCTAssertTrue(spanish.environment.contains("LANG=es_ES.UTF-8"))
        let english = TerminalLaunch(folder: "/", shell: "/bin/zsh", environment: source, language: "en") { _ in true }
        XCTAssertTrue(english.environment.contains("LANG=en_US.UTF-8"))
        let missing = TerminalLaunch(folder: "/", shell: "/bin/zsh", environment: source, language: "es") { _ in false }
        XCTAssertTrue(missing.environment.contains("LANG=en_US.UTF-8"))
    }

    func testFallsBackWhenTheShellOrFolderIsBad() {
        let launch = TerminalLaunch(folder: "/no/existe", shell: "relativa/zsh", environment: source)
        XCTAssertTrue(launch.executable.hasPrefix("/"))
        XCTAssertEqual(launch.directory, "/Users/cuy")
    }

    func testRunTypesTheCommandAndPressesReturn() {
        XCTAssertEqual(TerminalInput.run("$ ls -la\n"), "ls -la\r")
        XCTAssertEqual(TerminalInput.run("cd x\r\n$ swift build"), "cd x\rswift build\r")
    }
}
