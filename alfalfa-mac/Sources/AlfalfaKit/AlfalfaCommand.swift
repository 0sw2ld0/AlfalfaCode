import Foundation

/// Entradas hacia el proceso hijo: una línea JSON por mensaje.
public enum AlfalfaCommand: Equatable, Sendable {
    /// `session`: aprobar también lo mismo durante el resto de la sesión.
    case permission(id: String, approved: Bool, session: Bool = false, site: String? = nil, always: Bool = false)
    case message(String, images: [String] = [], id: String? = nil)
    case cancel
    /// «Enviar ahora» de un mensaje en cola: corta el turno y el mensaje abre el siguiente.
    case sendNow(String, id: String? = nil)
    /// «Redirigir» (steer de Codex): el modelo lee ya los mensajes en cola, sin cortar el turno.
    case redirect
    /// Quita el mensaje si aún no entró en el contexto del modelo.
    case unqueue(String)
    /// Compactar (`/compactar`, «Compactar sesión»): ya entre turnos, o antes de la siguiente
    /// respuesta del modelo si hay uno en marcha. Las instrucciones centran el resumen.
    case compact(instructions: String? = nil)
    /// `/contexto` y el aviso de tarifa: Ahorro o Contexto completo. Vale ya, también a mitad de turno.
    case contextMode(ContextMode)
    /// «Seguir en una sesión nueva»: corta el turno, compacta y abre otra con el resumen.
    case continueInNewSession
    /// Detener una orden en segundo plano (`p1`) o todas (`todos`).
    case stopProcess(String)
    /// Modelo y esfuerzo para los turnos siguientes.
    case settings(model: String?, effort: String?, mode: String? = nil)
    /// El interruptor de Ajustes ▸ General: mantener el Mac despierto mientras trabaja. Vale ya.
    case keepAwake(Bool)
    /// El interruptor de Ajustes ▸ General: ver los errores al editar. Vale desde la siguiente edición.
    case diagnostics(Bool)
    /// Ajustes ▸ General ▸ Diagnósticos de Swift (sourcekit-lsp, fuera del sandbox): desde la siguiente edición.
    case swiftDiagnostics(Bool)
    /// El interruptor de Ajustes ▸ General: firmar los commits del agente. Vale desde el siguiente `git commit`.
    case commitSignature(Bool)
    /// Ajustes ▸ General ▸ Presupuesto: los topes en dólares (nil, sin tope). Vale ya, también a mitad de turno.
    case budget(session: Double?, month: Double?)
    /// La respuesta del navegador propio a una petición `navegador`.
    case browserReply(BrowserReply)
    case exportBrowserTest(id: String, path: String)
    /// La tarjeta de una propuesta de la revisión al cerrar el turno: aplicarla o descartarla.
    case learning(id: String, apply: Bool)
    case reviewCode(turn: String, model: String)
    case cancelCodeReview
    case taskQueue(TaskQueueAction, tasks: [String] = [], mode: String = "serie", parallelism: Int = 2)

    public var line: String {
        let object: [String: Any]
        switch self {
        case let .permission(id, approved, session, site, always):
            object = approved && site != nil ? ["tipo": "permiso", "id": id, "aprobado": true, "sitio": site!]
                : approved && always ? ["tipo": "permiso", "id": id, "aprobado": true, "siempre": true]
                : approved && session ? ["tipo": "permiso", "id": id, "aprobado": true, "sesion": true]
                : ["tipo": "permiso", "id": id, "aprobado": approved]
        case let .message(text, images, id):
            var fields: [String: Any] = ["tipo": "mensaje", "texto": text]
            // `images` lleva imágenes y archivos: el motor los recibe por separado.
            let parts = Attachments.partition(images)
            if !parts.images.isEmpty { fields["imagenes"] = parts.images }
            if !parts.files.isEmpty { fields["archivos"] = parts.files }
            if let id { fields["id"] = id }
            object = fields
        case .cancel: object = ["tipo": "cancelar"]
        case let .sendNow(text, id):
            object = id.map { ["tipo": "enviar_ahora", "texto": text, "id": $0] } ?? ["tipo": "enviar_ahora", "texto": text]
        case .redirect: object = ["tipo": "redirigir"]
        case let .unqueue(id): object = ["tipo": "quitar_de_cola", "id": id]
        case let .contextMode(mode): object = ["tipo": "contexto", "modo": mode.rawValue]
        case .continueInNewSession: object = ["tipo": "continuar_en_nueva"]
        case let .stopProcess(id): object = ["tipo": "detener_proceso", "id": id]
        case let .keepAwake(on): object = ["tipo": "mantener_despierto", "activo": on]
        case let .diagnostics(on): object = ["tipo": "diagnosticos", "activo": on]
        case let .swiftDiagnostics(on): object = ["tipo": "diagnosticos_swift", "activo": on]
        case let .commitSignature(on): object = ["tipo": "firma", "activo": on]
        case let .budget(session, month):
            object = ["tipo": "presupuesto", "sesion": session.map { $0 as Any } ?? NSNull(),
                      "mes": month.map { $0 as Any } ?? NSNull()]
        case let .browserReply(reply): object = reply.object
        case let .exportBrowserTest(id, path): object = ["tipo": "exportar_prueba_navegador", "id": id, "ruta": path]
        case let .learning(id, apply): object = ["tipo": "aprendizaje", "id": id, "aplicar": apply]
        case let .reviewCode(turn, model): object = ["tipo": "revisar_codigo", "turno": turn, "modelo": model]
        case .cancelCodeReview: object = ["tipo": "cancelar_revision_codigo"]
        case let .taskQueue(action, tasks, mode, parallelism):
            var fields: [String: Any] = ["tipo": "encargos", "accion": action.rawValue]
            if action == .prepare { fields.merge(["encargos": tasks, "modo": mode, "paralelos": parallelism]) { _, v in v } }
            object = fields
        case let .compact(instructions):
            object = instructions.map { ["tipo": "compactar", "instrucciones": $0] } ?? ["tipo": "compactar"]
        case let .settings(model, effort, mode):
            var fields: [String: Any] = ["tipo": "ajustes"]
            if let model { fields["modelo"] = model }
            if let effort { fields["esfuerzo"] = effort }
            if let mode { fields["modo"] = mode }
            object = fields
        }
        let options: JSONSerialization.WritingOptions = [.sortedKeys, .withoutEscapingSlashes]
        let data = (try? JSONSerialization.data(withJSONObject: object, options: options)) ?? Data()
        return String(decoding: data, as: UTF8.self) + "\n"
    }
}
