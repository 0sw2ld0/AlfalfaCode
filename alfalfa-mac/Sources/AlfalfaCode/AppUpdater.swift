import AlfalfaKit
import AppKit
import SwiftUI

// El actualizador de AlfalfaCode.app, el mismo que el de CuyCleaner: lee version.json de la última
// release de GitHub, descarga el DMG, comprueba su huella SHA-256, que la app viene firmada por
// CuyCoders (mismo Team ID) y notarizada por Apple, y sólo entonces la cambia por la actual y la
// vuelve a abrir. Si algo no cuadra, no instala nada. Con `swift run` desde un clon no se usa: allí
// actualiza `alfalfa --actualizar` (AppModel.checkForUpdate).

@MainActor
final class AppUpdater: ObservableObject {

    // MARK: - Tipos

    enum State: Equatable {
        case idle
        case checking
        case upToDate
        case available(ReleaseInfo)
        case downloading(ReleaseInfo, Double)
        case verifying(ReleaseInfo, step: Int)    // 0 huella · 1 firma · 2 notarización · 3 instalando
        case failed(String, ReleaseInfo?)
    }

    // MARK: - Constantes

    static let shared = AppUpdater()
    private static let autoKey = "actualizaciones.auto"
    private static let lastKey = "actualizaciones.ultima"
    private static let skippedKey = "actualizaciones.omitida"

    /// La app empaquetada por build-app.sh (no `swift run`): es la única que se cambia a sí misma.
    static let isPackaged = Bundle.main.bundleURL.pathExtension == "app"
        && Bundle.main.bundleIdentifier == AppUpdatePolicy.bundleIdentifier

