import Foundation

public enum ClaudeCodeLaunch {
    public enum Model: String {
        case haiku, sonnet, opus
    }

    public enum Effort: String {
        case low, medium, high, xhigh, max
    }

    public static func executable() -> URL? {
        let files = FileManager.default
        let paths = (ProcessInfo.processInfo.environment["PATH"] ?? "")
            .split(separator: ":").filter { $0.hasPrefix("/") }.map(String.init)
        let nativeBin = files.homeDirectoryForCurrentUser.appendingPathComponent(".local/bin").path
        return ([nativeBin] + paths + ["/opt/homebrew/bin", "/usr/local/bin"])
            .map { URL(fileURLWithPath: $0, isDirectory: true).appendingPathComponent("claude") }
            .first { files.isExecutableFile(atPath: $0.path) }
    }

    public static func session(executable: URL, folder: URL, model: Model?, effort: Effort?) -> String {
        var arguments = [
            "--strict-mcp-config", "--mcp-config", #"{"mcpServers":{}}"#,
            "--disallowedTools", "mcp__*", "--no-chrome", "--permission-mode", "default"
        ]
        if let model { arguments += ["--model", model.rawValue] }
        if let effort { arguments += ["--effort", effort.rawValue] }
        let command = ([executable.path] + arguments).map(CodeActions.shellQuote).joined(separator: " ")
        return """
        #!/bin/sh
        /bin/rm -f -- "$0"
        cd \(CodeActions.shellQuote(folder.path)) || exit 1
        export ENABLE_CLAUDEAI_MCP_SERVERS=false
        exec \(command)
        """
    }

    public static func signIn(executable: URL) -> String {
        let command = CodeActions.shellQuote(executable.path)
        return """
        #!/bin/sh
        /bin/rm -f -- "$0"
        cd "$HOME" || exit 1
        \(command) auth login && \(command) auth status --text
        """
    }
}
