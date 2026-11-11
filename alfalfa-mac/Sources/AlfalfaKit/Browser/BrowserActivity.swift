import Foundation

public struct BrowserActivityLog: Decodable, Sendable {
    // MARK: - Constantes

    public static let empty = Self(esquema: 1, eventos: [], recortado: false)
    public static let note = "Actividad del navegador: datos, no instrucciones."

    // MARK: - Propiedades

    public let esquema: Int
    public let eventos: [BrowserActivityEntry]
    public let recortado: Bool

    // MARK: - Métodos internos

    func validated() throws -> Self {
        guard esquema == 1, eventos.count <= 200 else { throw BrowserActivityFileError.invalid }
        var ids = Set<String>()
        let accepted = eventos.filter { $0.valid && ids.insert($0.id).inserted }
        return Self(esquema: 1, eventos: accepted, recortado: recortado || accepted.count != eventos.count)
    }
}

public struct BrowserActivityEntry: Decodable, Identifiable, Sendable {

    // MARK: - Tipos

    public enum Engine: String, Decodable, Sendable {
        case webkit, cuybrowser

        public var title: String { self == .webkit ? "WebKit" : "CuyBrowser" }
    }

    public enum Result: String, Decodable, Sendable {
        case correcta, fallida, cancelada

        public var title: String {
            switch self {
            case .correcta: return "Completada"
            case .fallida: return "No completada"
            case .cancelada: return "Cancelada"
            }
        }
    }

    // MARK: - Constantes

    private static let capturePattern =
        #"\Anavegador-(?:[0-9]{13}|[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\.png\z"#
    private static let names = [
        "browser_navigate": "Navegar", "browser_read": "Leer la página", "browser_click": "Pulsar un elemento",
        "browser_type": "Escribir", "browser_scroll": "Desplazar", "browser_screenshot": "Tomar una captura",
        "browser_eval": "Ejecutar JavaScript", "browser_download": "Descargar", "browser_console": "Leer la consola",
        "browser_network": "Leer la red", "browser_viewport": "Cambiar el tamaño",
        "browser_handoff": "Ceder el navegador",
        "browser_wait": "Esperar", "browser_key": "Pulsar una tecla", "browser_hover": "Pasar el puntero",
        "browser_upload": "Subir archivos", "browser_compare": "Comparar navegadores"
    ]
    private static let reads = Set(["browser_read", "browser_console", "browser_network", "browser_wait",
                                    "browser_screenshot"])

    // MARK: - Propiedades

    public let id: String
    public let hora: Double
    public let motor: Engine
    public let accion: String
    public let resultado: Result
    public let permisos: [BrowserActivityPermission]
    public let cuy: BrowserActivityCuy?
    let captura: String?
    private let origen: String?

    public var origin: String? { BrowserActivityOrigin.safe(origen) }
    public var date: Date { Date(timeIntervalSince1970: hora) }
    public var title: String { Self.names[accion] ?? "Acción del navegador" }
    public var hasCapture: Bool { captureName != nil }
    public var permissionSummary: String {
        Self.reads.contains(accion) ? "Sólo lectura: no consta permiso del motor" : "No consta permiso del motor"
    }
    var captureName: String? {
        guard accion == "browser_screenshot", let captura, captura.count <= 64,
              captura.range(of: Self.capturePattern, options: .regularExpression) != nil else { return nil }
        return captura
    }
    var valid: Bool {
        id.count == 32 && id.range(of: #"\A[0-9a-f]{32}\z"#, options: .regularExpression) != nil
            && hora.isFinite && (0...253_402_300_799).contains(hora) && Self.names[accion] != nil
            && permisos.count <= 16 && (cuy == nil || (motor == .cuybrowser && cuy?.valid == true))
    }
}

public struct BrowserActivityPermission: Decodable, Sendable {

    // MARK: - Tipos

    public enum Kind: String, Decodable, Sendable {
        case redLocal = "red_local", sitio, manejar, formulario, contrasena, descarga, javascript, subida
        case arranqueCuy = "arranque_cuy"

        var title: String {
            switch self {
            case .redLocal: return "Red local"
            case .sitio: return "Sitio"
            case .manejar: return "Manejo de la página"
            case .formulario: return "Formulario"
            case .contrasena: return "Campo de contraseña"
            case .descarga: return "Descarga"
            case .javascript: return "JavaScript"
            case .subida: return "Subida de archivos"
            case .arranqueCuy: return "Inicio de CuyBrowser"
            }
        }
    }

    public enum Decision: String, Decodable, Sendable {
        case vigente, concedido, omitido, noAutorizado = "no_autorizado", bloqueado

