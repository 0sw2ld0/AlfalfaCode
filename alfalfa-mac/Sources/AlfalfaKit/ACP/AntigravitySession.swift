import Foundation
import Combine

public struct AntigravityMCPServer: Identifiable, Equatable {
    public let id: String
    private enum Endpoint: Equatable {
        case stdio(String, [String], [String: String])
        case https(URL)
    }

    private let endpoint: Endpoint

    public static func delegation(launcher: URL, model: String,
                                  home: String? = ProcessInfo.processInfo.environment["ALFALFA_HOME"]) -> Self {
        let environment = home.map { ["ALFALFA_HOME": $0] } ?? [:]
        return Self(id: "alfalfa-delegation", endpoint: .stdio(launcher.path, ["--delegacion-mcp", model], environment))
    }

    public var summary: String {
        switch endpoint {
        case let .stdio(command, arguments, _): return ([command] + arguments).joined(separator: "\n")
        case let .https(url): return url.absoluteString
        }
    }

    fileprivate var isHTTP: Bool {
        if case .https = endpoint { return true }
        return false
    }

    /// Only entries without credentials or custom tool permissions. A local server must
    /// already allow network access in AlfalfaCode because ACP cannot carry that restriction.
    public static func portableServers(in data: Data) throws -> [Self] {
        guard data.count <= 256 * 1024,
              let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let servers = object["servidores"] as? [String: Any] else {
            throw CocoaError(.fileReadCorruptFile)
        }
        return servers.compactMap { name, value -> Self? in
            guard name.range(of: #"^[A-Za-z0-9][A-Za-z0-9-]{0,23}$"#, options: .regularExpression) != nil,
                  let server = value as? [String: Any],
                  server["activo"] == nil || server["activo"] as? Bool == true else { return nil }
            let keys = Set(server.keys)
            if keys.isSubset(of: ["orden", "argumentos", "red", "activo"]),
               server["red"] as? Bool == true, let command = server["orden"] as? String, command.hasPrefix("/"),
               server["argumentos"] == nil || server["argumentos"] is [String] {
                let arguments = server["argumentos"] as? [String] ?? []
                guard ([command] + arguments).allSatisfy({ $0 == Redactor.clean($0) }) else { return nil }
                return Self(id: name, endpoint: .stdio(command, arguments, [:]))
            }
            guard keys.isSubset(of: ["url", "activo"]), let text = server["url"] as? String,
                  let parsed = URLComponents(string: text), parsed.scheme == "https",
                  let host = parsed.host, !host.isEmpty,
                  parsed.user == nil, parsed.password == nil, parsed.query == nil, parsed.fragment == nil,
                  ["", "/", "/mcp", "/mcp/", "/api/mcp", "/api/mcp/"].contains(parsed.percentEncodedPath),
                  let url = parsed.url else { return nil }
            return Self(id: name, endpoint: .https(url))
        }
        .sorted { $0.id < $1.id }
    }

    fileprivate var params: [String: Any] {
        switch endpoint {
        case let .stdio(command, arguments, environment):
            let entries = environment.sorted { $0.key < $1.key }.map { ["name": $0.key, "value": $0.value] }
            return ["name": id, "command": command, "args": arguments, "env": entries]
        case let .https(url):
            return ["type": "http", "name": id, "url": url.absoluteString, "headers": [[String: String]]()]
        }
    }
}

@MainActor
public final class AntigravitySession: ObservableObject {
    public enum State: Equatable {
        case disconnected, connecting, signInRequired, signingIn, ready, running, cancelling
    }

    public struct Entry: Identifiable, Equatable {
        public enum Kind: Equatable { case user, response, tool, notice }
        public let id = UUID()
        public let kind: Kind
        public var text: String
        public var status: String?
    }

    public struct Usage: Equatable {
        public let used: Int
        public let size: Int
    }

    public struct Approval: Identifiable, Equatable {
        public let id = UUID()
        public let title: String
        public let details: String?
        public let canAllowOnce: Bool
    }