    nonisolated static var installedVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
    }

    // MARK: - Propiedades

    @Published private(set) var state: State = .idle
    @Published var showWindow = false
    @Published var automatic: Bool { didSet { UserDefaults.standard.set(automatic, forKey: Self.autoKey) } }
    @Published private(set) var lastCheck = UserDefaults.standard.object(forKey: AppUpdater.lastKey) as? Date

    private let feed = AppUpdatePolicy.feed(environment: ProcessInfo.processInfo.environment)

    var release: ReleaseInfo? {
        switch state {
        case let .available(r), let .downloading(r, _), let .verifying(r, _): return r
        case let .failed(_, r): return r
        default: return nil
        }
    }

    var busy: Bool {
        switch state {
        case .checking, .downloading, .verifying: return true
        default: return false
        }
    }

    // MARK: - Inicializadores

    private init() {
        UserDefaults.standard.register(defaults: [Self.autoKey: true])
        automatic = UserDefaults.standard.bool(forKey: Self.autoKey)
    }

    // MARK: - Buscar

    /// Al abrir la app: como mucho una vez cada 20 horas, en silencio; sólo avisa si hay algo nuevo.
    /// Con `-actualizarAhora` busca e instala sin preguntar (para probar el circuito completo).
    func checkIfDue() async {
        guard Self.isPackaged else { return }
        if CommandLine.arguments.contains("-actualizarAhora") {
            showWindow = true
            await check(manual: true)
            if case let .available(release) = state { await install(release) }
            return
        }
        guard automatic, AppUpdatePolicy.isDue(lastCheck: lastCheck) else { return }
        try? await Task.sleep(for: .seconds(8))
        await check(manual: false)
    }

    func checkManually() {
        showWindow = true
        Task { await check(manual: true) }
    }

    func check(manual: Bool) async {
        guard !busy || state == .checking else { return }
        state = .checking
        do {
            var request = URLRequest(url: feed, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 20)
            request.setValue("AlfalfaCode/\(Self.installedVersion)", forHTTPHeaderField: "User-Agent")
            let (data, response) = try await URLSession.shared.data(for: request)
            if let http = response as? HTTPURLResponse, http.statusCode != 200 { throw UpdateError.server }
            let release = try JSONDecoder().decode(ReleaseInfo.self, from: data)
            lastCheck = Date()
            UserDefaults.standard.set(lastCheck, forKey: Self.lastKey)
            let skipped = UserDefaults.standard.string(forKey: Self.skippedKey)
            if AppUpdatePolicy.shouldOffer(release, installed: Self.installedVersion, skipped: skipped, manual: manual,
                                           feed: feed) {
                state = .available(release)
                showWindow = true
            } else {
                state = manual ? .upToDate : .idle
            }
        } catch {
            state = manual ? .failed("No se pudo comprobar: revisa tu conexión a internet.", nil) : .idle
        }
    }

    func skip(_ release: ReleaseInfo) {
        UserDefaults.standard.set(release.version, forKey: Self.skippedKey)
        showWindow = false
        state = .idle
    }

    /// Cierra la ventana. Una versión disponible se queda (Ajustes ofrece instalarla); una descarga
    /// en curso sigue; lo demás vuelve a reposo.
    func dismiss() {
        showWindow = false
        if case .available = state { return }
        if !busy { state = .idle }
    }

    // MARK: - Instalar

    func install(_ release: ReleaseInfo) async {
        let current = Bundle.main.bundleURL
        if AppUpdatePolicy.isTranslocated(current) {
            state = .failed(UpdateError.translocated.message, release)
            return
        }
        state = .downloading(release, 0)
        do {
            let dmg = try await download(release)
            state = .verifying(release, step: 0)
            try await Task.detached { try Self.checkDigest(dmg, expected: release.sha256) }.value
            state = .verifying(release, step: 1)
            let fresh = try await Task.detached {
                try Self.prepareApp(dmg: dmg, version: release.version, beside: current) { step in
                    Task { @MainActor in AppUpdater.shared.state = .verifying(release, step: step) }
                }
            }.value
            state = .verifying(release, step: 3)
            try? await Task.sleep(for: .seconds(0.8))
            replaceAndRelaunch(with: fresh, current: current)
        } catch {
            state = .failed((error as? UpdateError)?.message ?? error.localizedDescription, release)
        }
    }

    private func download(_ release: ReleaseInfo) async throws -> URL {
        Self.removeOldDownloads()
        let target = FileManager.default.temporaryDirectory
            .appendingPathComponent("AlfalfaCode-\(release.version)-\(UUID().uuidString).dmg")
        var last = Date.distantPast
        let downloaded = try await Download.fetch(release.url, total: release.tamano ?? 0) { fraction in
            Task { @MainActor in
                guard Date().timeIntervalSince(last) > 0.1 || fraction >= 1 else { return }
                last = Date()
                AppUpdater.shared.state = .downloading(release, fraction)
            }
        }
        try FileManager.default.moveItem(at: downloaded, to: target)
        state = .downloading(release, 1)
        return target
    }

    /// DMG a medias de intentos anteriores: son temporales nuestros y se quitan directamente.
    nonisolated private static func removeOldDownloads() {
        let tmp = FileManager.default.temporaryDirectory
        for name in (try? FileManager.default.contentsOfDirectory(atPath: tmp.path)) ?? []
        where name.hasSuffix(".dmg") && (name.hasPrefix("AlfalfaCode-") || name.hasPrefix("descarga-alfalfa-")) {
            try? FileManager.default.removeItem(at: tmp.appendingPathComponent(name))
        }
    }

    nonisolated private static func checkDigest(_ file: URL, expected: String) throws {
        guard try AppUpdatePolicy.sha256(of: file) == expected.lowercased() else { throw UpdateError.digest }
    }

    /// Monta el DMG; comprueba firma, equipo, identificador, versión y notarización; y copia la app
    /// junto a la actual con un nombre temporal. Devuelve esa copia.
    nonisolated private static func prepareApp(dmg: URL, version: String, beside current: URL,
                                               progress: @escaping @Sendable (Int) -> Void) throws -> URL {
        let mount = FileManager.default.temporaryDirectory.appendingPathComponent("alfalfa-act-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: mount, withIntermediateDirectories: true)
        guard run("/usr/bin/hdiutil", ["attach", dmg.path, "-nobrowse", "-readonly", "-mountpoint", mount.path],
                  timeout: 120).status == 0 else { throw UpdateError.content }
        defer {
            _ = run("/usr/bin/hdiutil", ["detach", mount.path, "-force"], timeout: 60)
            try? FileManager.default.removeItem(at: dmg)
            try? FileManager.default.removeItem(at: mount)
        }
        let app = mount.appendingPathComponent("AlfalfaCode.app")
        guard FileManager.default.fileExists(atPath: app.path) else { throw UpdateError.content }

        // Firma íntegra y del mismo equipo.
        guard run("/usr/bin/codesign", ["--verify", "--deep", "--strict", app.path], timeout: 300).status == 0,
              AppUpdatePolicy.signedByTeam(codesignDetails: run("/usr/bin/codesign", ["-dv", app.path]).output)
        else { throw UpdateError.signature }
        let info = NSDictionary(contentsOf: app.appendingPathComponent("Contents/Info.plist"))
        guard info?["CFBundleIdentifier"] as? String == AppUpdatePolicy.bundleIdentifier,
              info?["CFBundleShortVersionString"] as? String == version else { throw UpdateError.content }
        progress(2)

        // Notarización: la misma comprobación que hace Gatekeeper al abrirla. La app lleva el ticket
        // grapado; si macOS tuviera que preguntar a Apple y fallara a la primera, se reintenta una vez.
        func notarized() -> Bool { run("/usr/sbin/spctl", ["--assess", "--type", "execute", app.path], timeout: 120).status == 0 }
        guard notarized() || { Thread.sleep(forTimeInterval: 3); return notarized() }() else {
            throw UpdateError.notarization
        }

        let copy = current.deletingLastPathComponent().appendingPathComponent(".AlfalfaCode-\(version)-nueva.app")
        try? FileManager.default.removeItem(at: copy)
        guard run("/usr/bin/ditto", [app.path, copy.path], timeout: 300).status == 0 else { throw UpdateError.permissions }
        return copy
    }

    /// Un proceso aparte espera a que esta app se cierre (cerrando antes las sesiones), cambia una
    /// carpeta por otra y abre la nueva. Si el cambio falla, se restaura la anterior.
    private func replaceAndRelaunch(with fresh: URL, current: URL) {
        let old = current.deletingLastPathComponent().appendingPathComponent(".AlfalfaCode-anterior.app").path
        let script = """
        while /bin/kill -0 \(getpid()) 2>/dev/null; do /bin/sleep 0.2; done
        /bin/rm -rf "$VIEJA"
        if /bin/mv "$ACTUAL" "$VIEJA" && /bin/mv "$NUEVA" "$ACTUAL"; then /bin/rm -rf "$VIEJA"; else /bin/mv "$VIEJA" "$ACTUAL"; fi
        /usr/bin/open "$ACTUAL"
        """
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/sh")
        process.arguments = ["-c", script]
        process.environment = ["VIEJA": old, "ACTUAL": current.path, "NUEVA": fresh.path]
        try? process.run()
        showWindow = false
        UserDefaults.standard.synchronize()
        // Con una hoja abierta macOS puede ignorar la salida: si la app sigue viva a los 3 s, se sale.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { NSApp.terminate(nil) }
        DispatchQueue.main.asyncAfter(deadline: .now() + 3.5) { exit(0) }
    }

    /// Ejecuta sin shell y devuelve el código de salida y lo que escribió (stdout y stderr juntos:
    /// `codesign -dv` escribe en stderr). Se espera con un semáforo, nunca en el hilo principal.
    nonisolated private static func run(_ path: String, _ arguments: [String],
                                        timeout: TimeInterval = 60) -> (status: Int32, output: String) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: path)
        process.arguments = arguments
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        process.standardInput = FileHandle.nullDevice
        let done = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in done.signal() }
        guard (try? process.run()) != nil else { return (-1, "") }
        var data = Data()
        let reading = DispatchGroup()
        DispatchQueue.global(qos: .utility).async(group: reading) { data = pipe.fileHandleForReading.readDataToEndOfFile() }
        if done.wait(timeout: .now() + timeout) == .timedOut {
            process.terminate()
            _ = done.wait(timeout: .now() + 5)
            return (-1, "")
        }
        reading.wait()
        return (process.terminationStatus, String(decoding: data, as: UTF8.self))
    }
}

