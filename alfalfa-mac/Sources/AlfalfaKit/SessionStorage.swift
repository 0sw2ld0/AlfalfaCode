import Foundation

/// Una sesión guardada vista por lo que ocupa (`alfalfa --espacio-sesiones --json`).
public struct StoredSessionUsage: Identifiable, Equatable, Sendable {

    // MARK: - Propiedades

    public let id: String
    public let title: String?
    public let cwd: String?
    /// Bytes en disco de su carpeta (lo que se libera al vaciar la Papelera).
    public let bytes: Int64
    /// De esos, los de las imágenes adjuntas o generadas.
    public let images: Int64
    public let lastUsed: Date?
    public let daysUnused: Int?
    /// Alguien tiene su cerrojo: está trabajando o abierta en la app. El motor no la toca.
    public let inUse: Bool
    public let pinned: Bool
    public let state: String

    public var displayTitle: String { title ?? "Sesión \(id)" }

    // MARK: - Inicialización

    public init(id: String, title: String? = nil, cwd: String? = nil, bytes: Int64 = 0, images: Int64 = 0,
                lastUsed: Date? = nil, daysUnused: Int? = nil, inUse: Bool = false, pinned: Bool = false,
                state: String = "completed") {
        self.id = id
        self.title = title
        self.cwd = cwd
        self.bytes = bytes
        self.images = images
        self.lastUsed = lastUsed
        self.daysUnused = daysUnused
        self.inUse = inUse
        self.pinned = pinned
        self.state = state
    }

    init?(_ row: [String: Any]) {
        guard let id = row["id"] as? String, !id.isEmpty else { return nil }
        let used = (row["ultimo_uso"] as? NSNumber)?.doubleValue ?? 0
        self.init(id: id, title: (row["titulo"] as? String).flatMap { $0.isEmpty ? nil : $0 },
                  cwd: row["cwd"] as? String, bytes: (row["bytes"] as? NSNumber)?.int64Value ?? 0,
                  images: (row["imagenes"] as? NSNumber)?.int64Value ?? 0,
                  lastUsed: used > 0 ? Date(timeIntervalSince1970: used) : nil,
                  daysUnused: (row["dias_sin_usar"] as? NSNumber)?.intValue,
                  inUse: row["en_uso"] as? Bool ?? false, pinned: row["fijada"] as? Bool ?? false,
                  state: row["estado"] as? String ?? "completed")
    }
}

/// `espacio_sesiones`: todas las sesiones con lo que ocupan, y los totales del hogar.
public struct SessionStorage: Equatable, Sendable {

    // MARK: - Tipos

    public enum Order: String, CaseIterable, Sendable {
        case size, lastUse
    }

    // MARK: - Propiedades

    public var sessions: [StoredSessionUsage]
    public var sessionsBytes: Int64
    public var indexBytes: Int64
    public var snapshotsBytes: Int64
    /// Lo que hay que decir antes de mover nada a la Papelera (lo escribe el motor).
    public var notice: String

    // MARK: - Inicialización

    public init(sessions: [StoredSessionUsage], sessionsBytes: Int64 = 0, indexBytes: Int64 = 0,
                snapshotsBytes: Int64 = 0, notice: String = "") {
        self.sessions = sessions
        self.sessionsBytes = sessionsBytes
        self.indexBytes = indexBytes
        self.snapshotsBytes = snapshotsBytes
        self.notice = notice
    }

    init?(_ object: [String: Any]) {
        guard let rows = object["sesiones"] as? [[String: Any]] else { return nil }
        let totals = object["totales"] as? [String: Any] ?? [:]
        func bytes(_ key: String) -> Int64 { (totals[key] as? NSNumber)?.int64Value ?? 0 }
        self.init(sessions: rows.compactMap(StoredSessionUsage.init), sessionsBytes: bytes("sesiones"),
                  indexBytes: bytes("indice"), snapshotsBytes: bytes("instantaneas"),
                  notice: object["nota"] as? String ?? "")
    }

    // MARK: - Métodos públicos

