import Foundation

public enum AlfalfaError: Error {
    case launcherNotFound
    case failed(String)
}

// MARK: - LocalizedError

extension AlfalfaError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .launcherNotFound:
            return "No se encontró scripts/alfalfa. Define ALFALFA_REPO con la carpeta del repositorio."
        case let .failed(text): return text
        }
    }
}

public enum Launcher {
    /// El motor: `scripts/alfalfa` de ALFALFA_REPO; si no, el que va dentro de AlfalfaCode.app
    /// (`Contents/Resources/motor/alfalfa`, lo pone build-app.sh); si no, `scripts/alfalfa`
    /// hacia arriba desde el ejecutable y la carpeta actual (la app compilada en un clon).
    public static func locate(environment: [String: String] = ProcessInfo.processInfo.environment,
                              from starts: [URL] = defaultStarts(),
                              bundled: URL? = bundledEngine()) -> URL? {
        var candidates: [URL] = []
        if let repo = environment["ALFALFA_REPO"] {
            let script = URL(fileURLWithPath: repo).appendingPathComponent("scripts/alfalfa")
            if FileManager.default.isExecutableFile(atPath: script.path) { return script }
        }
        if let bundled, FileManager.default.isExecutableFile(atPath: bundled.path) { return bundled }
        for start in starts {
            var url = start.standardizedFileURL
            for _ in 0..<12 {
                candidates.append(url)
                url.deleteLastPathComponent()
            }
        }
        for root in candidates {
            let script = root.appendingPathComponent("scripts/alfalfa")
            if FileManager.default.isExecutableFile(atPath: script.path) { return script }
        }
        return nil
    }

    /// El lanzador del motor empaquetado, si esta app es un .app (no `swift run`).
    public static func bundledEngine(bundle: Bundle = .main) -> URL? {
        guard bundle.bundleURL.pathExtension == "app", let resources = bundle.resourceURL else { return nil }
        return resources.appendingPathComponent("motor/alfalfa")
    }

    public static func defaultStarts() -> [URL] {
        var starts = [URL(fileURLWithPath: FileManager.default.currentDirectoryPath)]
        if let executable = Bundle.main.executableURL { starts.append(executable.deletingLastPathComponent()) }
        return starts
    }

    /// Argumentos de `alfalfa --stdio`. El encargo va tras `--` para que un texto
    /// que empiece por «-» no se lea como opción.
    public static func stdioArguments(task: String?, resume: String?, model: String?,
                                      effort: String?, readOnly: Bool, images: [String] = [],
                                      mode: String? = nil, wait: Bool = false, parent: String? = nil,
                                      title: String? = nil, keepAwake: Bool = true,
                                      browser: Bool = false, budget: Double? = nil,
                                      monthlyBudget: Double? = nil) -> [String] {
        var arguments = ["--stdio"]
        // La app ofrece su navegador propio: el motor da al agente las herramientas browser_*.
        if browser { arguments.append("--navegador") }
        // Ajustes ▸ General ▸ Mantener el Mac despierto, apagado.
        if !keepAwake { arguments.append("--sin-mantener-despierto") }
        // Ajustes ▸ General ▸ Presupuesto: los topes en dólares de la sesión y del mes.
        if let budget, budget > 0 { arguments += ["--presupuesto", BudgetFormat.argument(budget)] }
        if let monthlyBudget, monthlyBudget > 0 { arguments += ["--presupuesto-mes", BudgetFormat.argument(monthlyBudget)] }
        // La sesión que la propuso: sólo al crearla, se guarda con ella.
        if let parent, resume == nil { arguments += ["--padre", parent] }
        // El título de la propuesta, para que la barra lateral no enseñe el principio del encargo.
        if let title, !title.isEmpty, resume == nil { arguments += ["--titulo", title] }
        if let mode, !mode.isEmpty { arguments += ["--modo", mode] }
        for image in images { arguments += ["--imagen", image] }
        if let resume { arguments += ["--reanudar", resume] }
        if let model, !model.isEmpty { arguments += ["--modelo", model] }
        if let effort, !effort.isEmpty { arguments += ["--esfuerzo", effort] }
        if readOnly { arguments.append("--solo-lectura") }
        // Abrir la sesión guardada sin turno: el motor espera un mensaje o «compactar».
        if wait, resume != nil, task == nil { arguments.append("--esperar") }
        if let task { arguments += ["--", task] }
        return arguments
    }
}

/// Proceso hijo `alfalfa --stdio`. Lee JSONL de stdout y escribe JSONL en stdin.
public final class AlfalfaProcess: @unchecked Sendable {
    private let process = Process()
    private let input = Pipe()
    private let output = Pipe()
    private let errors = Pipe()
    private let queue = DispatchQueue(label: "alfalfa.process")
    private let group = DispatchGroup()
    private var buffer = Data()
    private var stderrTail = Data()
    private var inputClosed = false

    public var onMessage: (@Sendable (AlfalfaMessage) -> Void)?
    /// Código de salida y últimas líneas de stderr (sin la clave de Ollama).
    public var onExit: (@Sendable (Int32, String) -> Void)?

