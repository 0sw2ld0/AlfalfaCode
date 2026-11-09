import Foundation

/// Versión del protocolo JSONL de `alfalfa --stdio`.
public let protocolVersion = 2

public struct SessionInfo: Equatable, Sendable {
    public var id: String
    public var cwd: String
    public var model: String
    public var effort: String
    /// Modo de permisos (`auto`, `manual`, `ediciones`, `plan`, `omitir`); vacío en motores anteriores.
    public var mode: String = ""
    /// Ahorro o contexto completo (`contexto`); nil en motores anteriores.
    public var contextMode: ContextMode?
}

public struct AgentEvent: Equatable, Sendable {
    public var role: String
    public var text: String
    public var time: Double
    /// Turno al que pertenece (`turno`); nil en motores anteriores o eventos sin turno.
    public var turn: String?
    /// Aviso de tarifa por contexto largo (`tarifa`): la conversación lo pinta con sus botones.
    public var priceWarning: PriceWarning?
    /// Datos de una compactación (`compactacion`): la conversación pinta su marca.
    public var compaction: Compaction?
    /// Llamada a herramienta (`herramienta`): nombre, fase y descripción; nil en motores anteriores.
    public var tool: ToolInfo? = nil
    /// El código se deshizo o se rehízo (`deshacer`): la conversación pinta su marca con «Rehacer».
    public var undo: UndoStep? = nil
}

/// Una orden en segundo plano (`run_command` con `background`), como los background
/// terminals de Codex: un juego, una app o un servidor que sigue abierto.
public struct BackgroundProcess: Equatable, Sendable, Identifiable {
    public var id: String
    public var argv: [String]
    public var purpose: String
    /// Segundos desde 1970.
    public var since: Double
    public var alive: Bool
    public var exitCode: Int?
    public var unsandboxed: Bool
    /// Lo detuvo el usuario o el agente (no terminó por su cuenta).
    public var stopped: Bool
    public var lastLines: [String]

    public init(id: String, argv: [String], purpose: String = "", since: Double = 0, alive: Bool = true,
                exitCode: Int? = nil, unsandboxed: Bool = false, stopped: Bool = false, lastLines: [String] = []) {
        self.id = id
        self.argv = argv
        self.purpose = purpose
        self.since = since
        self.alive = alive
        self.exitCode = exitCode
        self.unsandboxed = unsandboxed
        self.stopped = stopped
        self.lastLines = lastLines
    }

    public var command: String { argv.joined(separator: " ") }
}

/// Un paso del plan del agente (`update_plan`, como el de Codex).
public struct PlanStep: Equatable, Sendable {
    public enum Status: String, Sendable { case pending, inProgress = "in_progress", completed }
    public var text: String
    public var status: Status

    public init(_ text: String, _ status: Status) {
        self.text = text
        self.status = status
    }
}

/// La lista de tareas que el agente mantiene mientras trabaja.
public struct Plan: Equatable, Sendable {
    public var steps: [PlanStep]
    public var explanation: String?

    public init(steps: [PlanStep], explanation: String? = nil) {
        self.steps = steps
        self.explanation = explanation
    }

    public var isDone: Bool { steps.allSatisfy { $0.status == .completed } }
}

/// Un subagente de la sesión (`spawn_agent`), como los de Codex y Claude Code.
public struct Subagent: Equatable, Sendable, Identifiable {
    public var id: String
    public var name: String
    public var type: String
    /// iniciando, trabajando, terminado, fallido, cerrado o interrumpido.
    public var state: String
    public var model: String
    public var since: Double
    public var until: Double?
    public var activity: String
    public var steps: Int
    public var report: String
    public var error: String

    public init(id: String, name: String, type: String = "general", state: String = "trabajando", model: String = "",
                since: Double = 0, until: Double? = nil, activity: String = "", steps: Int = 0, report: String = "",
                error: String = "") {
        self.id = id
        self.name = name
        self.type = type
        self.state = state
        self.model = model
        self.since = since
        self.until = until
        self.activity = activity
        self.steps = steps
        self.report = report
        self.error = error
    }

    public var working: Bool { state == "iniciando" || state == "trabajando" }
}

/// Una compactación del contexto, como «Conversation compacted» de Claude Code.
public struct Compaction: Equatable, Sendable {
    public enum State: String, Sendable { case running = "en_curso", done = "hecha", failed = "fallida" }

    public var state: State
    /// `auto` (al llegar al umbral) o `manual` (botón o /compactar).
    public var reason: String
    /// Tokens aproximados de la conversación antes y después.
    public var before: Int
    public var after: Int
    /// El resumen de traspaso que sustituye a lo anterior.
    public var summary: String
    /// Lo que pidió el usuario para el resumen (`/compactar instrucciones`).
    public var instructions: String?

    public init(state: State, reason: String, before: Int = 0, after: Int = 0, summary: String = "",
                instructions: String? = nil) {
        self.state = state
        self.reason = reason
        self.before = before
        self.after = after
        self.summary = summary
        self.instructions = instructions
    }
}

/// Motivo de un permiso de la política on-request (`motivo` del evento `permiso`).
public enum PermissionReason: String, CaseIterable, Equatable, Sendable {
    case dangerous = "peligroso"
    case network = "red"
    case leaveSandbox = "salir_sandbox"
    case retry = "reintento"
    /// Manual y Aceptar ediciones piden permiso para cada orden que no sólo lee.
    case mode = "modo"
    /// Manual pide permiso para cada edición de archivos.
    case edit = "edicion"
    /// Mirar o manejar otra app del Mac (app_state, app_action), una vez por app.
    case app = "app"
    /// El navegador propio: enviar un formulario, escribir una contraseña, descargar, JavaScript
    /// que cambia la página o abrir una dirección local.
    case browser = "navegador"

    /// Nombre corto que muestra la tarjeta de permiso.
    public var title: String {
        switch self {
        case .dangerous: return "Orden peligrosa"
        case .network: return "Necesita red"
        case .leaveSandbox: return "Salir del sandbox"
        case .retry: return "Reintento sin sandbox"
        case .mode: return "El modo pide permiso"
        case .edit: return "Edición de archivos"
        case .app: return "Controlar una app"
        case .browser: return "Navegador"
        }
    }

