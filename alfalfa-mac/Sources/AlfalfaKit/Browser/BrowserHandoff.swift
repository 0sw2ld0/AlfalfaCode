import Foundation

/// Petición del agente a la persona: sólo los botones del panel completan el relevo.
public struct BrowserHandoff: Equatable, Sendable {
    public let id: String
    public let reason: String
    public let urlContains: String?
}

// MARK: - Relevo a la persona

extension BrowserEngine {
    /// Se comprueba de nuevo al continuar: la URL puede cambiar mientras la persona trabaja.
    public var canContinueHandoff: Bool {
        guard let handoff else { return false }
        guard let fragment = handoff.urlContains else { return true }
        return webView.url?.absoluteString.contains(fragment) == true
    }

    public func continueHandoff() {
        guard let handoff, canContinueHandoff else { return }
        finishHandoff(.success(handoff.id, ["url": webView.url?.absoluteString ?? "",
                                          "titulo": webView.title ?? "", "continuado": true]))
    }

    public func cancelHandoff(id: String? = nil) {
        if let id, queuedHandoffs[id] != nil { queuedHandoffs[id] = true }
        guard let handoff, id == nil || id == handoff.id else { return }
        finishHandoff(.failure(handoff.id, "La persona canceló el relevo del navegador."))
    }

    func beginHandoff(_ request: BrowserRequest) async -> BrowserReply {
        guard let reason = request.string("motivo"), !reason.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              reason.count <= 2000 else { return .failure(request.id, "Falta el motivo del relevo.") }
        handoff = BrowserHandoff(id: request.id, reason: reason, urlContains: request.string("url_contiene"))
        let seconds = min(300, max(0.05, (request.fields["tiempo"] as? NSNumber)?.doubleValue ?? 295))
        return await withCheckedContinuation { continuation in
            handoffContinuation = continuation
            handoffDeadline = Task { @MainActor [weak self] in
                do { try await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000)) } catch { return }
                guard let self, self.handoff?.id == request.id else { return }
                self.finishHandoff(.failure(request.id, "El relevo del navegador agotó el tiempo de espera."))
            }
        }
    }

    private func finishHandoff(_ reply: BrowserReply) {
        handoffDeadline?.cancel()
        handoffDeadline = nil
        handoff = nil
        let continuation = handoffContinuation
        handoffContinuation = nil
        continuation?.resume(returning: reply)
    }
}
