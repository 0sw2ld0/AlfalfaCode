// Firma las peticiones al servicio Alfalfa con una clave del Secure Enclave.
//
//   alfalfa-firma clave   --archivo RUTA   crea la clave si no existe e imprime la pública
//   alfalfa-firma firmar  --archivo RUTA   firma la entrada estándar e imprime la firma
//
// La clave privada nunca sale del Secure Enclave. En RUTA sólo se guarda su
// `dataRepresentation`, un envoltorio cifrado que únicamente el Secure Enclave
// de este Mac sabe abrir: copiado a otro ordenador no sirve para firmar.
// La pública sale en `x963Representation` y la firma en `derRepresentation`,
// ambas en base64. Cada respuesta es una línea JSON.
import CryptoKit
import Foundation

func responder(_ valor: [String: Any], codigo: Int32 = 0) -> Never {
    let datos = (try? JSONSerialization.data(withJSONObject: valor)) ?? Data("{}".utf8)
    FileHandle.standardOutput.write(datos + Data("\n".utf8))
    exit(codigo)
}

func fallar(_ texto: String) -> Never { responder(["ok": false, "error": texto], codigo: 1) }

let argumentos = Array(CommandLine.arguments.dropFirst())
guard let orden = argumentos.first else { fallar("Falta la orden: clave o firmar.") }
guard let archivo = argumentos.first(where: { $0.hasPrefix("--archivo=") })?.dropFirst("--archivo=".count)
        ?? argumentos.firstIndex(of: "--archivo").flatMap({ $0 + 1 < argumentos.count ? Substring(argumentos[$0 + 1]) : nil })
else { fallar("Falta --archivo.") }
let url = URL(fileURLWithPath: String(archivo))

guard SecureEnclave.isAvailable else {
    fallar("Este Mac no tiene Secure Enclave: el código de cortesía necesita uno para atarse al ordenador.")
}

func cargar(crear: Bool) -> SecureEnclave.P256.Signing.PrivateKey {
    if let datos = try? Data(contentsOf: url) {
        do { return try SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: datos) } catch {
            fallar("La clave guardada no es de este Mac o está dañada. Pide que liberen el código y actívalo otra vez.")
        }
    }
    guard crear else { fallar("Aún no hay clave: activa el código primero.") }
    do {
        let clave = try SecureEnclave.P256.Signing.PrivateKey()
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        try clave.dataRepresentation.write(to: url, options: [.atomic])
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        return clave
    } catch {
        fallar("No se pudo crear la clave en el Secure Enclave: \(error.localizedDescription)")
    }
}

switch orden {
case "clave":
    let clave = cargar(crear: true)
    responder(["ok": true, "publica": clave.publicKey.x963Representation.base64EncodedString()])
case "firmar":
    let clave = cargar(crear: false)
    let datos = FileHandle.standardInput.readDataToEndOfFile()
    do {
        let firma = try clave.signature(for: datos)
        responder(["ok": true, "firma": firma.derRepresentation.base64EncodedString(),
                   "publica": clave.publicKey.x963Representation.base64EncodedString()])
    } catch {
        fallar("El Secure Enclave no pudo firmar: \(error.localizedDescription)")
    }
default:
    fallar("Orden desconocida: \(orden)")
}