    /// Las de mayor alcance se muestran como peligro.
    public var leavesSandbox: Bool { self == .leaveSandbox || self == .retry }
}

/// Un argumento de una llamada MCP: clave y valor JSON ya redactado por el motor.
public struct McpArgument: Equatable, Sendable {
    public var key: String
    public var value: String

    public init(key: String, value: String) {
        self.key = key
        self.value = value
    }
}

/// Llamada MCP que pide permiso (campo `mcp` del evento `permiso`).
/// La app del Mac que el agente quiere mirar o manejar (control de apps).
public struct AppTarget: Equatable, Sendable {
    public var name: String
    public var bundle: String?
    /// Lo primero que quiere hacer: «mirar su ventana», «hacer clic»…
    public var action: String?
    /// Nivel de acceso cuando no es completo: «lectura» (un navegador) o «clic» (una terminal).
    public var level: String?

    public init(name: String, bundle: String? = nil, action: String? = nil, level: String? = nil) {
        self.name = name
        self.bundle = bundle
        self.action = action
        self.level = level
    }

    /// Lo que la tarjeta dice del nivel, o nil si el acceso es completo.
    public var levelText: String? {
        switch level {
        case "lectura": return "Sólo mirar"
        case "clic": return "Sólo mirar y clics sueltos"
        default: return nil
        }
    }
}

public struct McpCall: Equatable, Sendable {
    public var server: String
    public var tool: String
    /// Nombre que ve el modelo: mcp__servidor__herramienta.
    public var name: String
    /// Argumentos estructurados en orden de clave; las credenciales llegan ya como [REDACTADO].
    public var arguments: [McpArgument]
    /// Identificador que correlaciona la tarjeta con el resultado de la llamada.
    public var callID: String

    public init(server: String, tool: String, name: String, arguments: [McpArgument], callID: String) {
        self.server = server
        self.tool = tool
        self.name = name
        self.arguments = arguments
        self.callID = callID
    }
}

/// Desglose MCP del evento `contexto`. Lo calcula el motor sin tokenizador:
/// bytes UTF-8 del JSON compacto de las declaraciones / 4, redondeado hacia arriba.
public struct McpContextUsage: Equatable, Sendable {
    /// Esquemas MCP declarados al modelo (cargados y directos), sin duplicados.
    public var loadedTokens: Int
    public var loadedCount: Int
    /// buscar_herramientas más el resumen de una línea por servidor.
    public var baseTokens: Int
    public var method: String
    public var exact: Bool
    /// Herramientas declaradas con su peso, para la lista del panel.
    public var tools: [ContextEntry]
    /// Catálogo diferido: no está en el contexto hasta que el modelo lo carga.
    public var deferredCount: Int
    public var deferredTokens: Int

    public init(loadedTokens: Int, loadedCount: Int, baseTokens: Int, method: String, exact: Bool,
                tools: [ContextEntry] = [], deferredCount: Int = 0, deferredTokens: Int = 0) {
        self.loadedTokens = loadedTokens
        self.loadedCount = loadedCount
        self.baseTokens = baseTokens
        self.method = method
        self.exact = exact
        self.tools = tools
        self.deferredCount = deferredCount
        self.deferredTokens = deferredTokens
    }

    public var totalTokens: Int { loadedTokens + baseTokens }

    /// Línea del panel: esquemas declarados, base y si el dato es exacto o estimado.
    public var summary: String {
        let schemas = loadedCount == 1 ? "1 esquema cargado" : "\(loadedCount) esquemas cargados"
        return schemas + " · base \(baseTokens) · " + (exact ? "exacto" : "estimado")
    }
}

public struct PermissionRequest: Equatable, Sendable {
    public var id: String
    public var argv: [String]
    public var purpose: String
    /// Motivo principal; nil en eventos anteriores a la política on-request o con un motivo desconocido.
    public var reason: PermissionReason?
    /// Todos los motivos reconocidos de esta única decisión, el principal primero.
    public var reasons: [PermissionReason]
    public var justification: String?
    /// Regla peligrosa que coincidió (`rm con opción de fuerza`, `sudo`…).
    public var rule: String?
    /// Intento fallido que se reintentaría.
    public var retryOf: String?
    /// Explicación del motor al salir del sandbox (fallo de Seatbelt o resultado del intento).
    public var detail: String?
    /// Presente sólo en las llamadas a herramientas MCP (motivo `mcp`).
    public var mcp: McpCall?
    /// Se puede aprobar para el resto de la sesión (`para_sesion`), como «Yes, and don't ask
    /// again for this command» de Codex. Nunca en órdenes peligrosas ni con reglas que preguntan.
    public var forSession: Bool = false
    /// Presente sólo al mirar o manejar otra app (motivo `app`).
    public var app: AppTarget?

    public init(id: String, argv: [String], purpose: String, reason: PermissionReason? = nil,
                reasons: [PermissionReason]? = nil, justification: String? = nil, rule: String? = nil,
                retryOf: String? = nil, detail: String? = nil, mcp: McpCall? = nil, forSession: Bool = false,
                app: AppTarget? = nil) {
        self.forSession = forSession
        self.app = app
        self.id = id
        self.argv = argv
        self.purpose = purpose
        self.reason = reason
        self.reasons = reasons ?? reason.map { [$0] } ?? []
        self.justification = justification
        self.rule = rule
        self.retryOf = retryOf
        self.detail = detail
        self.mcp = mcp
    }

    public var command: String { argv.joined(separator: " ") }

    /// Qué concede aprobar esta única decisión, según el más amplio de sus motivos
    /// (el mismo orden que la pregunta de la terminal); nil sin motivos on-request.
    public var scope: String? {
        if reasons.contains(.retry) {
            return "El sandbox probablemente bloqueó la orden. Se repetirá una vez, igual, sin sandbox: "
                + "con todos los permisos de tu usuario y con red."
        }
        if reasons.contains(.leaveSandbox) {
            return "Se ejecutará sin sandbox, con todos los permisos de tu usuario y con red."
        }
        if reasons.contains(.network) {
            return "Se ejecutará dentro del sandbox con la red abierta sólo para esta orden; la escritura sigue limitada."
        }
        if reasons.contains(.dangerous) { return "Se ejecutará dentro del sandbox, sin red." }
        if reasons.contains(.edit) { return "El modo Manual pide permiso antes de editar archivos." }
        if reasons.contains(.mode) {
            return "Tu modo pide permiso para cada orden que no sólo lee; se ejecutará dentro del sandbox, sin red."
        }
        if reasons.contains(.browser) {
            return "Lo hará el navegador de la sesión, que ves en el panel: aislado, sin tus cuentas "
                + "ni las cookies de Safari."
        }
        return nil
    }
}