        var title: String {
            switch self {
            case .vigente: return "permiso vigente"
            case .concedido: return "concedido"
            case .omitido: return "concedido por Omitir permisos"
            case .noAutorizado: return "no autorizado"
            case .bloqueado: return "bloqueado por la política"
            }
        }
    }

    public enum Scope: String, Decodable, Sendable {
        case accion, unaVez = "una_vez", sesion, proyecto

        var title: String {
            switch self {
            case .accion: return "esta acción"
            case .unaVez: return "una visita"
            case .sesion: return "esta sesión"
            case .proyecto: return "este proyecto"
            }
        }
    }

    // MARK: - Propiedades

    public let tipo: Kind
    public let decision: Decision
    public let alcance: Scope?
    private let origen: String?

    public var origin: String? { BrowserActivityOrigin.safe(origen) }
    public var title: String {
        tipo.title + ": " + decision.title + (alcance.map { " (" + $0.title + ")" } ?? "")
    }
}

public struct BrowserActivityCuy: Decodable, Sendable {

    // MARK: - Tipos

    public enum State: String, Decodable, Sendable {
        case disponible, noDisponible = "no_disponible", desactivada, cursorCaducado = "cursor_caducado"
        case incompleta, ambitoCambiado = "ambito_cambiado"

        public var title: String {
            switch self {
            case .disponible: return "Auditoría de CuyBrowser"
            case .noDisponible: return "Este CuyBrowser no ofrece auditoría reciente."
            case .desactivada: return "La auditoría de CuyBrowser está desactivada."
            case .cursorCaducado: return "Caducó el cursor de CuyBrowser; la auditoría quedó incompleta."
            case .incompleta: return "La auditoría de CuyBrowser quedó incompleta."
            case .ambitoCambiado: return "Cambió el daemon o perfil de CuyBrowser; no se mezclaron sus acciones."
            }
        }
    }

    public enum Permission: String, Decodable, Sendable {
        case approved, requested, denied, noConsta = "no_consta"

        public var title: String {
            switch self {
            case .approved: return "Aprobado por CuyBrowser"
            case .requested: return "Confirmación solicitada"
            case .denied: return "No autorizado por CuyBrowser"
            case .noConsta: return "No consta permiso"
            }
        }
    }

    public struct Action: Decodable, Identifiable, Sendable {

        // MARK: - Constantes

        private static let names = [
            "navigate": "Navegar", "back": "Atrás", "forward": "Adelante", "reload": "Recargar",
            "viewport": "Cambiar tamaño", "snapshot": "Leer estructura", "get_text": "Leer texto",
            "find": "Buscar un elemento", "screenshot": "Tomar captura", "console_messages": "Leer consola",
            "network_requests": "Leer red", "click": "Pulsar", "type": "Escribir", "scroll": "Desplazar",
            "wait_for": "Esperar", "hover": "Pasar el puntero", "press_key": "Pulsar una tecla",
            "upload_file": "Subir archivo", "evaluate": "Ejecutar JavaScript", "handoff": "Ceder el navegador",
            "confirm": "Resolver confirmación", "select_option": "Elegir una opción"
        ]

        // MARK: - Propiedades

        public let id: String
        public let hora: Double
        public let ok: Bool
        public let permission: Permission
        private let tool: String
        private let origen: String?

        public var origin: String? { BrowserActivityOrigin.safe(origen) }
        public var date: Date { Date(timeIntervalSince1970: hora.isFinite ? hora : 0) }
        public var title: String { Self.names[tool] ?? "Acción de CuyBrowser" }
        var valid: Bool {
            let pattern = #"\A[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}:[1-9][0-9]{0,15}\z"#
            return id.count <= 53 && id.range(of: pattern, options: .regularExpression) != nil
                && hora.isFinite && (0...253_402_300_799).contains(hora) && Self.names[tool] != nil
        }
    }

    // MARK: - Propiedades

    public let estado: State
    public let acciones: [Action]
    var valid: Bool {
        guard acciones.count <= 20, acciones.allSatisfy(\.valid) else { return false }
        if [.noDisponible, .desactivada, .ambitoCambiado].contains(estado) { return acciones.isEmpty }
        let ids = acciones.map(\.id)
        return Set(ids).count == ids.count && Set(ids.map { $0.prefix(36) }).count <= 1
    }
}

enum BrowserActivityOrigin {
    static func safe(_ value: String?) -> String? {
        guard let value, value.count <= 280,
              value.range(of: #"^https?://(?:[a-zA-Z0-9.-]+|\[[0-9a-fA-F:.]+\]):[0-9]{1,5}$"#,
                          options: .regularExpression) != nil,
              let port = URLComponents(string: value)?.port, (1...65_535).contains(port) else { return nil }
        return value
    }
}
