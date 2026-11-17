import Darwin
import Foundation

/// La conexión con el socket de una sesión que trabaja en otro proceso (`<sesiones>/<id>/vivo.sock`): manda el
/// `hello` de quien sólo mira (`PacaiObservation`) y lee lo que llega.
///
/// `connect` espera a la foto (el `snapshot`) y devuelve lo que hace falta para pintar la sesión: hasta dónde vale el
/// historial en disco y lo que está en marcha. Lo que llega después se guarda hasta que alguien lo recoge con `attach`,
/// en el hilo principal y en orden, para que no se pierda nada mientras la app lee el historial.
public final class PacaiObserver: @unchecked Sendable {

    // MARK: - Tipos

    public struct Ready {
        public let observer: PacaiObserver
        /// La transcripción en disco vale hasta aquí (hora de sus filas); lo posterior llega en vivo.
        public let historyUntil: Double?
        public let sessionID: String?
        /// Lo que está en marcha: permisos pendientes, uso, contexto, plan, subagentes…
        public let state: [AlfalfaMessage]
    }

    public enum Failure: Error, Equatable {
        case pathTooLong
        case unreachable(Int32)
        case rejected(String)
        case timedOut
        case closed
    }

    // MARK: - Constantes

    public static let socketName = "vivo.sock"

    // MARK: - Propiedades

    public let url: URL
    private let observation: PacaiObservation
    private let queue = DispatchQueue(label: "alfalfa.observador")
    private var socket: Int32 = -1
    private var source: DispatchSourceRead?
    private var buffer = Data()
    private var finished = false
    /// Sólo en el hilo principal.
    private var pending: [AlfalfaMessage] = []
    private var sink: (([AlfalfaMessage]) -> Void)?
    private var closedHandler: (() -> Void)?
    private var closedEarly = false
    private var opening: ((Result<Ready, Failure>) -> Void)?

    // MARK: - Inicializadores

    init(url: URL, observation: PacaiObservation = PacaiObservation()) {
        self.url = url
        self.observation = observation
    }

    deinit {
        source?.cancel()
    }

    // MARK: - Métodos públicos

    /// El socket de una sesión, en la carpeta de sesiones del motor (`ALFALFA_HOME` o `~/.alfalfa`).
    public static func socketURL(sessionID: String, root: URL = SessionsWatcher.directory()) -> URL {
        root.appendingPathComponent(sessionID).appendingPathComponent(socketName)
    }

    /// Se conecta y espera la foto, como mucho `timeout` segundos. Sin socket (un motor anterior, `--stdio`, una ruta
    /// demasiado larga) o si el agente no lo acepta, da el motivo: la app sigue entonces la transcripción en disco.
    public static func connect(to url: URL, timeout: TimeInterval = 3,
                               observation: PacaiObservation = PacaiObservation()) async -> Result<Ready, Failure> {
        let observer = PacaiObserver(url: url, observation: observation)
        return await withCheckedContinuation { continuation in
            observer.open(timeout: timeout) { continuation.resume(returning: $0) }
        }
    }

    /// Recibe en el hilo principal lo que llegó desde la foto y, después, cada tanda según llega.
    public func attach(_ sink: @escaping ([AlfalfaMessage]) -> Void, onClose: @escaping () -> Void) {
        dispatchPrecondition(condition: .onQueue(.main))
        let earlier = pending
        pending = []
        if !earlier.isEmpty { sink(earlier) }
        self.sink = sink
        if closedEarly { onClose() } else { closedHandler = onClose }
    }

    /// Deja de mirar. Al otro lado sólo se ve que se fue (EOF): no manda nada al agente.
    public func close() {
        queue.async { self.finish(notify: false) }
    }

    // MARK: - Métodos privados