/// Reparto estimado del contexto (`desglose`): instrucciones, herramientas, MCP y mensajes,
/// y aparte lo que no es del arnés: archivos de memoria, instrucciones MCP y complementos.
public struct ContextBreakdown: Equatable, Sendable {
    public var system: Int
    public var tools: Int
    public var mcp: Int
    public var messages: Int
    public var memory: Int
    public var mcpInstructions: Int
    public var plugins: Int

    public init(system: Int, tools: Int, mcp: Int, messages: Int, memory: Int = 0, mcpInstructions: Int = 0,
                plugins: Int = 0) {
        self.system = system
        self.tools = tools
        self.mcp = mcp
        self.messages = messages
        self.memory = memory
        self.mcpInstructions = mcpInstructions
        self.plugins = plugins
    }

    public var total: Int { system + tools + mcp + messages + memory + mcpInstructions + plugins }
}

/// Una fila de las listas del panel: una herramienta MCP o un archivo de memoria.
public struct ContextEntry: Equatable, Sendable, Identifiable {
    public var name: String
    public var tokens: Int
    public var id: String { name }

    public init(name: String, tokens: Int) {
        self.name = name
        self.tokens = tokens
    }
}

/// Lo que el motor sabe además del total medido: el uso estimado ahora (lo medido más lo
/// añadido después), el umbral de compactación automática y los archivos de memoria.
public struct ContextDetails: Equatable, Sendable {
    public var estimate: Int?
    public var threshold: Int?
    public var memoryFiles: [ContextEntry]
    /// Tramo de contexto largo del modelo y modo de la sesión (`tarifa`); nil si el modelo no lo tiene.
    public var priceTier: PriceTier?

    public init(estimate: Int? = nil, threshold: Int? = nil, memoryFiles: [ContextEntry] = [],
                priceTier: PriceTier? = nil) {
        self.priceTier = priceTier
        self.estimate = estimate
        self.threshold = threshold
        self.memoryFiles = memoryFiles
    }
}

/// Último contexto medido de una sesión guardada.
public struct SavedContext: Equatable, Sendable {
    public var tokens: Int
    public var limit: Int
    public var breakdown: ContextBreakdown?
    public var details: ContextDetails?

    public init(tokens: Int, limit: Int, breakdown: ContextBreakdown? = nil, details: ContextDetails? = nil) {
        self.tokens = tokens
        self.limit = limit
        self.breakdown = breakdown
        self.details = details
    }
}

public struct Outcome: Equatable, Sendable {
    public var state: String
    public var summary: String
    public var exitCode: Int
    public var tokens: Int
    /// Cierre del turno (herramienta `wrap_up`); nil si el agente no lo dio.
    public var wrapUp: WrapUp?
    /// Id del turno que terminó (`turno`).
    public var turn: String?
    /// Coste estimado de la sesión en USD con los precios de models.dev; nil sin precio conocido.
    public var cost: Double?
    /// Tokens y coste estimado del turno que terminó.
    public var turnTokens: Int?
    public var turnCost: Double?
    /// Lo que ahorró la caché de prompts en la sesión; nil sin caché (Ollama) o sin tokens de caché.
    public var cache: CacheSavings? = nil
}

/// Respuesta de `alfalfa --buscar-actualizacion --json` y `--actualizar --json`.
public struct UpdateInfo: Equatable, Sendable {
    public var installed: String
    public var latest: String?
    public var available: Bool
    public var updated: Bool
    public var macAppChanged: Bool
    public var text: String
    /// «app» dentro de AlfalfaCode.app (se actualiza con un DMG nuevo), «clon» o «paquete».
    public var installation: String? = nil
    /// Página del DMG nuevo, sólo para la app empaquetada.
    public var downloadURL: URL? = nil

    /// La app empaquetada no se cambia sola: la versión nueva se descarga.
    public var needsDownload: Bool { installation == "app" }
}

/// Un chat de la app de Ollama que se puede cargar (`--chats-ollama`): sólo título y recuentos.
public struct OllamaChat: Equatable, Identifiable, Sendable {
    public var id: String
    public var title: String
    public var messages: Int
    public var models: [String]
    public var date: Double

    public init(id: String, title: String, messages: Int, models: [String], date: Double) {
        self.id = id
        self.title = title
        self.messages = messages
        self.models = models
        self.date = date
    }

    public func matches(_ search: String) -> Bool {
        search.isEmpty || title.localizedStandardContains(search) || models.contains { $0.localizedStandardContains(search) }
    }
}

public struct ModelInfo: Equatable, Identifiable, Sendable {
    /// El que guarda la sesión: `glm-5.3-flash:cloud`, `openai/gpt-6-luna`.
    public var name: String
    /// El visible en el selector («GPT-6 Luna - Alfalfa»).
    public var label: String
    /// `ollama`, `openai`, `alfalfa` o un compatible con OpenAI del catálogo (`openrouter`).
    public var provider: String
    /// Nombre visible del proveedor («OpenRouter»), para los compatibles del catálogo.
    public var providerName: String?
    /// Ventana de contexto: la del proveedor o, si no la da, la del catálogo de models.dev.
    public var context: Int?
    /// Hasta dónde llega la tarifa barata (`barato_hasta`): pasado eso, toda la petición cuesta más.
    public var cheapUntil: Int?
    /// Precio por millón de tokens, de quien cobra por token.
    public var price: ModelPrice?
    /// En «Gratis ahora»: coste 0, de nube y con herramientas.
    public var free: Bool
    /// El aviso de datos que acompaña a un modelo gratis.
    public var notice: String?
    /// La clave que falta para usarlo, si su proveedor no la tiene.
    public var missingKey: MissingKey?
    /// Si ve imágenes: true, false o nil cuando no se sabe (entonces se deja adjuntar).
    public var vision: Bool?
    public var id: String { name }

