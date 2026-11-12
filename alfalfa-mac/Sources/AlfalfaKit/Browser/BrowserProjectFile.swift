import Darwin
import Foundation

/// Abre cada componente relativo al proyecto sin seguir enlaces, también si cambian durante el permiso.
enum BrowserProjectFile {
    static func read(path: URL, root: URL, remaining: Int) throws -> Data {
        guard path.path.hasPrefix(root.path + "/"), remaining >= 0 else {
            throw BrowserFailure("El archivo está fuera del proyecto o supera el límite de 20 MiB.")
        }
        let components = String(path.path.dropFirst(root.path.count + 1)).split(separator: "/").map(String.init)
        guard let name = components.last, !components.contains("..") else {
            throw BrowserFailure("Ruta de archivo no válida.")
        }
        var directory = open(root.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
        guard directory >= 0 else { throw BrowserFailure("No se pudo abrir el proyecto de la subida.") }
        defer { close(directory) }
        for component in components.dropLast() {
            let next = openat(directory, component, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
            guard next >= 0 else { throw BrowserFailure("La subida no sigue enlaces ni carpetas inaccesibles.") }
            close(directory)
            directory = next
        }
        let descriptor = openat(directory, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard descriptor >= 0 else { throw BrowserFailure("No se pudo abrir el archivo sin seguir enlaces.") }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        defer { try? handle.close() }
        var attributes = stat()
        guard fstat(descriptor, &attributes) == 0, (attributes.st_mode & S_IFMT) == S_IFREG,
              attributes.st_size >= 0, attributes.st_size <= remaining else {
            throw BrowserFailure("Sólo se suben archivos normales hasta un total de 20 MiB.")
        }
        guard attributes.st_nlink == 1 else {
            throw BrowserFailure("No se suben archivos con enlaces duros: elige un archivo independiente del proyecto.")
        }
        let data = try handle.read(upToCount: remaining + 1) ?? Data()
        guard data.count <= remaining, data.count == attributes.st_size else {
            throw BrowserFailure("El archivo cambió antes de subirlo o supera el límite de 20 MiB.")
        }
        return data
    }
}