    @Published public private(set) var state: State = .disconnected
    @Published public private(set) var entries: [Entry] = []
    @Published public private(set) var approvals: [Approval] = []
    @Published public private(set) var usage: Usage?
    @Published public private(set) var error: String?
    @Published public private(set) var directory: URL?

    private enum Request { case initialize, authenticate, newSession, prompt }
    private struct PendingApproval {
        var requestID: Any
        var allowOnce: String?
        var rejectOnce: String?
    }

    private var process: AlfalfaProcess?
    private var generation = UUID()
    private var connectionTimeout: Task<Void, Never>?
    private var sessionID: String?
    private var nextID = 0
    private var requests: [Int: Request] = [:]
    private var waiting: [UUID: PendingApproval] = [:]
    private var tools: [String: UUID] = [:]
    private var agentEntry: UUID?
    private var googleSignIn = false
    private var servers: [AntigravityMCPServer] = []

    public init() {}

    deinit { process?.terminate() }

    public static func executable(environment: [String: String] = ProcessInfo.processInfo.environment) -> URL? {
        let fm = FileManager.default
        let home = fm.homeDirectoryForCurrentUser
        let installed = [home.appendingPathComponent(".local/bin"), home.appendingPathComponent(".antigravity/bin")]
        let paths = (environment["PATH"] ?? "").split(separator: ":")
            .filter { $0.hasPrefix("/") }.map { URL(fileURLWithPath: String($0), isDirectory: true) }
        let fallback = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"].map { URL(fileURLWithPath: $0) }
        return (installed + paths + fallback)
            .map { $0.appendingPathComponent("agy") }
            .first { fm.isExecutableFile(atPath: $0.path) }
    }

    public func connect(executable: URL, directory: URL, servers: [AntigravityMCPServer] = []) {
        guard state == .disconnected else { return }
        entries = []
        tools = [:]
        usage = nil
        error = nil
        self.directory = directory
        self.servers = servers
        let current = generation
        var arguments = ["acp", "--sandbox"]
        if servers.contains(where: \.isHTTP) { arguments.append("--process-per-session") }
        var env = ProcessInfo.processInfo.environment.filter {
            ["HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR",
             "PATH", "GEMINI_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"].contains($0.key)
        }
        env["PATH"] = (env["PATH"] ?? "").split(separator: ":").filter { $0.hasPrefix("/") }.joined(separator: ":")
        let child = AlfalfaProcess(launcher: executable, arguments: arguments, directory: directory, environment: env)
        child.onRawLine = { [weak self] line in
            DispatchQueue.main.async {
                guard let self, self.generation == current else { return }
                self.receive(line)
            }
        }
        child.onExit = { [weak self] status, _ in
            DispatchQueue.main.async {
                guard let self, self.generation == current else { return }
                self.fail("Antigravity CLI terminó (código \(status)). Revisa el inicio de sesión y vuelve a conectar.")
            }
        }
        do {
            try child.start()
            process = child
            state = .connecting
            request(.initialize, method: "initialize", params: [
                "protocolVersion": 1, "clientCapabilities": [:], "clientInfo": ["name": "AlfalfaCode"]
            ])
            startTimeout(seconds: 30)
        } catch {
            fail("No se pudo iniciar Antigravity CLI. Comprueba que el ejecutable seleccionado funciona.")
        }
    }

    public func signIn() {
        guard state == .signInRequired, googleSignIn else { return }
        state = .signingIn
        error = nil
        request(.authenticate, method: "authenticate", params: ["methodId": "oauth-personal"])
        startTimeout(seconds: 180)
    }

    private func startTimeout(seconds: Int) {
        connectionTimeout?.cancel()
        connectionTimeout = Task { [weak self] in
            try? await Task.sleep(for: .seconds(seconds))
            guard !Task.isCancelled else { return }
            self?.fail("Antigravity no completó la conexión a tiempo. Comprueba el inicio de sesión y reintenta.")
        }
    }