enum UpdateError: Error {
    case server, digest, content, signature, notarization, permissions, translocated

    var message: String {
        switch self {
        case .server: return "El servidor de actualizaciones no respondió. Inténtalo más tarde."
        case .digest: return "La descarga no coincide con la huella publicada. No se instaló nada."
        case .content: return "El archivo descargado no contiene la versión esperada. No se instaló nada."
        case .signature: return "La app descargada no está firmada por CuyCoders. No se instaló nada."
        case .notarization: return "Apple no ha validado (notarizado) esta descarga. No se instaló nada."
        case .permissions:
            return "No hay permiso para escribir en la carpeta de la app. Descárgala desde GitHub y arrástrala a Aplicaciones."
        case .translocated:
            return "macOS abrió AlfalfaCode desde una copia temporal. Arrástrala a Aplicaciones con Finder, ábrela desde allí y vuelve a intentarlo."
        }
    }
}

// MARK: - Descarga

/// URLSessionDownloadTask con reintentos: hasta 5 intentos, siguiendo donde se quedó si el servidor
/// lo permite. GitHub redirige a su almacén de archivos y URLSession sigue la redirección.
private final class Download: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    private let progress: @Sendable (Double) -> Void
    private let total: Int64
    private var continuation: CheckedContinuation<URL, Error>?
    private var destination: URL?

    private init(total: Int64, progress: @escaping @Sendable (Double) -> Void) {
        self.total = total
        self.progress = progress
    }

    static func fetch(_ url: URL, total: Int64, progress: @escaping @Sendable (Double) -> Void) async throws -> URL {
        let download = Download(total: total, progress: progress)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 60
        configuration.timeoutIntervalForResource = 30 * 60
        configuration.waitsForConnectivity = true
        let session = URLSession(configuration: configuration, delegate: download, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        var resumeData: Data?
        var lastError: Error = URLError(.unknown)
        for attempt in 0..<5 {
            if attempt > 0 { try await Task.sleep(for: .seconds(Double(attempt) * 2)) }
            do {
                return try await withCheckedThrowingContinuation { continuation in
                    download.continuation = continuation
                    var request = URLRequest(url: url)
                    request.setValue("AlfalfaCode/\(AppUpdater.installedVersion)", forHTTPHeaderField: "User-Agent")
                    let task = resumeData.map { session.downloadTask(withResumeData: $0) } ?? session.downloadTask(with: request)
                    task.resume()
                }
            } catch {
                lastError = error
                resumeData = (error as? URLError)?.downloadTaskResumeData
                if error is UpdateError || Task.isCancelled { throw error }
            }
        }
        throw lastError
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData _: Int64,
                    totalBytesWritten written: Int64, totalBytesExpectedToWrite expected: Int64) {
        let size = max(expected, total)
        if size > 0 { progress(min(1, Double(written) / Double(size))) }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        // El archivo temporal desaparece al volver de aquí: se mueve ya. Un DMG local (prueba) no
        // tiene respuesta HTTP.
        if let http = downloadTask.response as? HTTPURLResponse, http.statusCode != 200 { destination = nil; return }
        let copy = FileManager.default.temporaryDirectory.appendingPathComponent("descarga-alfalfa-\(UUID().uuidString).dmg")
        destination = (try? FileManager.default.moveItem(at: location, to: copy)) != nil ? copy : nil
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let continuation = continuation
        self.continuation = nil
        if let error { continuation?.resume(throwing: error) }
        else if let destination { self.destination = nil; continuation?.resume(returning: destination) }
        else { continuation?.resume(throwing: UpdateError.server) }
    }
}

