import Foundation

/// Cómo arranca una pestaña de la terminal integrada: la shell de inicio del usuario, en la
/// carpeta de la sesión y con un entorno limpio. No hereda el entorno de la app: ahí pueden
/// ir claves de proveedores (OPENAI_API_KEY, OLLAMA_API_KEY…) y variables del arnés que no
/// tienen por qué llegar a cada orden que se escriba. La shell de inicio pone el PATH.
public struct TerminalLaunch: Equatable {

    // MARK: - Constantes

    /// Variables del entorno de la app que sí pasan: quién es el usuario, dónde vive y su
    /// agente de SSH (sin él, `ssh` y `git` piden la frase de la llave cada vez).
    static let inherited = ["HOME", "USER", "LOGNAME", "TMPDIR", "SSH_AUTH_SOCK", "__CF_USER_TEXT_ENCODING"]

    // MARK: - Propiedades

    public let executable: String
    /// Nombre con guion delante («-zsh»): así la shell se sabe de inicio y lee .zprofile.
    public let execName: String
    public let environment: [String]
    public let directory: String

    // MARK: - Inicializadores

    public init(folder: String, shell: String? = nil,
                environment source: [String: String] = ProcessInfo.processInfo.environment,
                language: String? = Locale.current.language.languageCode?.identifier,
                localeExists: (String) -> Bool = TerminalLaunch.localeInstalled) {
        let shell = Self.validShell(shell ?? Self.loginShell() ?? source["SHELL"]) ?? "/bin/zsh"
        executable = shell
        execName = "-" + (shell as NSString).lastPathComponent
        var isDirectory: ObjCBool = false
        directory = FileManager.default.fileExists(atPath: folder, isDirectory: &isDirectory) && isDirectory.boolValue
            ? folder : (source["HOME"] ?? NSHomeDirectory())
        var env = ["TERM=xterm-256color", "COLORTERM=truecolor", "TERM_PROGRAM=AlfalfaCode", "SHELL=\(shell)"]
        let spanish = "es_ES.UTF-8"
        env.append("LANG=" + (language == "es" && localeExists(spanish) ? spanish : "en_US.UTF-8"))
        for name in Self.inherited { if let value = source[name], !value.isEmpty { env.append("\(name)=\(value)") } }
        environment = env
    }

    // MARK: - Métodos

    /// Si macOS trae ese idioma (`/usr/share/locale/es_ES.UTF-8`).
    public static func localeInstalled(_ name: String) -> Bool {
        FileManager.default.fileExists(atPath: "/usr/share/locale/" + name)
    }

    /// La shell de la cuenta (la de Ajustes del Sistema → Usuarios), no la del entorno de la app.
    static func loginShell() -> String? {
        guard let entry = getpwuid(getuid()), let shell = entry.pointee.pw_shell else { return nil }
        return String(cString: shell)
    }

    /// Sólo una ruta absoluta a un ejecutable que exista.
    static func validShell(_ path: String?) -> String? {
        guard let path, path.hasPrefix("/"), FileManager.default.isExecutableFile(atPath: path) else { return nil }
        return path
    }
}

/// Lo que se escribe en la terminal al pulsar ▷ en un bloque de código: la orden y un Intro.
/// Con varias líneas, cada una se ejecuta como si se tecleara, igual que en Terminal.app.
public enum TerminalInput {
    public static func run(_ code: String) -> String {
        CodeActions.command(from: code).replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\n", with: "\r") + "\r"
    }
}