    public init(name: String, label: String? = nil, provider: String = "ollama", providerName: String? = nil,
                context: Int? = nil, price: ModelPrice? = nil, free: Bool = false, notice: String? = nil,
                missingKey: MissingKey? = nil, vision: Bool? = nil, cheapUntil: Int? = nil) {
        self.cheapUntil = cheapUntil
        self.name = name
        self.label = label ?? name
        self.provider = provider
        self.providerName = providerName
        self.context = context
        self.price = price
        self.free = free
        self.notice = notice
        self.missingKey = missingKey
        self.vision = vision
    }
}

/// Un proveedor tal como lo cuenta `alfalfa --proveedores --json`, sin secretos.
public struct ProviderStatus: Equatable, Identifiable, Sendable {
    public var id: String
    public var name: String
    public var configured: Bool
    public var url: String?
    public var hasKey: Bool
    /// Compatible con OpenAI del catálogo (OpenRouter, OpenCode Zen…): se configura con una clave.
    public var compatible: Bool
    /// Nombre de su clave (`OPENROUTER_API_KEY`) y dónde se consigue.
    public var variable: String?
    public var keyPage: String?
    /// Lo que conviene saber de sus condiciones («Los modelos gratis de Zen sólo desde OpenCode»).
    public var notice: String?

    public init(id: String, name: String, configured: Bool, url: String? = nil, hasKey: Bool = false,
                compatible: Bool = false, variable: String? = nil, keyPage: String? = nil, notice: String? = nil) {
        self.id = id
        self.name = name
        self.configured = configured
        self.url = url
        self.hasKey = hasKey
        self.compatible = compatible
        self.variable = variable
        self.keyPage = keyPage
        self.notice = notice
    }
}

/// La primera vez sin proveedor (`alfalfa --primera-vez --json`): los gratis ahora para empezar.
public struct FirstRun: Equatable, Sendable {
    /// Ningún proveedor configurado: ni claves, ni proveedor elegido, ni Ollama en este Mac.
    public var needed: Bool
    /// «Gratis: puede usar tus datos para entrenar».
    public var notice: String
    public var providers: [FirstRunProvider]

    public init(needed: Bool, notice: String, providers: [FirstRunProvider]) {
        self.needed = needed
        self.notice = notice
        self.providers = providers
    }
}

/// Un proveedor con modelos gratis ahora, para la primera vez.
public struct FirstRunProvider: Equatable, Identifiable, Sendable {
    public var id: String
    public var name: String
    /// Se conecta en el navegador (OAuth), sin pegar la clave: OpenRouter.
    public var connects: Bool
    /// Dónde se saca su clave, si no se conecta así.
    public var keyPage: String?
    /// El modelo gratis que se elige con él (`openrouter/…`).
    public var recommended: String?
    public var models: [String]
    public var labels: [String]

    public init(id: String, name: String, connects: Bool, keyPage: String? = nil, recommended: String? = nil,
                models: [String] = [], labels: [String] = []) {
        self.id = id
        self.name = name
        self.connects = connects
        self.keyPage = keyPage
        self.recommended = recommended
        self.models = models
        self.labels = labels
    }
}

/// El buscador web de los agentes, como lo cuenta `alfalfa --buscador --json`, sin secretos.
public struct WebSearchSettings: Equatable, Sendable {
    /// Los modos que entiende `alfalfa --buscador`, en el orden del selector.
    public static let modes = ["auto", "ollama", "searxng", "apagado"]

    public var mode: String
    public var searxngURL: String
    /// Un integrador usa un servicio CuySearch remoto (`ALFALFA_CUYSEARCH_URL`).
    public var remote: Bool
    /// Está instalada la librería cuysearch en el entorno de AlfalfaCode.
    public var library: Bool
    public var hasOllamaKey: Bool

    public init(mode: String, searxngURL: String = "http://127.0.0.1:8888", remote: Bool = false,
                library: Bool = true, hasOllamaKey: Bool = false) {
        self.mode = mode
        self.searxngURL = searxngURL
        self.remote = remote
        self.library = library
        self.hasOllamaKey = hasOllamaKey
    }

    /// Falta la librería y hace falta: la app la instala sola al arrancar.
    public var needsInstall: Bool { !library && !remote && mode != "apagado" }

    /// El nombre de cada modo en Ajustes.
    public static func label(of mode: String) -> String {
        switch mode {
        case "auto": return "Automático"
        case "ollama": return "Ollama Web Search"
        case "searxng": return "SearXNG"
        case "apagado": return "Apagado"
        default: return mode
        }
    }
}