    public init(launcher: URL, arguments: [String], directory: URL,
                environment: [String: String] = ProcessInfo.processInfo.environment) {
        process.executableURL = launcher
        process.arguments = arguments
        process.currentDirectoryURL = directory
        var env = environment
        let parts = (env["PATH"] ?? "").split(separator: ":").map(String.init)
        let standard = ["/usr/bin", "/bin", "/usr/local/bin", "/opt/homebrew/bin"]
        env["PATH"] = (parts + standard.filter { !parts.contains($0) }).joined(separator: ":")
        process.environment = env
        process.standardInput = input
        process.standardOutput = output
        process.standardError = errors
    }

    public var isRunning: Bool { process.isRunning }

    public func start() throws {
        signal(SIGPIPE, SIG_IGN)
        group.enter() // stdout
        group.enter() // stderr
        group.enter() // terminación
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard let self else { handle.readabilityHandler = nil; return }
            if data.isEmpty {
                handle.readabilityHandler = nil
                self.queue.async { self.flush(); self.group.leave() }
            } else {
                self.queue.async { self.consume(data) }
            }
        }
        errors.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard let self else { handle.readabilityHandler = nil; return }
            if data.isEmpty {
                handle.readabilityHandler = nil
                self.queue.async { self.group.leave() }
            } else {
                self.queue.async {
                    self.stderrTail.append(data)
                    if self.stderrTail.count > 4096 { self.stderrTail = self.stderrTail.suffix(4096) }
                }
            }
        }
        process.terminationHandler = { [weak self] _ in self?.group.leave() }
        do {
            try process.run()
        } catch {
            output.fileHandleForReading.readabilityHandler = nil
            errors.fileHandleForReading.readabilityHandler = nil
            throw error
        }
        group.notify(queue: queue) { [weak self] in
            guard let self else { return }
            let tail = Redactor.clean(String(decoding: self.stderrTail, as: UTF8.self))
            self.onExit?(self.process.terminationStatus, tail)
        }
    }

    private func consume(_ data: Data) {
        buffer.append(data)
        while let newline = buffer.firstIndex(of: 0x0A) {
            let line = buffer.subdata(in: buffer.startIndex..<newline)
            buffer.removeSubrange(buffer.startIndex...newline)
            emit(line)
        }
    }

    private func flush() {
        if !buffer.isEmpty { emit(buffer); buffer.removeAll() }
    }

    private func emit(_ line: Data) {
        let text = Redactor.clean(String(decoding: line, as: UTF8.self))
        if let message = AlfalfaMessage.decode(line: text) { onMessage?(message) }
    }

    /// Escribe una línea de entrada. No hace nada si el proceso ya cerró stdin.
    public func send(_ command: AlfalfaCommand) {
        queue.async {
            guard !self.inputClosed, self.process.isRunning else { return }
            try? self.input.fileHandleForWriting.write(contentsOf: Data(command.line.utf8))
        }
    }

    /// Escribe una línea suelta por stdin (por ejemplo una clave para `--guardar-clave`).
    public func write(line: String) {
        queue.async {
            guard !self.inputClosed else { return }
            try? self.input.fileHandleForWriting.write(contentsOf: Data((line + "\n").utf8))
        }
    }

    /// Cierra stdin (EOF): el proceso termina la sesión.
    public func closeInput() {
        queue.async { self.closeInputNow() }
    }

    /// Como `closeInput`, pero espera a que stdin esté cerrado (para salir de la app sin dejar huérfanos).
    public func closeInputAndWait() {
        queue.sync { closeInputNow() }
    }

    private func closeInputNow() {
        guard !inputClosed else { return }
        inputClosed = true
        try? input.fileHandleForWriting.close()
    }

    public func terminate() {
        if process.isRunning { process.terminate() }
    }

    /// Consulta de una sola pasada (`--sesiones --json`, `--modelos --json`).
    /// `input` va por la entrada estándar (una clave para `--guardar-clave`), nunca en los argumentos.
    public static func query(launcher: URL, arguments: [String], directory: URL,
                             input: String? = nil) async throws -> [AlfalfaMessage] {
        try await withCheckedThrowingContinuation { continuation in
            let process = AlfalfaProcess(launcher: launcher, arguments: arguments, directory: directory)
            let box = MessageBox()
            process.onMessage = { box.append($0) }
            process.onExit = { _, tail in
                let messages = box.all()
                if messages.isEmpty {
                    continuation.resume(throwing: AlfalfaError.failed(tail.isEmpty ? "Sin respuesta de alfalfa." : tail))
                } else {
                    continuation.resume(returning: messages)
                }
                _ = process
            }
            do {
                try process.start()
                if let input { process.write(line: input) }
                process.closeInput()
            } catch {
                continuation.resume(throwing: error)
            }
        }
    }
}

private final class MessageBox: @unchecked Sendable {
    private let lock = NSLock()
    private var messages: [AlfalfaMessage] = []
    func append(_ message: AlfalfaMessage) { lock.lock(); messages.append(message); lock.unlock() }
    func all() -> [AlfalfaMessage] { lock.lock(); defer { lock.unlock() }; return messages }
}
