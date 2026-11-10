import Foundation
import WebKit

extension BrowserEngine {
    func trackSiteURL(_ view: WKWebView) {
        guard !view.isLoading, let current = siteDocumentURL,
              let url = view.url, BrowserSitePolicy.origin(current) == BrowserSitePolicy.origin(url) else { return }
        siteDocumentURL = url
    }

    func prepareSites(_ request: BrowserRequest) throws -> BrowserReply? {
        if let policy = request.fields["sitios"] as? [String: Any] {
            sites.apply(try BrowserSitePolicy(policy))
        }
        if request.action == "abrir" { sites.pending = nil }
        guard let pending = sites.pending, request.action != "reanudar_sitio" else { return nil }
        if let target = pending.request.url, sites.policy?.blocks(target) == true {
            sites.pending = nil
            throw BrowserFailure("El sitio pendiente está bloqueado por la política actual.")
        }
        return sites.permission(request.id, result: state())
    }
}
