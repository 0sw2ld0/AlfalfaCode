import Foundation

/// Modos de permisos, como los de Claude Code (alfalfa/permisos/modos.py).
public enum PermissionMode: String, CaseIterable, Sendable {
    case auto, manual, ediciones, plan, omitir

    public var title: String {
        switch self {
        case .auto: return "Auto"
        case .manual: return "Manual"
        case .ediciones: return "Aceptar ediciones"
        case .plan: return "Plan"
        case .omitir: return "Omitir permisos"
        }
    }

    public var summary: String {
        switch self {
        case .auto: return "Las órdenes normales corren en el sandbox; pregunta las peligrosas"
        case .manual: return "Pregunta antes de cada cambio: órdenes y ediciones"
        case .ediciones: return "Edita sin preguntar; pregunta antes de cada orden"
        case .plan: return "Sólo lee y propone un plan antes de cambiar nada"
        case .omitir: return "Aprueba todo sin preguntar, también fuera del sandbox"
        }
    }

    public var symbol: String {
        switch self {
        case .auto: return "sparkles"
        case .manual: return "hand.raised"
        case .ediciones: return "pencil.and.list.clipboard"
        case .plan: return "list.bullet.clipboard"
        case .omitir: return "exclamationmark.shield"
        }
    }

    /// Omitir permisos se confirma antes de activarlo.
    public var needsConfirmation: Bool { self == .omitir }

    public static func named(_ value: String?) -> PermissionMode {
        value.flatMap { PermissionMode(rawValue: $0) } ?? .auto
    }
}

/// Órdenes del compositor que empiezan por «/», como las de Codex
/// (codex-rs/tui/src/slash_command.rs): cada una tiene descripción, si admite
/// argumento y si vale con un turno en marcha. También se aceptan sus nombres
/// en inglés, para quien venga de Codex o Claude Code.
public enum SlashCommand: String, CaseIterable, Sendable {
    case nuevo, modelo, esfuerzo, modo, compactar, estado, revisar, inicializar
    case renombrar, fijar, bifurcar, archivar, copiar, exportar, detener
    case procesos, cerrarProcesos = "cerrar-procesos"

    public var name: String { rawValue }

    public var aliases: [String] {
        switch self {
        case .nuevo: return ["new", "clear"]
        case .modelo: return ["model"]
        case .esfuerzo: return ["effort"]
        case .modo: return ["mode", "permissions"]
        case .compactar: return ["compact"]
        case .estado: return ["status"]
        case .revisar: return ["review"]
        case .inicializar: return ["init"]
        case .renombrar: return ["rename"]
        case .fijar: return ["pin"]
        case .bifurcar: return ["fork"]
        case .archivar: return ["archive"]
        case .copiar: return ["copy"]
        case .exportar: return ["export"]
        case .detener: return ["stop"]
        case .procesos: return ["ps"]
        case .cerrarProcesos: return ["clean"]
        }
    }

    public var summary: String {
        switch self {
        case .nuevo: return "Empieza un chat nuevo en la misma carpeta"
        case .modelo: return "Elige el modelo para los siguientes turnos"
        case .esfuerzo: return "Elige el esfuerzo para los siguientes turnos"
        case .modo: return "Elige qué puede hacer el agente sin preguntarte"
        case .compactar: return "Resume la conversación para liberar contexto; escribe detrás qué conservar"
        case .estado: return "Muestra el id, el modelo, el contexto y los tokens de la sesión"
        case .revisar: return "Revisa los cambios sin commit y busca fallos"
        case .inicializar: return "Crea un AGENTS.md con instrucciones para el agente"
        case .renombrar: return "Cambia el título de este chat"
        case .fijar: return "Mantén este chat arriba en la barra lateral"
        case .bifurcar: return "Copia este chat en una sesión nueva para probar otro camino"
        case .archivar: return "Oculta este chat de la barra lateral"
        case .copiar: return "Copia la última respuesta del agente"
        case .exportar: return "Guarda la conversación como Markdown"
        case .detener: return "Detiene el turno en marcha"
        case .procesos: return "Lista los juegos, apps y servidores que siguen abiertos"
        case .cerrarProcesos: return "Cierra todas las órdenes en segundo plano"
        }
    }

    /// Símbolo de SF Symbols para el menú.
    public var symbol: String {
        switch self {
        case .nuevo: return "plus.bubble"
        case .modelo: return "cpu"
        case .esfuerzo: return "gauge.with.dots.needle.50percent"
        case .modo: return "shield.lefthalf.filled"
        case .compactar: return "arrow.down.right.and.arrow.up.left"
        case .estado: return "info.circle"
        case .revisar: return "checklist"
        case .inicializar: return "doc.badge.plus"
        case .renombrar: return "pencil"
        case .fijar: return "pin"
        case .bifurcar: return "arrow.triangle.branch"
        case .archivar: return "archivebox"
        case .copiar: return "doc.on.doc"
        case .exportar: return "square.and.arrow.up"
        case .detener: return "stop.circle"
        case .procesos: return "terminal"
        case .cerrarProcesos: return "xmark.circle"
        }
    }

