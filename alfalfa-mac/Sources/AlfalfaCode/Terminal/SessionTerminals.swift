import AlfalfaKit
import AppKit
import SwiftTerm
import SwiftUI

/// Una pestaña de la terminal integrada: una shell de verdad (CuyTerm, la copia de SwiftTerm)
/// en la carpeta de la sesión. Sigue viva aunque se cierre el panel o se cambie de sesión.
@MainActor
final class TerminalTab: NSObject {

    // MARK: - Propiedades

    let id = UUID()
    let title: String
    let view: LocalProcessTerminalView
    var onExit: ((TerminalTab) -> Void)?

    // MARK: - Inicializadores

    init(title: String, folder: String) {
        self.title = title
        view = LocalProcessTerminalView(frame: NSRect(x: 0, y: 0, width: 480, height: 400))
        super.init()
        view.font = NSFont.monospacedSystemFont(ofSize: 12.5, weight: .regular)
        view.nativeBackgroundColor = NSColor(Theme.sidebar)
        view.nativeForegroundColor = NSColor(Theme.text)
        view.caretColor = NSColor(Theme.alfalfa)
        view.selectedTextBackgroundColor = NSColor(Theme.alfalfa).withAlphaComponent(0.35)
        // Con el teclado español, Opción escribe @, #, [ ], { }, | y \: no puede ser Meta.
        view.optionAsMetaKey = false
        view.processDelegate = self
        let launch = TerminalLaunch(folder: folder)
        view.startProcess(executable: launch.executable, args: [], environment: launch.environment,
                          execName: launch.execName, currentDirectory: launch.directory)
    }

    // MARK: - Métodos

    func send(_ text: String) { view.send(txt: text) }

    func terminate() { view.terminate() }
}

extension TerminalTab: Identifiable {}

extension TerminalTab: LocalProcessTerminalViewDelegate {
    func sizeChanged(source: LocalProcessTerminalView, newCols: Int, newRows: Int) {}

    func setTerminalTitle(source: LocalProcessTerminalView, title: String) {}

    func hostCurrentDirectoryUpdate(source: TerminalView, directory: String?) {}

    func processTerminated(source: TerminalView, exitCode: Int32?) {
        onExit?(self)
    }
}

/// Las terminales de una sesión: sus pestañas, la elegida y si el panel está abierto.
@MainActor
final class SessionTerminals: ObservableObject {

    // MARK: - Propiedades

    @Published private(set) var tabs: [TerminalTab] = []
    @Published var selected: TerminalTab.ID?
    @Published private(set) var isOpen = false
    let folder: String

    var current: TerminalTab? { tabs.first { $0.id == selected } ?? tabs.last }

    // MARK: - Inicializadores

    init(folder: String) {
        self.folder = folder
    }

    // MARK: - Métodos

    /// Abre el panel; si no hay ninguna pestaña, crea una.
    func open() {
        if tabs.isEmpty { addTab() }
        isOpen = true
        focus()
    }

    func close() { isOpen = false }

    func toggle() {
        if isOpen { close() } else { open() }
    }

    @discardableResult func addTab() -> TerminalTab {
        let used = Set(tabs.map(\.title))
        let title = (1...).lazy.map { $0 == 1 ? "zsh" : "zsh \($0)" }.first { !used.contains($0) } ?? "zsh"
        let tab = TerminalTab(title: title, folder: folder)
        tab.onExit = { [weak self] tab in self?.remove(tab, terminate: false) }
        tabs.append(tab)
        selected = tab.id
        isOpen = true
        focus()
        return tab
    }

    func closeTab(_ tab: TerminalTab) { remove(tab, terminate: true) }

    func select(_ tab: TerminalTab) {
        selected = tab.id
        focus()
    }

    /// El ▷ de un bloque de código: abre el panel y escribe la orden en la pestaña elegida.
    func run(_ code: String) {
        let tab = current ?? addTab()
        isOpen = true
        selected = tab.id
        tab.send(TerminalInput.run(code))
        focus()
    }

    /// Cierra todas las shells (al quitar la sesión).
    func terminateAll() {
        tabs.forEach { $0.terminate() }
        tabs = []
        isOpen = false
    }

    // MARK: - Métodos privados

    private func remove(_ tab: TerminalTab, terminate: Bool) {
        guard let index = tabs.firstIndex(where: { $0.id == tab.id }) else { return }
        if terminate { tab.terminate() }
        tabs.remove(at: index)
        if selected == tab.id { selected = tabs[safe: min(index, tabs.count - 1)]?.id }
        if tabs.isEmpty { isOpen = false }
    }

    /// El teclado va a la terminal en cuanto se ve.
    private func focus() {
        DispatchQueue.main.async { [weak self] in
            guard let view = self?.current?.view, let window = view.window else { return }
            window.makeFirstResponder(view)
        }
    }
}

/// Las terminales de cada sesión abierta, para que sobrevivan al cambiar de una a otra.
@MainActor
final class TerminalHub {

    // MARK: - Propiedades

    static let shared = TerminalHub()

    // MARK: - Propiedades privadas

    private var bySession: [UUID: SessionTerminals] = [:]

    // MARK: - Métodos

    func terminals(for session: LiveSession) -> SessionTerminals {
        if let existing = bySession[session.id] { return existing }
        let created = SessionTerminals(folder: session.cwd.path)
        bySession[session.id] = created
        return created
    }
}

/// El ▷ de los bloques de código, cuando la vista está dentro de una sesión con terminal.
private struct RunInTerminalKey: EnvironmentKey {
    static let defaultValue: ((String) -> Void)? = nil
}

extension EnvironmentValues {
    var runInTerminal: ((String) -> Void)? {
        get { self[RunInTerminalKey.self] }
        set { self[RunInTerminalKey.self] = newValue }
    }
}
