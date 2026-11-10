import Foundation
import WebKit

struct BrowserSiteVisit: Equatable {
    let id: String
    let origin: String
}

/// Foto de la política decidida por el motor. La app nunca añade confianza duradera.
struct BrowserSitePolicy: Equatable {
    let allowed: Set<String>
    let denied: Set<String>
    let limit: Set<String>?
    let strict: Bool
    var visit: BrowserSiteVisit?

    init(_ fields: [String: Any]) throws {
        guard fields["version"] as? Int == 1,
              let allowed = fields["permitidos"] as? [String],
              let denied = fields["bloqueados"] as? [String],
              let strict = fields["estricto"] as? Bool,
              allowed.count <= 600, denied.count <= 600 else {
            throw BrowserFailure("La política de sitios del motor no es válida.")
        }
        self.allowed = try Self.origins(allowed)
        self.denied = try Self.origins(denied)
        self.limit = try (fields["limite_proyecto"] as? [String]).map(Self.origins)
        self.strict = strict
        if let visit = fields["visita"] as? [String: Any] {
            guard let id = visit["id"] as? String,
                  id.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil,
                  let origin = visit["origen"] as? String else {
                throw BrowserFailure("La concesión de una visita no es válida.")
            }
            _ = try Self.origins([origin])
            self.visit = BrowserSiteVisit(id: id, origin: origin)
        }
    }

    func blocks(_ url: URL) -> Bool {
        guard let origin = Self.origin(url) else { return true }
        return denied.contains(origin) || limit.map { !$0.contains(origin) } == true
    }

    func permits(_ url: URL) -> Bool {
        guard let origin = Self.origin(url) else { return false }
        return !blocks(url) && (allowed.contains(origin) || visit?.origin == origin)
    }

    static func origin(_ url: URL) -> String? {
        guard let scheme = url.scheme?.lowercased(), ["http", "https"].contains(scheme),
              url.user == nil, url.password == nil, var host = url.host?.lowercased(), !host.isEmpty else { return nil }
        host = host.trimmingCharacters(in: CharacterSet(charactersIn: ".[]"))
        var address = in_addr()
        if inet_aton(host, &address) == 1 {
            host = withUnsafeBytes(of: address.s_addr) { $0.map(String.init).joined(separator: ".") }
        } else if host.contains(":") {
            var address6 = in6_addr()
            guard inet_pton(AF_INET6, host, &address6) == 1 else { return nil }
            var buffer = [CChar](repeating: 0, count: Int(INET6_ADDRSTRLEN))
            guard inet_ntop(AF_INET6, &address6, &buffer, socklen_t(buffer.count)) != nil else { return nil }
            host = "[" + String(cString: buffer) + "]"
        }
        let port = url.port ?? (scheme == "https" ? 443 : 80)
        guard (1...65535).contains(port) else { return nil }
        return scheme + "://" + host + ":" + String(port)
    }

    private static func origins(_ values: [String]) throws -> Set<String> {
        guard values.count <= 600 else { throw BrowserFailure("Demasiados sitios en la política del motor.") }
        return try Set(values.map { value in
            guard let url = URL(string: value), let origin = Self.origin(url), origin == value else {
                throw BrowserFailure("Un origen de la política de sitios no es válido.")
            }
            return origin
        })
    }
}

/// Una navegación cancelada antes de contactar con un sitio sin permiso.
@MainActor
final class BrowserSiteState {
    struct Pending {
        let token: String
        let request: URLRequest
        let document: Int
        let source: URL?
        let owner: String?
    }

    var policy: BrowserSitePolicy?
    var pending: Pending?
    private(set) var currentVisit: BrowserSiteVisit?
    private(set) var consumedVisits: Set<String> = []
    private(set) var reportedConsumption: String?

    func apply(_ policy: BrowserSitePolicy) {
        var fresh = policy
        reportedConsumption = nil
        if let visit = fresh.visit, consumedVisits.contains(visit.id) {
            reportedConsumption = visit.id
            fresh.visit = nil
        }
        self.policy = fresh
    }

    /// Sólo un commit abandona la visita. Cancelar un destino conserva el documento actual.
    @discardableResult
    func commitVisit(at url: URL?) -> Bool {
        let origin = url.flatMap(BrowserSitePolicy.origin)
        var changed = false
        if let currentVisit, currentVisit.origin != origin {
            consumedVisits.insert(currentVisit.id)
            reportedConsumption = currentVisit.id
            self.currentVisit = nil
            if policy?.visit?.id == currentVisit.id { policy?.visit = nil }
            changed = true
        }
        if let visit = policy?.visit, visit.origin == origin, !consumedVisits.contains(visit.id) {
            currentVisit = visit
        }
        return changed
    }

    func intercept(_ request: URLRequest, document: Int, source: URL?, owner: String? = nil) throws -> Bool {
        if let source { commitVisit(at: source) }
        guard let policy, let url = request.url, ["http", "https"].contains(url.scheme ?? "") else { return false }
        if policy.blocks(url) { throw BrowserFailure("El sitio está bloqueado por las reglas del proyecto.") }
        guard !policy.permits(url) else { return false }
        pending = Pending(token: UUID().uuidString, request: request, document: document, source: source, owner: owner)
        return true
    }

    func permission(_ id: String, result: [String: Any]) -> BrowserReply? {
        guard let pending, let url = pending.request.url else { return nil }
        return .sitePermission(id, url: url.absoluteString, token: pending.token, initiated: pending.owner == id,
                               result: result)
    }

    func take(_ request: BrowserRequest, document: Int, source: URL?) throws -> URLRequest {
        guard let pending, request.string("token") == pending.token,
              request.string("url") == pending.request.url?.absoluteString,
              pending.document == document, pending.source == source,
              let url = pending.request.url, policy?.permits(url) == true else {
            self.pending = nil
            throw BrowserFailure("La navegación pendiente cambió o ya no está autorizada; vuelve a abrir la dirección.")
        }
        self.pending = nil
        let method = (pending.request.httpMethod ?? "GET").uppercased()
        // Visitar B no autoriza a entregar allí un cuerpo que se aprobó para A, aunque WebKit lo conserve.
        guard ["GET", "HEAD"].contains(method) else {
            throw BrowserFailure("Sitio aprobado para el siguiente intento. Visitarlo no autoriza reenviar allí "
                                 + "un formulario ni su cuerpo. No se envió ni se repitió. "
                                 + "Repite la acción explícitamente.")
        }
        guard pending.request.httpBodyStream == nil else {
            throw BrowserFailure("Sitio aprobado. No se puede repetir de forma segura un cuerpo en streaming.")
        }
        return pending.request
    }
}