    public func send(_ text: String) {
        guard state == .ready, let sessionID, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return
        }
        guard text.utf8.count <= 64 * 1024 else {
            error = "El mensaje supera los 64 KB."
            return
        }
        error = nil
        agentEntry = nil
        entries.append(Entry(kind: .user, text: text))
        state = .running
        request(.prompt, method: "session/prompt", params: [
            "sessionId": sessionID, "prompt": [["type": "text", "text": text]]
        ])
    }

    public func cancelTurn() {
        guard state == .running, let sessionID else { return }
        state = .cancelling
        cancelApprovals()
        send(["method": "session/cancel", "params": ["sessionId": sessionID]])
    }

    public func respond(to approval: Approval, allow: Bool) {
        guard let pending = waiting.removeValue(forKey: approval.id) else { return }
        approvals.removeAll { $0.id == approval.id }
        outcome(pending.requestID, selected: allow ? pending.allowOnce : pending.rejectOnce)
    }

    public func disconnect() {
        generation = UUID()
        connectionTimeout?.cancel()
        connectionTimeout = nil
        cancelApprovals()
        process?.closeInput()
        process?.terminate()
        process = nil
        sessionID = nil
        requests = [:]
        googleSignIn = false
        state = .disconnected
    }

    private func fail(_ message: String) {
        disconnect()
        error = message
    }

    private func request(_ request: Request, method: String, params: [String: Any]) {
        nextID += 1
        requests[nextID] = request
        send(["id": nextID, "method": method, "params": params])
    }

    private func send(_ fields: [String: Any]) {
        var frame = fields
        frame["jsonrpc"] = "2.0"
        guard let data = try? JSONSerialization.data(withJSONObject: frame),
              let process else { return }
        process.write(line: String(decoding: data, as: UTF8.self))
    }

