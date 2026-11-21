import Foundation

public struct AppControlPreview: Decodable, Equatable, Sendable {
    public let app: String
    public let action: String
    public let notice: String
    public let permissionRequest: String?
    fileprivate let capture: String?

    enum CodingKeys: String, CodingKey {
        case app
        case action = "accion"
        case notice = "aviso"
        case capture = "captura"
        case permissionRequest = "solicitud_permiso"
    }
}

/// La última actividad del control de apps vive en un archivo local, fuera del chat.
public struct AppControlPreviewStore: Hashable, Sendable {
    private let files: SessionActivityFiles

    public init?(home: URL, sessionID: String) {
        guard let files = SessionActivityFiles(home: home, sessionID: sessionID) else { return nil }
        self.files = files
    }

    public func load() -> AppControlPreview? {
        guard let data = try? files.read(["vista-control-apps.json"], limit: 4096, privateFile: true) else { return nil }
        return try? JSONDecoder().decode(AppControlPreview.self, from: data)
    }

    public func capture(for preview: AppControlPreview) -> Data? {
        guard let name = preview.capture,
              name.range(of: #"\A(?:[0-9]{1,10}-[0-9]{13,16}|vista-app-[0-9a-f]{32})\.png\z"#,
                         options: .regularExpression) != nil else { return nil }
        return files.capture(named: name, limit: 8 * 1024 * 1024)
    }
}
