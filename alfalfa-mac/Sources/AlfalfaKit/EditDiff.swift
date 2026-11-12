import Foundation

/// El diff de una edición (`diff` del permiso de write_file, edit_file o apply_patch), por archivo:
/// la tarjeta enseña cada uno con sus líneas +/− y su contenido plegable, nunca el JSON de la llamada.
public struct EditDiff: Equatable, Sendable {
    public struct File: Equatable, Sendable, Identifiable {
        public enum Change: Equatable, Sendable { case added, deleted, modified, renamed(from: String) }

        public var path: String
        public var change: Change
        public var added: Int
        public var removed: Int
        /// Las líneas del archivo tras sus cabeceras: `@@`, `+`, `-` y contexto.
        public var lines: [String]

        public var id: String { path }
    }

    public var files: [File]

    public var added: Int { files.reduce(0) { $0 + $1.added } }
    public var removed: Int { files.reduce(0) { $0 + $1.removed } }

    /// nil si el texto no trae ninguna cabecera `--- … / +++ …` (un diff de otro tipo).
    public init?(_ text: String) {
        var files: [File] = []
        var pendingOld: String?
        for line in text.split(separator: "\n", omittingEmptySubsequences: false).map(String.init) {
            if line.hasPrefix("--- ") {
                pendingOld = Self.path(line.dropFirst(4))
                continue
            }
            if line.hasPrefix("+++ "), let old = pendingOld {
                pendingOld = nil
                let new = Self.path(line.dropFirst(4))
                let change: File.Change
                switch (old, new) {
                case ("/dev/null", _): change = .added
                case (_, "/dev/null"): change = .deleted
                case let (old, new) where old != new: change = .renamed(from: old)
                default: change = .modified
                }
                files.append(File(path: new == "/dev/null" ? old : new, change: change, added: 0, removed: 0, lines: []))
                continue
            }
            if let old = pendingOld {
                // Un «---» que no era cabecera: una línea borrada que empezaba por «-- ».
                pendingOld = nil
                Self.append("--- " + old, to: &files)
            }
            Self.append(line, to: &files)
        }
        guard !files.isEmpty else { return nil }
        self.files = files
    }

    // MARK: - Métodos privados

    private static func append(_ line: String, to files: inout [File]) {
        guard !files.isEmpty else { return }
        if line.isEmpty, files[files.count - 1].lines.isEmpty { return }
        if line.hasPrefix("+") { files[files.count - 1].added += 1 }
        if line.hasPrefix("-") { files[files.count - 1].removed += 1 }
        files[files.count - 1].lines.append(line)
    }

    /// «a/src/x.py» → «src/x.py»; «/dev/null» se queda; la fecha de `diff -u` se quita.
    private static func path(_ raw: Substring) -> String {
        var text = String(raw.split(separator: "\t", maxSplits: 1).first ?? raw)
        if text.hasPrefix("a/") || text.hasPrefix("b/") { text.removeFirst(2) }
        return text
    }
}
