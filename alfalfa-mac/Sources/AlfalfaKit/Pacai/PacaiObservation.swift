import Foundation

/// Mirar en vivo una sesión que trabaja en otro proceso: un `alfalfa --pacai` lanzado desde la terminal, desde Claude
/// Code (skill `encargar-a-alfalfa`) o desde otro cliente PACAI. Ese proceso abre un socket en la carpeta de la sesión
/// (`<sesiones>/<id>/vivo.sock`, `alfalfa/pacai/observadores.py`) y la app se conecta como un cliente que sólo mira:
/// su `hello` lleva `observe: true`, no declara herramientas y no manda nada más.
///
/// El agente contesta con un `snapshot` (lo que está en marcha en ese momento) y después le copia lo que ve el dueño.
/// La traducción es la de `PacaiClient`, así que la app recibe los mismos `AlfalfaMessage` que en sus sesiones.
/// No lee ni escribe: `PacaiObserver` le pasa cada línea.
public final class PacaiObservation {

    // MARK: - Tipos

    public enum Update: Equatable {
        /// Llegó la foto. La transcripción en disco vale hasta `historyUntil` (hora de sus filas): lo posterior llega
        /// en vivo. `state` es lo que está en marcha (permisos pendientes, uso, contexto, plan, subagentes…), sin los
        /// pasos de la conversación, que ya trae el historial. `running`: hay una ejecución en marcha.
        case ready(sessionID: String?, historyUntil: Double?, state: [AlfalfaMessage], running: Bool)
        /// Lo que pasa desde la foto.
        case messages([AlfalfaMessage])
        /// El agente no aceptó al observador (otra versión, demasiados observadores…).
        case rejected(String)
    }

    // MARK: - Propiedades

    public let session: String
    public private(set) var isReady = false
    private let client: PacaiClient
    private var helloID: String?

    // MARK: - Inicializadores

    public init(session: String = PacaiClient.newSession(), clock: @escaping () -> Date = Date.init,
                ids: @escaping () -> String = PacaiClient.newID) {
        self.session = session
        client = PacaiClient(session: session, clock: clock, ids: ids)
    }

    // MARK: - Métodos públicos

    /// El `hello` de quien sólo mira, con su salto de línea.
    public func start() -> String {
        let line = client.envelope("hello", ["versions": [PacaiClient.version], "profiles": ["work"],
                                             "extensions": ["alfalfa"], "observe": true])
        helloID = Self.object(line)?["id"] as? String
        return line
    }

    /// Una línea del agente: la foto, lo que pasa después o el rechazo. nil si no cambia nada (su `hello`, el
    /// `session_started`, un latido).
    public func receive(_ line: String) -> Update? {
        guard let message = Self.object(line), message["pacai"] as? String == PacaiClient.version,
              let type = message["type"] as? String else { return nil }
        guard isReady else { return opening(type, message) }
        let messages = Self.watchable(client.receive(line).events)
        return messages.isEmpty ? nil : .messages(messages)
    }

    // MARK: - Métodos privados

    private func opening(_ type: String, _ message: [String: Any]) -> Update? {
        let payload = message["payload"] as? [String: Any] ?? [:]
        switch type {
        case "error":
            return .rejected(payload["message"] as? String ?? "El agente no admite observadores.")
        case "close":
            return .rejected(payload["reason"] as? String ?? "El agente cerró la conexión.")
        case "snapshot" where helloID == nil || message["reply_to"] as? String == helloID:
            isReady = true
            // Primero lo que sigue en marcha (ejecuciones, herramientas abiertas, el último uso…) y después los
            // permisos pendientes, que remiten a sus herramientas.
            let earlier = (payload["last_messages"] as? [[String: Any]] ?? [])
                + (payload["pending_approvals"] as? [[String: Any]] ?? [])
            var state: [AlfalfaMessage] = []
            for previous in earlier {
                guard let line = Self.line(previous) else { continue }
                state += client.receive(line).events
            }
            let extra = payload["alfalfa"] as? [String: Any]
            return .ready(sessionID: extra?["session_id"] as? String,
                          historyUntil: (extra?["history_until"] as? NSNumber)?.doubleValue,
                          state: Self.watchable(state).filter(Self.isState),
                          running: !(payload["runs"] as? [Any] ?? []).isEmpty)
        default:
            return nil
        }
    }

    /// Lo que la app puede enseñar de una sesión que no es suya: nada que le pida actuar (el navegador del dueño) ni
    /// que conteste a mensajes que no mandó ella.
    static func watchable(_ messages: [AlfalfaMessage]) -> [AlfalfaMessage] {
        messages.filter { message in
            switch message {
            case .browser, .messageRejected, .unqueued: return false
            default: return true
            }
        }
    }

    /// Estado, no pasos: la foto no repite lo que el historial ya pinta.
    static func isState(_ message: AlfalfaMessage) -> Bool {
        switch message {
        case .event, .subagentEvent, .error, .turnFinished, .finished: return false
        default: return true
        }
    }

    static func object(_ line: String) -> [String: Any]? {
        guard let data = line.data(using: .utf8) else { return nil }
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }

    static func line(_ object: [String: Any]) -> String? {
        guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.withoutEscapingSlashes])
        else { return nil }
        return String(decoding: data, as: UTF8.self)
    }
}
