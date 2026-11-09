import AlfalfaKit
import Foundation

/// Un modelo elegido cuyo proveedor no tiene clave: qué falta y qué hacer al guardarla.
struct KeyRequest: Identifiable {
    let id = UUID()
    let model: ModelInfo
    let missing: MissingKey
    let apply: (String) -> Void
}

/// Un proveedor de «Gratis ahora» en el selector, con sus modelos.
struct FreeGroup {
    let provider: String
    let title: String
    let names: [String]
}

/// Lo que viene del catálogo de models.dev: «Gratis ahora», la clave que falta y el coste estimado.
extension AppModel {

    // MARK: - Propiedades

    /// Los gratis ahora por proveedor: primero los que ya tienen clave, luego los que tienen más modelos.
    var freeGroups: [FreeGroup] {
        let free = models.filter(\.free)
        var order: [String] = []
        var groups: [String: [ModelInfo]] = [:]
        for row in free {
            if groups[row.provider] == nil { order.append(row.provider) }
            groups[row.provider, default: []].append(row)
        }
        let sorted = order.sorted { left, right in
            let leftReady = groups[left]?.first?.missingKey == nil
            let rightReady = groups[right]?.first?.missingKey == nil
            if leftReady != rightReady { return leftReady }
            return (groups[left]?.count ?? 0) > (groups[right]?.count ?? 0)
        }
        return sorted.map { provider in
            let rows = groups[provider] ?? []
            let name = rows.first?.providerName ?? providerName(provider)
            let ready = rows.first?.missingKey == nil
            return FreeGroup(provider: provider, title: name + " (\(rows.count))" + (ready ? "" : " · sin clave"),
                             names: rows.map(\.name))
        }
    }

    // MARK: - Métodos públicos

    /// La fila de la lista de modelos con ese nombre, si está.
    func info(for name: String) -> ModelInfo? {
        let listed = ModelProvider.listed(name, in: models)
        return models.first { $0.name == listed }
    }

    /// Un modelo elegido antes sin `ollama/` que la lista ahora trae con él (su autor se llama como un
    /// proveedor del catálogo) pasa a guardarse con él, para que siga yendo a Ollama.
    func adoptListedNames() {
        let current = ModelProvider.listed(model, in: models)
        if current != model { model = current }
        let saved = ModelProvider.listed(defaultModel, in: models)
        if saved != defaultModel { defaultModel = saved }
    }

    /// Elige un modelo; si a su proveedor le falta la clave, primero la pide en una hoja.
    func choose(_ name: String, apply: @escaping (String) -> Void) {
        if let row = info(for: name), let missing = row.missingKey {
            keyRequest = KeyRequest(model: row, missing: missing, apply: apply)
        } else {
            apply(name)
        }
    }

    /// Guarda la clave de la hoja en el Llavero y, si se pudo, aplica el modelo elegido.
    func saveKey(for request: KeyRequest, secret: String) async -> Bool {
        guard await saveKey(secret, for: request.model.provider) else { return false }
        request.apply(request.model.name)
        keyRequest = nil
        return true
    }
}