/// Salidas del proceso hijo. Los `tipo` desconocidos no producen ningún caso.
public enum AlfalfaMessage: Equatable, Sendable {
    case session(SessionInfo)
    case event(AgentEvent)
    case permission(PermissionRequest)
    /// `session`: se aprobó también para el resto de la sesión.
    case permissionResolved(id: String, approved: Bool, reason: String, session: Bool = false)
    /// `mcp` sólo llega de sesiones con servidores MCP; los clientes v2 anteriores lo ignoran.
    case context(tokens: Int, limit: Int, mcp: McpContextUsage? = nil, breakdown: ContextBreakdown? = nil,
                 details: ContextDetails? = nil)
    case error(String)
    /// `error` con `rechazado`: el mensaje no abrió turno (imágenes para un modelo que no las ve)
    /// y vuelve al cuadro de texto.
    case messageRejected(MessageRejection)
    case turnFinished(Outcome)
    case finished(Outcome)
    case sessions([SessionSummary])
    /// `alfalfa --historial ID --json`: la conversación guardada y el cierre de su último turno.
    case history(id: String, events: [AgentEvent], wrapUp: WrapUp?, context: SavedContext? = nil, tokens: Int = 0,
                 cost: Double? = nil)
    case models([ModelInfo])
    /// `--proveedores --json` y las órdenes que los cambian: el proveedor por defecto y el estado de cada uno.
    case providers(defaultProvider: String, providers: [ProviderStatus])
    /// `--buscador --json` y `--fijar-searxng`: el buscador web de los agentes.
    case webSearch(WebSearchSettings)
    /// `cuota`: el % usado de la cuota mensual de cada proveedor que la tiene.
    case quota([Quota])
    /// `--activar-codigo`: el código quedó atado a este Mac.
    case activated(name: String?, quotas: [Quota])
    /// `--primera-vez --json`.
    case firstRun(FirstRun)
    /// `--conectar`: la URL de autorización que se abrió en el navegador (por si no se abrió).
    case connecting(provider: String, url: String)
    /// `--conectar`: conectado, con el modelo gratis que quedó por defecto.
    case connected(provider: String, model: String?)
    /// `--reportar`: el reporte de error se envió, o por qué no.
    case reported(ReportResult)
    case ollamaChats([OllamaChat])
    /// `--importar --json`: lo que trae la carpeta de OpenCode, Codex y Claude Code, y su estado.
    case imported(ImportedConfig)
    case update(UpdateInfo)
    /// Respuesta a `--marcar`, `--eliminar-sesion` y `--solo-bifurcar`.
    case sessionChanged(id: String, forkedFrom: String?)
    /// «Seguir en una sesión nueva»: la sesión `id` sigue a `from` con su resumen; esta se cierra.
    case continued(id: String, from: String)
    /// `procesos`: las órdenes en segundo plano de la sesión, vivas o ya terminadas.
    case processes([BackgroundProcess])
    /// `subagentes`: los subagentes de la sesión y cómo van.
    case subagents([Subagent])
    /// `plan`: la lista de tareas del agente, entera cada vez.
    case plan(Plan)
    /// `subagente_evento`: un paso de un subagente; no entra en la conversación principal.
    case subagentEvent(agent: String, AgentEvent)
    /// `despierto`: el arnés tomó (true) o soltó (false) la aserción de macOS que impide que el Mac
    /// se duerma por inactividad mientras trabaja un turno.
    case keepingAwake(Bool)
    /// `presupuesto`: topes y gasto de la sesión y del mes, el aviso del 80 % y el corte, y la caché.
    case budget(BudgetStatus, cache: CacheSavings?)
    /// `navegador`: el agente pide algo al navegador propio de la app (BrowserEngine).
    case browser(BrowserRequest)
    /// `--volver … --vista-previa` y `--rehacer … --vista-previa`: qué se restauraría y qué no.
    case undoPreview(UndoPreview)
    /// `--volver` y `--rehacer`: el código se deshizo o se rehízo.
    case undone(UndoStep)

