import AlfalfaKit
import Foundation

/// Ajustes ▸ Memoria: `alfalfa --memoria` y `alfalfa --aprendido` en la carpeta de trabajo.
extension AppModel {

    // MARK: - Métodos públicos

    /// Los tres almacenes; con `arguments` (`["escribir", "usuario"]` y `text`) los cambia antes.
    /// El texto va por la entrada estándar: en los argumentos se vería en la lista de procesos.
    func memoryState(in folder: URL, _ arguments: [String] = [], text: String? = nil)
        async -> (state: MemoryState?, error: String?) {
        switch await learningQuery(["--memoria"] + arguments, in: folder, input: text) {
        case let .success(messages):
            for case let .memory(state) in messages { return (state, nil) }
            for case let .error(text) in messages { return (nil, text) }
            return (nil, "alfalfa no devolvió la memoria.")
        case let .failure(error): return (nil, error.localizedDescription)
        }
    }

    /// Habilidades aprendidas, propuestas y ajustes; con `arguments` (`["bloquear", "usuario:x"]`) los cambia.
    func learnedState(in folder: URL, _ arguments: [String] = [], text: String? = nil)
        async -> (state: LearnedState?, error: String?) {
        switch await learningQuery(["--aprendido"] + arguments, in: folder, input: text) {
        case let .success(messages):
            for case let .learned(state) in messages { return (state, nil) }
            for case let .error(text) in messages { return (nil, text) }
            return (nil, "alfalfa no devolvió lo aprendido.")
        case let .failure(error): return (nil, error.localizedDescription)
        }
    }

    /// Los archivos de una habilidad aprendida (`usuario:nombre`).
    func learnedSkillFiles(_ reference: String, in folder: URL) async -> [String: String]? {
        guard case let .success(messages) = await learningQuery(["--aprendido", "ver", reference], in: folder) else {
            return nil
        }
        for case let .learnedSkillFiles(_, files) in messages { return files }
        return nil
    }

    // MARK: - Métodos privados

    private func learningQuery(_ arguments: [String], in folder: URL,
                               input: String? = nil) async -> Result<[AlfalfaMessage], Error> {
        guard let launcher else { return .failure(AlfalfaError.failed("No se encuentra la orden alfalfa.")) }
        do {
            return .success(try await AlfalfaProcess.query(launcher: launcher, arguments: arguments + ["--json"],
                                                           directory: folder, input: input))
        } catch {
            return .failure(error)
        }
    }
}
