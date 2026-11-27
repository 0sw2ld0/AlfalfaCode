import Foundation

/// El archivo de referencia de la Lluvia de ideas: uno solo, Markdown o de texto, de hasta 16 KiB.
/// Lo mismo que `EXTENSIONES_REFERENCIA` y `MAX_BYTES_REFERENCIA` de `alfalfa/lluvia.py`.
public enum BrainstormReference {
    public static let extensions: Set<String> = ["md", "markdown", "txt"]
    public static let maximumBytes = 16 * 1024
    public static let notice = "Elige un archivo Markdown o de texto de hasta 16 KiB."

    /// Un archivo normal (no un enlace) con una extensión admitida y que cabe en el límite.
    public static func isValid(_ url: URL) -> Bool {
        guard url.isFileURL, extensions.contains(url.pathExtension.lowercased()) else { return false }
        let attributes = try? url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
        guard attributes?.isRegularFile == true, attributes?.isSymbolicLink == false,
              let size = attributes?.fileSize else { return false }
        return size <= maximumBytes
    }

    /// De lo que se elige o se suelta, el adjunto si es un único archivo válido; si no, nil.
    public static func pick(_ urls: [URL]) -> URL? {
        guard urls.count == 1, let url = urls.first, isValid(url) else { return nil }
        return url
    }
}
