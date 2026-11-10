import Foundation

/// Una decisión humana sobre un origen exacto, siempre circunscrita a un proyecto.
public struct BrowserSitePermission: Equatable, Sendable {
    public let origin: String
    public let project: String

    public init(origin: String, project: String) {
        self.origin = origin
        self.project = project
    }

    init?(_ value: Any?) {
        guard let fields = value as? [String: Any], let origin = fields["origen"] as? String,
              let project = fields["proyecto"] as? String, !origin.isEmpty, !project.isEmpty else { return nil }
        self.init(origin: origin, project: project)
    }
}