    private func receive(_ line: Data) {
        guard !line.isEmpty else { return }
        guard let frame = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any],
              frame["jsonrpc"] as? String == "2.0" else {
            fail("Antigravity devolvió una línea ACP inválida.")
            return
        }
        if let method = frame["method"] as? String {
            let params = frame["params"] as? [String: Any] ?? [:]
            if let id = frame["id"], id is String || id is NSNumber {
                if method == "session/request_permission" { permission(params, requestID: id) }
                else { send(["id": id, "error": ["code": -32601, "message": "Método no disponible en este cliente"]]) }
            } else if method == "session/update", let sessionID, params["sessionId"] as? String == sessionID,
                      let update = params["update"] as? [String: Any] {
                updateSession(update)
            }
            return
        }
        guard let id = frame["id"] as? Int, let request = requests.removeValue(forKey: id) else { return }
        if let failure = frame["error"] as? [String: Any] {
            let message = Redactor.clean(String((failure["message"] as? String ?? "Error de Antigravity.").prefix(500)))
            if googleSignIn && failure["code"] as? Int == -32000 {
                connectionTimeout?.cancel()
                cancelApprovals()
                state = .signInRequired
                if request == .authenticate { error = message }
            } else if request == .prompt {
                cancelApprovals()
                state = .ready
                error = message
            } else {
                fail(message + " Si falta autorización, inicia sesión ejecutando agy en Terminal.")
            }
            return
        }
        let result = frame["result"] as? [String: Any] ?? [:]
        switch request {
        case .initialize:
            guard result["protocolVersion"] as? Int == 1 else {
                fail("Esta versión de Antigravity no negoció ACP v1.")
                return
            }
            let capabilities = result["agentCapabilities"] as? [String: Any]
            let mcp = capabilities?["mcpCapabilities"] as? [String: Any]
            googleSignIn = (result["authMethods"] as? [[String: Any]])?.contains {
                $0["id"] as? String == "oauth-personal" && ($0["type"] == nil || $0["type"] as? String == "agent")
            } == true
            if servers.contains(where: \.isHTTP) && mcp?["http"] as? Bool != true {
                fail("Este CLI no anuncia MCP por HTTP. Desmarca esos servidores y configúralos en Antigravity.")
                return
            }
            newSession()
        case .authenticate:
            if sessionID != nil {
                connectionTimeout?.cancel()
                connectionTimeout = nil
                state = .ready
            } else {
                state = .connecting
                startTimeout(seconds: 30)
                newSession()
            }
        case .newSession:
            guard let id = result["sessionId"] as? String, !id.isEmpty else {
                fail("Antigravity no devolvió el identificador de sesión.")
                return
            }
            connectionTimeout?.cancel()
            connectionTimeout = nil
            sessionID = id
            state = .ready
        case .prompt:
            cancelApprovals()
            if result["stopReason"] as? String == "cancelled" {
                entries.append(Entry(kind: .notice, text: "Turno cancelado."))
            }
            state = .ready
        }
    }

    private func newSession() {
        guard let directory else { return }
        request(.newSession, method: "session/new", params: [
            "cwd": directory.path, "mcpServers": servers.map(\.params)
        ])
    }

    private func updateSession(_ update: [String: Any]) {
        switch update["sessionUpdate"] as? String {
        case "agent_message_chunk":
            guard let content = update["content"] as? [String: Any], content["type"] as? String == "text",
                  let text = content["text"] as? String, !text.isEmpty else { return }
            if let agentEntry, let position = entries.firstIndex(where: { $0.id == agentEntry }) {
                entries[position].text += Redactor.clean(text)
            } else {
                let entry = Entry(kind: .response, text: Redactor.clean(text))
                entries.append(entry)
                agentEntry = entry.id
            }
        case "tool_call", "tool_call_update":
            guard let id = update["toolCallId"] as? String else { return }
            let title = (update["title"] as? String).map { Redactor.clean($0) }
            let status = update["status"] as? String
            if let entry = tools[id], let position = entries.firstIndex(where: { $0.id == entry }) {
                if let title { entries[position].text = title }
                if let status { entries[position].status = status }
            } else {
                let entry = Entry(kind: .tool, text: title ?? "Herramienta", status: status)
                entries.append(entry)
                tools[id] = entry.id
            }
            agentEntry = nil
        case "usage_update":
            if let used = update["used"] as? Int, let size = update["size"] as? Int, used >= 0, size > 0 {
                usage = Usage(used: used, size: size)
            }
        default: break
        }
    }

    private func permission(_ params: [String: Any], requestID: Any) {
        guard let sessionID, params["sessionId"] as? String == sessionID, state == .running,
              let call = params["toolCall"] as? [String: Any],
              let options = params["options"] as? [[String: Any]] else {
            outcome(requestID, selected: nil)
            return
        }
        let allow = options.first { $0["kind"] as? String == "allow_once" }?["optionId"] as? String
        let reject = options.first { $0["kind"] as? String == "reject_once" }?["optionId"] as? String
        let title = Redactor.clean(String((call["title"] as? String ?? "Herramienta de Antigravity").prefix(1500)))
        var details: String?
        if let input = call["rawInput"], let data = try? JSONSerialization.data(
            withJSONObject: input, options: [.fragmentsAllowed, .prettyPrinted, .sortedKeys]
        ) {
            let text = Redactor.clean(String(decoding: data, as: UTF8.self))
            details = String(text.prefix(12000)) + (text.count > 12000 ? "…" : "")
        } else if let locations = call["locations"] as? [[String: Any]] {
            details = Redactor.clean(locations.compactMap { $0["path"] as? String }.joined(separator: "\n"))
        }
        let approval = Approval(title: title, details: details, canAllowOnce: allow != nil)
        waiting[approval.id] = PendingApproval(requestID: requestID, allowOnce: allow, rejectOnce: reject)
        approvals.append(approval)
    }

    private func outcome(_ id: Any, selected: String?) {
        let outcome = selected.map { ["outcome": "selected", "optionId": $0] } ?? ["outcome": "cancelled"]
        send(["id": id, "result": ["outcome": outcome]])
    }

    private func cancelApprovals() {
        for pending in waiting.values { outcome(pending.requestID, selected: nil) }
        waiting = [:]
        approvals = []
    }
}