    /// Interpreta una línea. Devuelve nil si no es JSON, no es versión 2,
    /// el tipo se desconoce o faltan campos obligatorios.
    public static func decode(line: String) -> AlfalfaMessage? {
        guard let data = line.data(using: .utf8),
              let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              object["version"] as? Int == protocolVersion,
              let kind = object["tipo"] as? String else { return nil }
        switch kind {
        case "sesion":
            return sessionInfo(object).map(Self.session)
        case "evento":
            return agentEvent(object).map(Self.event)
        case "permiso":
            guard let id = object["id"] as? String, let argv = object["argv"] as? [String] else { return nil }
            let reason = (object["motivo"] as? String).flatMap(PermissionReason.init(rawValue:))
            var reasons = (object["motivos"] as? [String] ?? []).compactMap(PermissionReason.init(rawValue:))
            if let reason, reasons.first != reason {
                reasons.removeAll { $0 == reason }
                reasons.insert(reason, at: 0)
            }
            return .permission(PermissionRequest(id: id, argv: argv, purpose: object["proposito"] as? String ?? "",
                                                 reason: reason, reasons: reasons,
                                                 justification: text(object["justificacion"]), rule: text(object["regla"]),
                                                 retryOf: text(object["reintento_de"]), detail: text(object["sin_sandbox"]),
                                                 mcp: mcpCall(object["mcp"]), forSession: bool(object["para_sesion"]) ?? false,
                                                 app: appTarget(object["app"])))
        case "permiso_resuelto":
            guard let id = object["id"] as? String, let approved = bool(object["aprobado"]) else { return nil }
            return .permissionResolved(id: id, approved: approved, reason: object["motivo"] as? String ?? "",
                                       session: bool(object["sesion"]) ?? false)
        case "contexto":
            guard let tokens = object["tokens"] as? Int, let limit = object["limite"] as? Int else { return nil }
            return .context(tokens: tokens, limit: limit, mcp: mcpUsage(object), breakdown: breakdown(object["desglose"]),
                            details: details(object))
        case "error":
            if let rejected = object["rechazado"] as? [String: Any] {
                let alternatives = (object["con_vision"] as? [[String: Any]] ?? []).compactMap { row -> ModelChoice? in
                    guard let model = text(row["modelo"]) else { return nil }
                    return ModelChoice(model: model, label: text(row["nombre"]) ?? model)
                }
                return .messageRejected(MessageRejection(message: object["texto"] as? String ?? "",
                                                         reason: text(object["motivo"]),
                                                         text: rejected["texto"] as? String ?? "",
                                                         images: rejected["imagenes"] as? [String] ?? [],
                                                         alternatives: alternatives))
            }
            return .error(object["texto"] as? String ?? "")
        case "turno_terminado":
            return outcome(object).map { .turnFinished($0) }
        case "fin":
            return outcome(object).map { .finished($0) }
        case "historial":
            guard let id = object["sesion"] as? String, let rows = object["eventos"] as? [[String: Any]] else { return nil }
            let events = rows.compactMap(agentEvent)
            var saved: SavedContext?
            if let context = object["contexto"] as? [String: Any], let tokens = context["tokens"] as? Int,
               let limit = context["limite"] as? Int {
                saved = SavedContext(tokens: tokens, limit: limit, breakdown: breakdown(context["desglose"]),
                                     details: details(context))
            }
            return .history(id: id, events: events, wrapUp: wrapUp(object["cierre"]), context: saved,
                            tokens: object["tokens"] as? Int ?? 0, cost: amount(object["coste"]))
        case "sesiones":
            guard let rows = object["sesiones"] as? [[String: Any]] else { return nil }
            return .sessions(rows.compactMap(summary))
        case "modelos":
            guard let rows = object["modelos"] as? [[String: Any]] else { return nil }
            return .models(rows.compactMap { row in
                guard let name = row["name"] as? String else { return nil }
                return ModelInfo(name: name, label: text(row["nombre"]) ?? name,
                                 provider: text(row["proveedor"]) ?? "ollama",
                                 providerName: text(row["nombre_proveedor"]), context: integer(row["ventana"]),
                                 price: modelPrice(row["precio"]), free: bool(row["gratis"]) ?? false,
                                 notice: text(row["aviso"]), missingKey: missingKey(row["falta_clave"]),
                                 vision: bool(row["vision"]), cheapUntil: integer(row["barato_hasta"]))
            })
        case "proveedores":
            guard let rows = object["proveedores"] as? [[String: Any]] else { return nil }
            return .providers(defaultProvider: text(object["por_defecto"]) ?? "ollama", providers: rows.compactMap { row in
                guard let id = text(row["id"]) else { return nil }
                return ProviderStatus(id: id, name: text(row["nombre"]) ?? id,
                                      configured: row["configurado"] as? Bool ?? false,
                                      url: text(row["url"]), hasKey: row["clave"] as? Bool ?? false,
                                      compatible: bool(row["compatible"]) ?? false, variable: text(row["variable"]),
                                      keyPage: text(row["donde"]), notice: text(row["aviso"]))
            })
        case "buscador":
            return webSearch(object).map { .webSearch($0) }
        case "cuota":
            guard let rows = object["cuotas"] as? [[String: Any]] else { return nil }
            return .quota(quotas(rows))
        case "activado":
            return .activated(name: text(object["nombre"]), quotas: quotas(object["cuotas"] as? [[String: Any]] ?? []))
        case "primera_vez":
            let rows = object["proveedores"] as? [[String: Any]] ?? []
            return .firstRun(FirstRun(
                needed: bool(object["sin_proveedor"]) ?? false,
                notice: text(object["aviso"]) ?? "Gratis: puede usar tus datos para entrenar",
                providers: rows.compactMap { row in
                    guard let id = text(row["id"]) else { return nil }
                    let models = row["modelos"] as? [[String: Any]] ?? []
                    return FirstRunProvider(id: id, name: text(row["nombre"]) ?? id,
                                            connects: bool(row["conectar"]) ?? false, keyPage: text(row["donde"]),
                                            recommended: text(row["recomendado"]),
                                            models: models.compactMap { text($0["name"]) },
                                            labels: models.compactMap { text($0["nombre"]) ?? text($0["name"]) })
                }))
        case "conectando":
            guard let provider = text(object["proveedor"]), let url = text(object["url"]) else { return nil }
            return .connecting(provider: provider, url: url)
        case "conectado":
            guard let provider = text(object["proveedor"]) else { return nil }
            return .connected(provider: provider, model: text(object["modelo"]))
        case "importadas":
            return ImportedConfig(object).map(Self.imported)
        case "chats_ollama":
            guard let rows = object["chats"] as? [[String: Any]] else { return nil }
            return .ollamaChats(rows.compactMap { row in
                guard let id = row["id"] as? String, let title = row["titulo"] as? String else { return nil }
                return OllamaChat(id: id, title: title, messages: row["mensajes"] as? Int ?? 0,
                                  models: row["modelos"] as? [String] ?? [], date: number(row["fecha"]) ?? 0)
            })
        case "procesos":
            guard let rows = object["procesos"] as? [[String: Any]] else { return nil }
            return .processes(rows.compactMap { row in
                guard let id = text(row["id"]), let argv = row["argv"] as? [String] else { return nil }
                return BackgroundProcess(id: id, argv: argv, purpose: row["proposito"] as? String ?? "",
                                         since: number(row["desde"]) ?? 0, alive: bool(row["vivo"]) ?? false,
                                         exitCode: integer(row["codigo"]), unsandboxed: bool(row["sin_sandbox"]) ?? false,
                                         stopped: bool(row["detenido"]) ?? false,
                                         lastLines: (row["ultimas"] as? [String]) ?? [])
            })
        case "subagentes":
            guard let rows = object["subagentes"] as? [[String: Any]] else { return nil }
            return .subagents(rows.compactMap { row in
                guard let id = text(row["id"]) else { return nil }
                return Subagent(id: id, name: row["nombre"] as? String ?? id, type: row["tipo"] as? String ?? "general",
                                state: row["estado"] as? String ?? "trabajando", model: row["modelo"] as? String ?? "",
                                since: number(row["desde"]) ?? 0, until: number(row["hasta"]),
                                activity: row["actividad"] as? String ?? "", steps: integer(row["pasos"]) ?? 0,
                                report: row["informe"] as? String ?? "", error: row["error"] as? String ?? "")
            })
        case "plan":
            guard let rows = object["pasos"] as? [[String: Any]] else { return nil }
            let steps = rows.compactMap { row -> PlanStep? in
                guard let text = row["paso"] as? String, !text.isEmpty else { return nil }
                return PlanStep(text, PlanStep.Status(rawValue: row["estado"] as? String ?? "") ?? .pending)
            }
            return .plan(Plan(steps: steps, explanation: object["explicacion"] as? String))
        case "navegador":
            return BrowserRequest(object: object).map(AlfalfaMessage.browser)
        case "despierto":
            return bool(object["activo"]).map(Self.keepingAwake)
        case "presupuesto":
            return BudgetStatus(object: object).map { .budget($0, cache: CacheSavings(object: object["cache"])) }
        case "subagente_evento":
            guard let agent = text(object["agente"]), let role = object["rol"] as? String,
                  let content = object["texto"] as? String else { return nil }
            return .subagentEvent(agent: agent, AgentEvent(role: role, text: content, time: number(object["hora"]) ?? 0))
        case "deshacer_vista", "rehacer_vista":
            return UndoPreview.parse(object).map(Self.undoPreview)
        case "deshecho":
            return UndoStep.parse(object, text: object["texto"] as? String ?? "").map(Self.undone)
        case "sesion_cambiada":
            guard let id = text(object["id"]) else { return nil }
            if let from = text(object["continuada_de"]) { return .continued(id: id, from: from) }
            return .sessionChanged(id: id, forkedFrom: text(object["bifurcada_de"]))
        case "actualizacion":
            guard let installed = object["instalada"] as? String, let available = bool(object["hay_nueva"]) else { return nil }
            return .update(UpdateInfo(installed: installed, latest: text(object["ultima"]), available: available,
                                      updated: bool(object["actualizado"]) ?? false,
                                      macAppChanged: bool(object["app_mac_cambio"]) ?? false,
                                      text: object["texto"] as? String ?? "",
                                      installation: text(object["instalacion"]),
                                      downloadURL: text(object["descarga"]).flatMap(Self.httpsURL)))
        default:
            return ReportResult(kind: kind, object: object).map(Self.reported)
        }
    }