// MARK: - Ventana

struct UpdateWindow: View {
    @EnvironmentObject private var updater: AppUpdater

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 14) {
                Image(nsImage: NSApp.applicationIconImage).resizable().frame(width: 56, height: 56)
                VStack(alignment: .leading, spacing: 3) {
                    Text(title).font(.title2.bold())
                    Text("Tienes la versión \(AppUpdater.installedVersion)").font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
            }
            content
        }
        .padding(24)
        .frame(width: 480)
    }

    private var title: String {
        switch updater.state {
        case .checking, .idle: return "Buscando actualizaciones…"
        case .upToDate: return "AlfalfaCode está al día"
        case let .available(r): return "AlfalfaCode \(r.version) disponible"
        case let .downloading(r, _), let .verifying(r, _): return "Instalando AlfalfaCode \(r.version)"
        case .failed: return "No se pudo actualizar"
        }
    }

    @ViewBuilder private var content: some View {
        switch updater.state {
        case .checking, .idle:
            ProgressView().controlSize(.small)
        case .upToDate:
            Label("Ya tienes la última versión.", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
            buttons { Spacer(); Button("Aceptar") { updater.dismiss() }.keyboardShortcut(.defaultAction) }
        case let .available(release):
            notes(release)
            buttons {
                Button("Omitir esta versión") { updater.skip(release) }
                Spacer()
                Button("Más tarde") { updater.dismiss() }
                Button("Instalar y reiniciar") { Task { await updater.install(release) } }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
            }
        case let .downloading(release, fraction):
            ProgressView(value: fraction) {
                Text("Descargando… \(Int(fraction * 100)) %").font(.callout)
            } currentValueLabel: {
                if let size = release.tamano {
                    Text("\(bytes(Int64(Double(size) * fraction))) de \(bytes(size))").font(.caption)
                }
            }
            steps(0)
        case let .verifying(_, step):
            ProgressView(value: 1).tint(.green)
            steps(step + 1)
        case let .failed(message, _):
            Label(message, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                .fixedSize(horizontal: false, vertical: true)
            buttons {
                Button("Descargar desde GitHub") { NSWorkspace.shared.open(AppUpdatePolicy.releasesPage) }
                Spacer()
                Button("Cerrar") { updater.dismiss() }.keyboardShortcut(.defaultAction)
            }
        }
    }

    private func notes(_ release: ReleaseInfo) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if let notes = release.notas, !notes.isEmpty {
                Text("Novedades").font(.headline)
                ForEach(notes, id: \.self) { note in
                    Label { Text(note).fixedSize(horizontal: false, vertical: true) } icon: {
                        Image(systemName: "leaf").foregroundStyle(.green)
                    }.font(.callout)
                }
            }
            Text("Firmada por CuyCoders y notarizada por Apple"
                 + (release.tamano.map { ". Descarga de \(bytes($0))." } ?? ".")
                 + " Las sesiones abiertas se cierran y se pueden reanudar después.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
    }

    /// Lista de comprobaciones que se van marcando: se ve qué se verifica y en qué orden.
    private func steps(_ done: Int) -> some View {
        let list = ["Descargar", "Comprobar huella SHA-256", "Verificar firma de CuyCoders",
                    "Verificar notarización de Apple", "Instalar y reiniciar"]
        return VStack(alignment: .leading, spacing: 7) {
            ForEach(Array(list.enumerated()), id: \.offset) { index, text in
                HStack(spacing: 8) {
                    Group {
                        if index < done {
                            Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                        } else if index == done {
                            ProgressView().controlSize(.mini)
                        } else {
                            Image(systemName: "circle").foregroundStyle(.tertiary)
                        }
                    }
                    .frame(width: 16)
                    Text(text).font(.callout).foregroundStyle(index <= done ? .primary : .secondary)
                }
            }
        }
    }

    private func buttons<C: View>(@ViewBuilder _ content: () -> C) -> some View {
        HStack { content() }.padding(.top, 4)
    }

    private func bytes(_ value: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: value, countStyle: .file)
    }
}

/// Ajustes ▸ Actualizaciones en la app empaquetada.
struct AppUpdaterSettings: View {
    @EnvironmentObject private var updater: AppUpdater

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("AlfalfaCode \(AppUpdater.installedVersion)").font(.system(size: 13, weight: .semibold))
                    Text(updater.lastCheck.map { "Última comprobación: " + $0.formatted(.relative(presentation: .named)) }
                         ?? "Aún no se ha comprobado.")
                        .font(.system(size: 12)).foregroundStyle(Theme.secondary)
                }
                Spacer()
                if let release = updater.release, !updater.busy {
                    Button("Instalar \(release.version)…") { updater.showWindow = true }.buttonStyle(.borderedProminent)
                } else {
                    Button("Buscar actualización") { updater.checkManually() }.disabled(updater.busy)
                }
            }
            Toggle("Buscar actualizaciones automáticamente (como mucho una vez al día)", isOn: $updater.automatic)
            Text("Sólo se instala una versión firmada por CuyCoders y notarizada por Apple, y siempre con tu permiso.")
                .font(.system(size: 12)).foregroundStyle(Theme.secondary)
        }
    }
}
