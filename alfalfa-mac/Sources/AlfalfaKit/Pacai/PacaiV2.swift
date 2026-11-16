import Foundation

/// De los nombres en inglés de la extensión `alfalfa` a los de `--stdio` v2, para usar los decodificadores de
/// siempre. Es `en_ingles` (alfalfa/pacai/nombres.py) al revés: las claves, con la tabla generada (`PacaiNames`);
/// los valores enumerados, sólo los que lee la app y por tipo de mensaje, porque un valor inglés puede venir de
/// varios de v2 (`completed` es `terminado` en un subagente y `completada` en una cola).
enum PacaiV2 {

    // MARK: - Constantes

    /// Campo inglés → campo de v2. Cuando varios de v2 dan el mismo inglés, el que lee la app.
    static let fieldNames: [String: String] = {
        let preferred = ["updated": "actualizado", "blocked": "bloqueados", "deleted": "eliminada",
                         "commands": "ordenes", "pending": "pendientes"]
        var names: [String: String] = [:]
        for (spanish, english) in PacaiNames.fields where names[english] == nil || preferred[english] == spanish {
            names[english] = spanish
        }
        return names
    }()

    /// Los valores de v2 que puede traer cada campo, por lo que describe.
    static let modes = ["auto", "manual", "ediciones", "plan", "omitir"]
    static let contextModes = ["ahorro", "completo"]
    static let subagentStates = ["iniciando", "trabajando", "terminado", "fallido", "cerrado", "interrumpido"]
    static let queueStates = ["lista", "ejecutando", "pausando", "pausada", "completada", "cancelada"]
    /// Un encargo también trae el estado de su sesión en inglés (`completed`, `failed`, `blocked`): ése se queda.
    static let jobStates = ["pendiente", "ejecutando", "cancelado", "interrumpido"]
    static let cleanupStates = ["quitado", "conservado", "rama_conservada"]
    static let reviewStates = ["disponible", "revisando", "terminada", "fallida", "cancelada", "sin_presupuesto",
                               "sin_cierre", "sin_modelo_barato", "sin_senales", "tope_de_sesion", "no_escribe",
                               "apagada"]
    static let compactionStates = ["en_curso", "hecha", "fallida"]
    static let reasons = Array((PacaiNames.values["reason"] ?? [:]).keys)
    static let scopes = Array((PacaiNames.values["scope"] ?? [:]).keys)

    // MARK: - Métodos

    /// Las claves de un valor JSON, a cualquier profundidad, con su nombre de v2. Como `en_ingles`, nunca pisa una
    /// clave que ya esté en el mismo objeto: así una clave que el motor no tradujo vuelve tal cual.
    static func fields(_ value: Any) -> Any {
        if let list = value as? [Any] { return list.map(fields) }
        guard let object = value as? [String: Any] else { return value }
        var result: [String: Any] = [:]
        for key in object.keys.sorted() {
            var name = fieldNames[key] ?? key
            if name != key && (object[name] != nil || result[name] != nil) { name = key }
            result[name] = fields(object[key] as Any)
        }
        return result
    }

    /// Un objeto con sus claves de v2; vacío si no es un objeto.
    static func object(_ value: Any?) -> [String: Any] {
        value.map(fields) as? [String: Any] ?? [:]
    }

    /// El valor de v2 de `value` (en inglés, del campo inglés `field`) de entre `among`; si no es ninguno, tal cual.
    static func value(_ value: Any?, field: String, among: [String]) -> Any? {
        if let list = value as? [Any] { return list.map { self.value($0, field: field, among: among) as Any } }
        guard let english = value as? String, let table = PacaiNames.values[field] else { return value }
        return among.first { table[$0] == english } ?? english
    }

    /// Cambia en el sitio el valor de `key` (clave de v2) de `object`.
    static func fix(_ object: inout [String: Any], _ key: String, field: String, among: [String]) {
        guard let current = object[key] else { return }
        object[key] = value(current, field: field, among: among)
    }

    /// Lo mismo dentro de un objeto anidado (`object[parent][key]`).
    static func fix(_ object: inout [String: Any], in parent: String, _ key: String, field: String,
                    among: [String]) {
        guard var child = object[parent] as? [String: Any] else { return }
        fix(&child, key, field: field, among: among)
        object[parent] = child
    }

    /// Lo mismo en cada fila de una lista (`object[list][n][key]`).
    static func fixRows(_ object: inout [String: Any], _ list: String, _ key: String, field: String,
                        among: [String]) {
        guard let rows = object[list] as? [[String: Any]] else { return }
        object[list] = rows.map { row -> [String: Any] in
            var row = row
            fix(&row, key, field: field, among: among)
            return row
        }
    }

    /// El tipo de evento de v2 de una extensión `alfalfa.<nombre>`: los de `TIPOS` y el que el traductor nombra él
    /// (`subagents`).
    static func type(ofExtension name: String) -> String {
        if name == "subagents" { return "subagentes" }
        return PacaiNames.types.first { $0.value == name }?.key ?? name
    }
}