    private func open(timeout: TimeInterval, completion: @escaping (Result<Ready, Failure>) -> Void) {
        queue.async { [self] in
            opening = completion
            let descriptor = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
            guard descriptor >= 0 else { return fail(.unreachable(errno)) }
            socket = descriptor
            var one: Int32 = 1
            setsockopt(descriptor, SOL_SOCKET, SO_NOSIGPIPE, &one, socklen_t(MemoryLayout<Int32>.size))
            var address = sockaddr_un()
            let path = Array(url.path.utf8CString)
            guard path.count <= MemoryLayout.size(ofValue: address.sun_path) else { return fail(.pathTooLong) }
            address.sun_family = sa_family_t(AF_UNIX)
            address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
            withUnsafeMutableBytes(of: &address.sun_path) { raw in
                path.withUnsafeBytes { raw.copyMemory(from: $0) }
            }
            let connected = withUnsafePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.connect(descriptor, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                }
            }
            guard connected == 0 else { return fail(.unreachable(errno)) }
            guard send(observation.start()) else { return fail(.closed) }
            let flags = fcntl(descriptor, F_GETFL)
            _ = fcntl(descriptor, F_SETFL, flags | O_NONBLOCK)
            let reader = DispatchSource.makeReadSource(fileDescriptor: descriptor, queue: queue)
            reader.setEventHandler { [weak self] in self?.readAvailable() }
            source = reader
            reader.resume()
            queue.asyncAfter(deadline: .now() + timeout) { [weak self] in
                guard let self, self.opening != nil else { return }
                self.fail(.timedOut)
            }
        }
    }

    private func send(_ line: String) -> Bool {
        var data = Array(line.utf8)
        var offset = 0
        while offset < data.count {
            let written = data.withUnsafeMutableBytes { Darwin.write(socket, $0.baseAddress! + offset, $0.count - offset) }
            if written < 0 && errno == EINTR { continue }
            guard written > 0 else { return false }
            offset += written
        }
        data.removeAll()
        return true
    }

    private func readAvailable() {
        var chunk = [UInt8](repeating: 0, count: 64 * 1024)
        while true {
            let count = chunk.withUnsafeMutableBytes { Darwin.read(socket, $0.baseAddress, $0.count) }
            if count > 0 {
                buffer.append(contentsOf: chunk[0..<count])
                continue
            }
            if count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK) { break }
            if count < 0 && errno == EINTR { continue }
            consumeLines()
            return finish(notify: true)                       // EOF o error: el agente se fue
        }
        consumeLines()
    }

    private func consumeLines() {
        var updates: [PacaiObservation.Update] = []
        while let newline = buffer.firstIndex(of: 0x0A) {
            let line = String(decoding: buffer[buffer.startIndex..<newline], as: UTF8.self)
            buffer.removeSubrange(buffer.startIndex...newline)
            if let update = observation.receive(line) { updates.append(update) }
        }
        for update in updates { deliver(update) }
    }

    private func deliver(_ update: PacaiObservation.Update) {
        switch update {
        case let .ready(sessionID, historyUntil, state):
            guard let opening else { return }
            self.opening = nil
            opening(.success(Ready(observer: self, historyUntil: historyUntil, sessionID: sessionID, state: state)))
        case let .rejected(reason):
            fail(.rejected(reason))
        case let .messages(messages):
            DispatchQueue.main.async { [self] in
                if let sink { sink(messages) } else { pending += messages }
            }
        }
    }

    private func fail(_ failure: Failure) {
        let opening = self.opening
        self.opening = nil
        finish(notify: false)
        opening?(.failure(failure))
    }

    private func finish(notify: Bool) {
        guard !finished else { return }
        finished = true
        if let opening {
            self.opening = nil
            opening(.failure(.closed))
        }
        if let source {
            source.setCancelHandler { [socket] in Darwin.close(socket) }
            source.cancel()
            self.source = nil
        } else if socket >= 0 {
            Darwin.close(socket)
        }
        guard notify else { return }
        DispatchQueue.main.async { [self] in
            if let closedHandler { closedHandler() } else { closedEarly = true }
            closedHandler = nil
        }
    }
}
