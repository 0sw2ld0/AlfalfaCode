import Foundation

/// Los mensajes `alfalfa.*` y los bloques `payload.alfalfa` del núcleo, de vuelta a los eventos de v2 que ya
/// decodifica la app (`AlfalfaMessage.decode(object:)`).
enum PacaiExtension {

    // MARK: - Métodos

    /// El evento v2 de `alfalfa.<name>`. `time`: la hora del sobre, para los que v2 fechaba y la extensión no.
    static func event(_ name: String, payload: [String: Any], time: Double) -> [String: Any]? {
        var object = PacaiV2.object(payload)
        switch name {
        case "user_message":
            // El eco del mensaje del usuario: `evento` `user` con su id de cola.
            var event: [String: Any] = ["tipo": "evento", "rol": "user", "texto": payload["text"] as? String ?? "",
                                        "hora": time]
            event["id_cola"] = payload["input_id"]
            event["turno"] = payload["turn"]
            return event
        case "notice":
            object.removeValue(forKey: "run_id")
            return agentEvent(object)
        case "subagent_event":
            object["tipo"] = "subagente_evento"
            return object
        case "plan":
            return plan(payload, explanation: payload["explanation"])
        case "browser_cancel":
            guard let id = payload["tool_call_id"] as? String else { return nil }
            return ["tipo": "navegador", "id": id, "accion": "cancelar_relevo"]
        case "context":
            PacaiV2.fix(&object, in: "tarifa", "modo", field: "mode", among: PacaiV2.contextModes)
            object["tipo"] = "contexto"
            return object
        default:
            object["tipo"] = PacaiV2.type(ofExtension: name)
            fixValues(&object)
            return object
        }
    }

    /// Un `evento` de v2 (de `alfalfa.notice` o de un mensaje del núcleo) con sus valores enumerados de vuelta.
    static func agentEvent(_ fields: [String: Any]) -> [String: Any] {
        var object = fields
        object["tipo"] = "evento"
        PacaiV2.fix(&object, in: "compactacion", "estado", field: "status", among: PacaiV2.compactionStates)
        PacaiV2.fix(&object, in: "compactacion", "motivo", field: "reason", among: ["auto", "manual", "umbral"])
        PacaiV2.fix(&object, in: "tarifa", "modo", field: "mode", among: PacaiV2.contextModes)
        PacaiV2.fix(&object, in: "herramienta", "fase", field: "phase", among: ["inicio", "fin"])
        if var imported = object["importadas"] as? [String: Any] {
            PacaiV2.fixRows(&imported, "pendientes", "tipo", field: "type", among: ["agente", "comando"])
            PacaiV2.fixRows(&imported, "pendientes", "estado", field: "status", among: ["pendiente", "cambio"])
            object["importadas"] = imported
        }
        return object
    }

    /// `alfalfa.closing`: el `turno_terminado` de v2.
    static func turnFinished(_ payload: [String: Any]) -> [String: Any] {
        var object = PacaiV2.object(payload)
        object.removeValue(forKey: "run_id")
        object["tipo"] = "turno_terminado"
        PacaiV2.fix(&object, "estado", field: "status", among: ["cortado"])
        if var closing = object["cierre"] as? [String: Any] {
            PacaiV2.fix(&closing, in: "revision_codigo", "estado", field: "status", among: PacaiV2.reviewStates)
            object["cierre"] = closing
        }
        return object
    }

    /// El `plan` de v2: los pasos ya van en inglés en los dos protocolos (`pending`, `in_progress`, `completed`).
    static func plan(_ payload: [String: Any], explanation: Any?) -> [String: Any] {
        let steps = (payload["steps"] as? [[String: Any]] ?? []).map { step -> [String: Any] in
            ["paso": step["text"] as? String ?? "", "estado": step["status"] as? String ?? "pending"]
        }
        var object: [String: Any] = ["tipo": "plan", "pasos": steps]
        object["explicacion"] = explanation
        return object
    }

    /// El `permiso` de v2 de un `approval_request`: lo del núcleo más su bloque `payload.alfalfa`.
    static func permission(_ payload: [String: Any], id: String) -> [String: Any] {
        let extra = payload["alfalfa"] as? [String: Any] ?? [:]
        var object = PacaiV2.object(extra)
        object["tipo"] = "permiso"
        object["id"] = id
        if object["argv"] == nil { object["argv"] = (payload["arguments"] as? [String: Any])?["argv"] ?? [] }
        if object["proposito"] == nil { object["proposito"] = (payload["arguments"] as? [String: Any])?["purpose"] }
        PacaiV2.fix(&object, "motivo", field: "reason", among: PacaiV2.reasons)
        PacaiV2.fix(&object, "motivos", field: "reasons", among: PacaiV2.reasons)
        PacaiV2.fix(&object, "prefijo_alcance", field: "prefix_scope", among: ["sin_sandbox", "red", "sandbox"])
        // Los argumentos de una herramienta MCP son del servidor: van tal cual, sin nombres de v2.
        if var mcp = object["mcp"] as? [String: Any],
           let arguments = (extra["mcp"] as? [String: Any])?["arguments"] {
            mcp["argumentos"] = arguments
            object["mcp"] = mcp
        }
        return object
    }

    /// Los valores enumerados que lee la app, por tipo de evento (también el `error` del núcleo).
    static func fixValues(_ object: inout [String: Any]) {
        switch object["tipo"] as? String {
        case "sesion":
            PacaiV2.fix(&object, "modo", field: "mode", among: PacaiV2.modes)
            PacaiV2.fix(&object, "contexto", field: "context", among: PacaiV2.contextModes)
        case "subagentes":
            PacaiV2.fixRows(&object, "subagentes", "estado", field: "status", among: PacaiV2.subagentStates)
        case "encargos":
            guard var queue = object["cola"] as? [String: Any] else { return }
            PacaiV2.fix(&queue, "estado", field: "status", among: PacaiV2.queueStates)
            PacaiV2.fix(&queue, "modo", field: "mode", among: ["serie", "paralelo"])
            PacaiV2.fix(&queue, "permisos", field: "permissions", among: PacaiV2.modes)
            PacaiV2.fixRows(&queue, "encargos", "estado", field: "status", among: PacaiV2.jobStates)
            if let rows = queue["encargos"] as? [[String: Any]] {
                queue["encargos"] = rows.map { row -> [String: Any] in
                    var row = row
                    PacaiV2.fix(&row, in: "limpieza", "estado", field: "status", among: PacaiV2.cleanupStates)
                    return row
                }
            }
            object["cola"] = queue
        case "revision_codigo":
            PacaiV2.fix(&object, in: "revision", "estado", field: "status", among: PacaiV2.reviewStates)
        case "quitado_de_cola":
            PacaiV2.fix(&object, "motivo", field: "reason", among: ["entregado", "desconocido"])
        case "presupuesto":
            PacaiV2.fix(&object, "aviso", field: "warning", among: ["sesion", "mes"])
            PacaiV2.fix(&object, "agotado", field: "exhausted", among: ["sesion", "mes"])
        case "aprendizaje_propuesto":
            PacaiV2.fix(&object, "clase", field: "kind", among: ["memoria", "habilidad"])
            PacaiV2.fix(&object, "almacen", field: "store", among: PacaiV2.scopes)
            PacaiV2.fix(&object, "alcance", field: "scope", among: PacaiV2.scopes)
            PacaiV2.fix(&object, "accion", field: "action", among: ["crear", "restaurar", "borrar", "parchear"])
        case "error":
            PacaiV2.fix(&object, "motivo", field: "reason", among: ["sin_vision"])
        default:
            break
        }
    }
}