    /// Las que se pueden liberar: ni en uso, ni abiertas en la app (`open`).
    public func freeable(_ session: StoredSessionUsage, open: Set<String>) -> Bool {
        !session.inUse && !open.contains(session.id)
    }

    /// «Sin usar hace más de `days` días»: las que se pueden liberar, sin las fijadas.
    public func unused(moreThan days: Int, open: Set<String>, now: Date = Date()) -> Set<String> {
        Set(sessions.filter { session in
            guard freeable(session, open: open), !session.pinned, let used = session.lastUsed else { return false }
            return now.timeIntervalSince(used) > Double(days) * 86_400
        }.map(\.id))
    }

    /// De la que más ocupa a la que menos, o de la menos usada a la más.
    public func sorted(by order: Order) -> [StoredSessionUsage] {
        switch order {
        case .size:
            return sessions.sorted { ($0.bytes, $1.id) > ($1.bytes, $0.id) }
        case .lastUse:
            return sessions.sorted { ($0.lastUsed ?? .distantPast, $0.id) < ($1.lastUsed ?? .distantPast, $1.id) }
        }
    }

    public func bytes(of ids: Set<String>) -> Int64 {
        sessions.filter { ids.contains($0.id) }.reduce(0) { $0 + $1.bytes }
    }
}

/// `sesiones_liberadas`: respuesta a `--liberar-sesiones … --si --json`.
public struct SessionsFreed: Equatable, Sendable {

    // MARK: - Tipos

    public struct Skipped: Equatable, Sendable {
        public let id: String
        /// `en_uso`, `no_existe` o `error`.
        public let reason: String
    }

    // MARK: - Propiedades

    public var moved: [String]
    public var skipped: [Skipped]
    /// Bytes de las sesiones movidas: vuelven al vaciar la Papelera.
    public var freed: Int64
    /// Lo que encogieron ya el índice de búsqueda y las instantáneas de deshacer.
    public var indexFreed: Int64
    public var snapshotsFreed: Int64

    /// «Movidas 3 sesiones a la Papelera (1,2 GB)…», para el aviso de Ajustes.
    public var summary: String {
        let format = ByteCountFormatter()
        format.countStyle = .file
        var text = moved.count == 1 ? "1 sesión movida a la Papelera" : "\(moved.count) sesiones movidas a la Papelera"
        text += " (" + format.string(fromByteCount: freed) + ", vuelven al vaciarla)"
        let now = indexFreed + snapshotsFreed
        if now > 0 { text += "; el índice y las instantáneas ya liberaron " + format.string(fromByteCount: now) }
        text += "."
        let busy = skipped.filter { $0.reason == "en_uso" }.count
        if busy > 0 {
            text += busy == 1 ? " 1 estaba en uso y no se tocó." : " \(busy) estaban en uso y no se tocaron."
        }
        return text
    }

    // MARK: - Inicialización

    public init(moved: [String], skipped: [Skipped] = [], freed: Int64 = 0, indexFreed: Int64 = 0,
                snapshotsFreed: Int64 = 0) {
        self.moved = moved
        self.skipped = skipped
        self.freed = freed
        self.indexFreed = indexFreed
        self.snapshotsFreed = snapshotsFreed
    }

    init?(_ object: [String: Any]) {
        guard let moved = object["movidas"] as? [[String: Any]] else { return nil }
        func shrink(_ key: String) -> Int64 {
            guard let part = object[key] as? [String: Any],
                  let before = (part["antes"] as? NSNumber)?.int64Value,
                  let after = (part["despues"] as? NSNumber)?.int64Value else { return 0 }
            return max(0, before - after)
        }
        let skipped = (object["saltadas"] as? [[String: Any]] ?? []).compactMap { row -> Skipped? in
            guard let id = row["id"] as? String else { return nil }
            return Skipped(id: id, reason: row["motivo"] as? String ?? "error")
        }
        self.init(moved: moved.compactMap { $0["id"] as? String }, skipped: skipped,
                  freed: (object["liberado"] as? NSNumber)?.int64Value ?? 0,
                  indexFreed: shrink("indice"), snapshotsFreed: shrink("instantaneas"))
    }
}