    /// Admite texto detrás: `/modelo glm-5.3-flash:cloud`, `/renombrar Nuevo título`.
    public var takesArgument: Bool { [.modelo, .esfuerzo, .modo, .renombrar].contains(self) }

    /// Como `available_during_task` de Codex: lo que cambia el hilo espera a que acabe el turno.
    public var availableDuringTurn: Bool {
        switch self {
        case .revisar, .inicializar, .bifurcar: return false
        default: return true
        }
    }

    /// Necesita el proceso vivo (una sesión dormida aún no lo tiene). Compactar abre la
    /// sesión guardada sin turno, así que vale también dormida.
    public var needsProcess: Bool { self == .detener || self == .cerrarProcesos }

    public func available(running: Bool, dormant: Bool) -> Bool {
        if self == .detener { return running }
        if running && !availableDuringTurn { return false }
        if dormant && needsProcess { return false }
        return true
    }

    /// Texto que se manda al agente para las órdenes que son un encargo.
    public var prompt: String? {
        switch self {
        case .revisar: return Self.reviewPrompt
        case .inicializar: return Self.initPrompt
        default: return nil
        }
    }

    /// Lo que hay escrito si el compositor está en modo orden: `/`, el nombre y, si lo hay, el argumento.
    public struct Input: Equatable, Sendable {
        public var name: String
        public var argument: String?
    }

    public static func input(_ text: String) -> Input? {
        guard text.hasPrefix("/"), !text.contains("\n") else { return nil }
        let body = text.dropFirst()
        guard let space = body.firstIndex(of: " ") else { return Input(name: String(body), argument: nil) }
        return Input(name: String(body[..<space]), argument: String(body[body.index(after: space)...]))
    }

    /// La orden de un nombre exacto (o su alias).
    public static func named(_ name: String) -> SlashCommand? {
        let wanted = name.lowercased()
        return allCases.first { $0.name == wanted || $0.aliases.contains(wanted) }
    }

    /// Órdenes que encajan con lo escrito tras «/», como el menú de Codex: primero la
    /// coincidencia exacta y luego las que empiezan igual, en el orden de presentación.
    public static func matching(_ prefix: String) -> [SlashCommand] {
        let wanted = prefix.lowercased()
        if wanted.isEmpty { return allCases }
        let exact = allCases.filter { $0.name == wanted || $0.aliases.contains(wanted) }
        let starts = allCases.filter { command in
            !exact.contains(command)
                && (command.name.hasPrefix(wanted) || command.aliases.contains { $0.hasPrefix(wanted) })
        }
        return exact + starts
    }

    /// Adaptado de `prompt_for_init_command.md` de Codex.
    static let initPrompt = """
    Crea un archivo AGENTS.md que sirva de guía para quien trabaje en este repositorio, persona o agente.
    Antes de escribirlo, comprueba si ya existe AGENTS.md en la carpeta de trabajo; si existe, no lo sobrescribas ni lo cambies.
    El documento tiene que ser claro, breve y ordenado, con títulos descriptivos y explicaciones accionables.
    Sigue este esquema y adáptalo: añade secciones si hacen falta y quita las que no apliquen.

    Requisitos:
    - Título: «Guía del repositorio».
    - Títulos Markdown (#, ##) para la estructura.
    - Breve: entre 200 y 400 palabras.
    - Explicaciones cortas, directas y propias de este repositorio, con ejemplos (órdenes, rutas, nombres).

    Secciones recomendadas:
    - Estructura del proyecto: dónde están el código, las pruebas y los recursos.
    - Órdenes para compilar, probar y ejecutar en local, con lo que hace cada una.
    - Estilo de código y nombres: sangría, convenciones y herramientas de formato o lint.
    - Pruebas: marcos, convenciones de nombres y cómo ejecutarlas.
    - Commits y pull requests: las convenciones que se ven en el historial de git.
    """

    static let reviewPrompt = """
    Revisa los cambios sin commit de este repositorio (git status, git diff y los archivos nuevos) como un revisor de código.
    No edites archivos. Busca fallos reales: errores de lógica, casos sin cubrir, regresiones, problemas de seguridad y pruebas que falten.
    Para cada hallazgo da el archivo y la línea, qué falla, en qué caso concreto, y cómo arreglarlo. Ordénalos de más a menos grave.
    Si no encuentras nada importante, dilo así, sin inventar hallazgos.
    """
}
