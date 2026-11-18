import Foundation

/// La transcripción que el motor escribe de cada sesión (`<sesiones>/<id>/<id>.jsonl`): una línea JSON por evento,
/// en cuanto ocurre (`TerminalManager.event`). Seguirla deja ver paso a paso una sesión que trabaja en otro proceso
/// sin releer el historial entero. No trae el texto mientras se escribe: el motor apunta cada mensaje al terminarlo.
public enum SessionLog {
    public static func url(sessionID: String, root: URL = SessionsWatcher.directory()) -> URL {
        root.appendingPathComponent(sessionID).appendingPathComponent(sessionID + ".jsonl")
    }

    public static func size(of url: URL) -> UInt64 {
        ((try? FileManager.default.attributesOfItem(atPath: url.path))?[.size] as? NSNumber)?.uint64Value ?? 0
    }

    /// Un evento de la transcripción tal como lo da `--historial` (`alfalfa/stdio.py`, `history`): sin los avisos de
    /// reintento de transporte ni las compactaciones a medias, con el texto recortado a 20 000 caracteres.
    public static func event(fromLine line: String) -> AgentEvent? {
        guard let data = line.data(using: .utf8),
              let row = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let text = row["text"] as? String else { return nil }
        let role = row["role"] as? String ?? "system"
        if role == "system" && isTransportNotice(text) { return nil }
        var saved: [String: Any] = ["rol": role, "texto": String(text.prefix(20_000)), "hora": row["time"] ?? 0]
        if let compaction = row["compactacion"] as? [String: Any] {
            if compaction["estado"] as? String == "en_curso" { return nil }
            saved["compactacion"] = compaction
        }
        if let turn = row["turno"] as? String, !turn.isEmpty { saved["turno"] = turn }
        for key in ["herramienta", "deshacer", "tarifa", "importadas"] {
            if let value = row[key] as? [String: Any] { saved[key] = value }
        }
        return AlfalfaMessage.agentEvent(saved)
    }

    /// `_AVISO_DE_TRANSPORTE` de `alfalfa/reintentos/fallos.py`, con `match`: desde el principio del texto.
    private static let transportNotice = try! NSRegularExpression(pattern: #"^\w[\w .-]{0,40} transporte: intento \d+/\d+\b"#)

    static func isTransportNotice(_ text: String) -> Bool {
        transportNotice.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) != nil
    }
}

/// Lee lo que se va añadiendo a una transcripción a partir de `offset`. Una línea a medias espera a su salto de
/// línea; los eventos con hora hasta `after` se saltan porque ya estaban en el historial que se pintó.
public final class SessionLogTail {
    public enum Update: Equatable {
        case events([AgentEvent])
        /// El archivo encogió o no se puede seguir: hay que releer el historial entero.
        case restart
    }

    public let url: URL
    public private(set) var offset: UInt64
    private let after: Double
    private var pending = Data()

    public init(url: URL, offset: UInt64, after: Double = 0) {
        self.url = url
        self.offset = offset
        self.after = after
    }

    public func read() -> Update {
        if (try? url.resourceValues(forKeys: [.isSymbolicLinkKey]))?.isSymbolicLink == true { return .restart }
        let size = SessionLog.size(of: url)
        if size < offset { return .restart }
        guard size > offset else { return .events([]) }
        guard let handle = try? FileHandle(forReadingFrom: url) else { return .events([]) }
        defer { try? handle.close() }
        guard (try? handle.seek(toOffset: offset)) != nil else { return .restart }
        let data = (try? handle.readToEnd()) ?? Data()
        offset += UInt64(data.count)
        pending.append(data)
        var events: [AgentEvent] = []
        while let newline = pending.firstIndex(of: 0x0A) {
            let line = pending.subdata(in: pending.startIndex..<newline)
            pending = pending.subdata(in: pending.index(after: newline)..<pending.endIndex)
            if let text = String(data: line, encoding: .utf8), let event = SessionLog.event(fromLine: text),
               event.time > after {
                events.append(event)
            }
        }
        return .events(events)
    }
}