    /// `sesion`: id, carpeta, modelo, esfuerzo, modo de permisos y de contexto.
    private static func sessionInfo(_ object: [String: Any]) -> SessionInfo? {
        guard let id = object["id"] as? String, let cwd = object["cwd"] as? String else { return nil }
        return SessionInfo(id: id, cwd: cwd, model: object["modelo"] as? String ?? "",
                           effort: object["esfuerzo"] as? String ?? "", mode: object["modo"] as? String ?? "",
                           contextMode: (object["contexto"] as? String).flatMap(ContextMode.init(rawValue:)))
    }

    /// Un `evento` en vivo o una fila de `historial`, con su compactación o su aviso de tarifa.
    private static func agentEvent(_ row: [String: Any]) -> AgentEvent? {
        guard let role = row["rol"] as? String, let text = row["texto"] as? String else { return nil }
        return AgentEvent(role: role, text: text, time: number(row["hora"]) ?? 0, turn: Self.text(row["turno"]),
                          priceWarning: PriceWarning.parse(row["tarifa"], text: text),
                          compaction: compaction(row["compactacion"]), tool: ToolInfo.parse(row["herramienta"]),
                          undo: UndoStep.parse(row["deshacer"], text: text))
    }

    /// Sólo los booleanos JSON: NSNumber también convertiría 0 y 1.
    private static func bool(_ value: Any?) -> Bool? {
        guard let number = value as? NSNumber, CFGetTypeID(number) == CFBooleanGetTypeID() else { return nil }
        return number.boolValue
    }

    /// `--buscador --json`: nil si el modo no es uno de los conocidos.
    private static func webSearch(_ object: [String: Any]) -> WebSearchSettings? {
        guard let mode = text(object["modo"]), WebSearchSettings.modes.contains(mode) else { return nil }
        return WebSearchSettings(mode: mode, searxngURL: text(object["searxng_url"]) ?? "http://127.0.0.1:8888",
                                 remote: bool(object["remoto"]) ?? false, library: bool(object["libreria"]) ?? true,
                                 hasOllamaKey: bool(object["ollama_clave"]) ?? false)
    }

    private static func number(_ value: Any?) -> Double? { (value as? NSNumber)?.doubleValue }

    private static func quotas(_ rows: [[String: Any]]) -> [Quota] {
        rows.compactMap(Quota.init(row:))
    }

    /// Texto opcional no vacío; cualquier otro tipo se ignora.
    /// Sólo un enlace https: la app lo abre en el navegador.
    private static func httpsURL(_ value: String) -> URL? {
        guard let url = URL(string: value), url.scheme == "https", url.host != nil else { return nil }
        return url
    }

    private static func text(_ value: Any?) -> String? {
        guard let string = value as? String, !string.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return string
    }

    /// Un entero JSON que no sea booleano.
    private static func integer(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
              let int = value as? Int else { return nil }
        return int
    }

    private static func appTarget(_ value: Any?) -> AppTarget? {
        guard let object = value as? [String: Any], let name = object["nombre"] as? String, !name.isEmpty else { return nil }
        return AppTarget(name: name, bundle: text(object["bundle"]), action: text(object["accion"]),
                         level: text(object["nivel"]))
    }

    private static func mcpCall(_ value: Any?) -> McpCall? {
        guard let object = value as? [String: Any], let server = text(object["servidor"]),
              let tool = text(object["herramienta"]), let name = text(object["nombre"]) else { return nil }
        let raw = object["argumentos"] as? [String: Any] ?? [:]
        let arguments = raw.keys.sorted().map { key in
            McpArgument(key: key, value: json(raw[key] as Any))
        }
        return McpCall(server: server, tool: tool, name: name, arguments: arguments,
                       callID: text(object["llamada"]) ?? "")
    }

    private static func json(_ value: Any) -> String {
        if let string = value as? String { return string }
        guard JSONSerialization.isValidJSONObject([value]),
              let data = try? JSONSerialization.data(withJSONObject: [value], options: [.sortedKeys, .withoutEscapingSlashes]) else {
            return String(describing: value)
        }
        // Se serializa dentro de un array para admitir números y booleanos sueltos.
        return String(String(decoding: data, as: UTF8.self).dropFirst().dropLast())
    }

    private static func mcpUsage(_ object: [String: Any]) -> McpContextUsage? {
        guard let loaded = integer(object["herramientas_mcp"]) else { return nil }
        return McpContextUsage(loadedTokens: loaded, loadedCount: integer(object["herramientas_mcp_cargadas"]) ?? 0,
                               baseTokens: integer(object["herramientas_mcp_base"]) ?? 0,
                               method: object["herramientas_mcp_metodo"] as? String ?? "",
                               exact: bool(object["herramientas_mcp_exacto"]) ?? false,
                               tools: entries(object["herramientas_mcp_lista"], name: "nombre"),
                               deferredCount: integer(object["herramientas_mcp_diferidas"]) ?? 0,
                               deferredTokens: integer(object["herramientas_mcp_diferidas_tokens"]) ?? 0)
    }

    /// Filas `{nombre|ruta, tokens}`; las incompletas se descartan.
    private static func entries(_ value: Any?, name key: String) -> [ContextEntry] {
        (value as? [[String: Any]] ?? []).compactMap { row in
            guard let name = text(row[key]), let tokens = integer(row["tokens"]) else { return nil }
            return ContextEntry(name: name, tokens: max(0, tokens))
        }
    }

