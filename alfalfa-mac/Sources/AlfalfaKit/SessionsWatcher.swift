import CoreServices
import Foundation

/// Avisa cuando cambia algo en la carpeta de sesiones del motor (`$ALFALFA_HOME/sesiones` o `~/.alfalfa/sesiones`).
///
/// Un encargo lanzado fuera de la app (la terminal, un cliente PACAI como el de Claude Code, otra ventana) crea y
/// actualiza su sesión sin que la app lo sepa; sin esto, la barra lateral no lo veía hasta reabrir la app. Usa FSEvents
/// (no sondea) y agrupa los cambios cada `latency` segundos: `onChange` recibe, en el hilo principal, los ids de las
/// sesiones tocadas ("" si cambió la propia carpeta).
public final class SessionsWatcher {
    public static func directory(environment: [String: String] = ProcessInfo.processInfo.environment) -> URL {
        let home = environment["ALFALFA_HOME"].map { URL(fileURLWithPath: $0) }
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".alfalfa")
        return home.appendingPathComponent("sesiones")
    }

    /// El id de la sesión a la que pertenece `path` (su primera carpeta bajo `root`); "" para la propia `root`; nil
    /// si está fuera. FSEvents da las rutas reales (`/private/var/…`), así que se comparan sin ese prefijo.
    public static func sessionID(of path: String, root: String) -> String? {
        let ruta = normalized(path), raiz = normalized(root)
        if ruta == raiz { return "" }
        guard ruta.hasPrefix(raiz + "/") else { return nil }
        let resto = ruta.dropFirst(raiz.count + 1)
        return resto.split(separator: "/", maxSplits: 1).first.map(String.init)
    }

    static func normalized(_ path: String) -> String {
        var p = path
        while p.count > 1 && p.hasSuffix("/") { p.removeLast() }
        for alias in ["/private/var/", "/private/tmp/", "/private/etc/"] where p.hasPrefix(alias) {
            p.removeFirst("/private".count)
        }
        return p
    }

    public let root: URL
    private let latency: TimeInterval
    private let onChange: (Set<String>) -> Void
    private var stream: FSEventStreamRef?
    private let queue = DispatchQueue(label: "alfalfa.sesiones.vigilante")

    public init(root: URL = SessionsWatcher.directory(), latency: TimeInterval = 1.5,
                onChange: @escaping (Set<String>) -> Void) {
        self.root = root
        self.latency = latency
        self.onChange = onChange
    }

    /// Empieza a vigilar. Si la carpeta aún no existe (motor sin estrenar), vigila la de AlfalfaCode y filtra.
    @discardableResult
    public func start() -> Bool {
        guard stream == nil else { return true }
        let existe = FileManager.default.fileExists(atPath: root.path)
        let vigilada = existe ? root : root.deletingLastPathComponent()
        var contexto = FSEventStreamContext(version: 0, info: Unmanaged.passUnretained(self).toOpaque(),
                                            retain: nil, release: nil, copyDescription: nil)
        let callback: FSEventStreamCallback = { _, info, count, paths, _, _ in
            guard let info else { return }
            let vigilante = Unmanaged<SessionsWatcher>.fromOpaque(info).takeUnretainedValue()
            let rutas = (unsafeBitCast(paths, to: NSArray.self) as? [String]) ?? []
            vigilante.handle(Array(rutas.prefix(count)))
        }
        let flags = kFSEventStreamCreateFlagUseCFTypes | kFSEventStreamCreateFlagFileEvents
        guard let nuevo = FSEventStreamCreate(nil, callback, &contexto, [vigilada.path] as CFArray,
                                              FSEventStreamEventId(kFSEventStreamEventIdSinceNow), latency,
                                              FSEventStreamCreateFlags(flags)) else { return false }
        FSEventStreamSetDispatchQueue(nuevo, queue)
        guard FSEventStreamStart(nuevo) else {
            FSEventStreamInvalidate(nuevo)
            FSEventStreamRelease(nuevo)
            return false
        }
        stream = nuevo
        return true
    }

    public func stop() {
        guard let stream else { return }
        FSEventStreamStop(stream)
        FSEventStreamInvalidate(stream)
        FSEventStreamRelease(stream)
        self.stream = nil
    }

    deinit { stop() }

    private func handle(_ paths: [String]) {
        let ids = Set(paths.compactMap { Self.sessionID(of: $0, root: root.path) })
        guard !ids.isEmpty else { return }
        DispatchQueue.main.async { [onChange] in onChange(ids) }
    }
}