    private static func details(_ object: [String: Any]) -> ContextDetails? {
        let estimate = integer(object["estimado"]).map { max(0, $0) }
        let threshold = integer(object["umbral"]).flatMap { $0 > 0 ? $0 : nil }
        let memory = entries(object["memoria"], name: "ruta")
        let tier = PriceTier.parse(object["tarifa"])
        guard estimate != nil || threshold != nil || object["memoria"] != nil || tier != nil else { return nil }
        return ContextDetails(estimate: estimate, threshold: threshold, memoryFiles: memory, priceTier: tier)
    }

    private static func compaction(_ value: Any?) -> Compaction? {
        guard let object = value as? [String: Any], let raw = object["estado"] as? String,
              let state = Compaction.State(rawValue: raw) else { return nil }
        return Compaction(state: state, reason: object["motivo"] as? String ?? "auto",
                          before: integer(object["antes"]) ?? 0, after: integer(object["despues"]) ?? 0,
                          summary: object["resumen"] as? String ?? "", instructions: text(object["instrucciones"]))
    }

    private static func outcome(_ object: [String: Any]) -> Outcome? {
        guard let state = object["estado"] as? String else { return nil }
        return Outcome(state: state, summary: object["resumen"] as? String ?? "",
                       exitCode: object["codigo_salida"] as? Int ?? 1, tokens: object["tokens"] as? Int ?? 0,
                       wrapUp: wrapUp(object["cierre"]), turn: text(object["turno"]), cost: amount(object["coste"]),
                       turnTokens: integer(object["tokens_turno"]), turnCost: amount(object["coste_turno"]),
                       cache: CacheSavings(object: object["cache"]))
    }

    /// Un importe en USD no negativo y finito; nil si no llega o no lo es.
    private static func amount(_ value: Any?) -> Double? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        let double = number.doubleValue
        return double.isFinite && double >= 0 ? double : nil
    }

    private static func modelPrice(_ value: Any?) -> ModelPrice? {
        guard let object = value as? [String: Any], let input = amount(object["entrada"]),
              let output = amount(object["salida"]) else { return nil }
        return ModelPrice(input: input, output: output)
    }

    private static func missingKey(_ value: Any?) -> MissingKey? {
        guard let object = value as? [String: Any], let variable = text(object["variable"]) else { return nil }
        return MissingKey(variable: variable, url: text(object["donde"]),
                          text: text(object["texto"]) ?? "Falta la clave \(variable).")
    }

    private static func breakdown(_ value: Any?) -> ContextBreakdown? {
        guard let object = value as? [String: Any] else { return nil }
        func part(_ key: String) -> Int { max(0, object[key] as? Int ?? 0) }
        return ContextBreakdown(system: part("sistema"), tools: part("herramientas"), mcp: part("mcp"),
                                messages: part("mensajes"), memory: part("memoria"),
                                mcpInstructions: part("instrucciones_mcp"), plugins: part("complementos"))
    }

    private static func wrapUp(_ value: Any?) -> WrapUp? { WrapUp(cierre: value) }

    private static func summary(_ row: [String: Any]) -> SessionSummary? {
        guard let id = row["id"] as? String, let state = row["estado"] as? String else { return nil }
        return SessionSummary(id: id, task: row["encargo"] as? String, cwd: row["cwd"] as? String, state: state,
                              model: row["modelo"] as? String, effort: row["esfuerzo"] as? String,
                              date: number(row["fecha"]) ?? 0, tokens: row["tokens"] as? Int ?? 0,
                              title: text(row["titulo"]), pinned: bool(row["fijada"]) ?? false,
                              archived: bool(row["archivada"]) ?? false, mode: text(row["modo"]),
                              parent: text(row["padre"]), cost: amount(row["coste"]))
    }
}

/// Entradas hacia el proceso hijo: una línea JSON por mensaje.
public enum AlfalfaCommand: Equatable, Sendable {
    /// `session`: aprobar también lo mismo durante el resto de la sesión.
    case permission(id: String, approved: Bool, session: Bool = false)
    case message(String, images: [String] = [])
    case cancel
    /// «Enviar ahora» de un mensaje en cola: corta el turno y el mensaje abre el siguiente.
    case sendNow(String)
    /// «Redirigir» (steer de Codex): el modelo lee ya los mensajes en cola, sin cortar el turno.
    case redirect
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
    /// Ajustes ▸ General ▸ Presupuesto: los topes en dólares (nil, sin tope). Vale ya, también a mitad de turno.
    case budget(session: Double?, month: Double?)
    /// La respuesta del navegador propio a una petición `navegador`.
    case browserReply(BrowserReply)

    public var line: String {
        let object: [String: Any]
        switch self {
        case let .permission(id, approved, session):
            object = approved && session ? ["tipo": "permiso", "id": id, "aprobado": true, "sesion": true]
                : ["tipo": "permiso", "id": id, "aprobado": approved]
        case let .message(text, images):
            object = images.isEmpty ? ["tipo": "mensaje", "texto": text]
                : ["tipo": "mensaje", "texto": text, "imagenes": images]
        case .cancel: object = ["tipo": "cancelar"]
        case let .sendNow(text): object = ["tipo": "enviar_ahora", "texto": text]
        case .redirect: object = ["tipo": "redirigir"]
        case let .contextMode(mode): object = ["tipo": "contexto", "modo": mode.rawValue]
        case .continueInNewSession: object = ["tipo": "continuar_en_nueva"]
        case let .stopProcess(id): object = ["tipo": "detener_proceso", "id": id]
        case let .keepAwake(on): object = ["tipo": "mantener_despierto", "activo": on]
        case let .budget(session, month):
            object = ["tipo": "presupuesto", "sesion": session.map { $0 as Any } ?? NSNull(),
                      "mes": month.map { $0 as Any } ?? NSNull()]
        case let .browserReply(reply): object = reply.object
        case let .compact(instructions):
            object = instructions.map { ["tipo": "compactar", "instrucciones": $0] } ?? ["tipo": "compactar"]
        case let .settings(model, effort, mode):
            var fields: [String: Any] = ["tipo": "ajustes"]
            if let model { fields["modelo"] = model }
            if let effort { fields["esfuerzo"] = effort }
            if let mode { fields["modo"] = mode }
            object = fields
        }
        let data = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes])) ?? Data()
        return String(decoding: data, as: UTF8.self) + "\n"
    }
}
