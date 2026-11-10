import AppKit
import Foundation
import WebKit

/// El navegador propio de una sesión, como el de la app de Codex: un WKWebView (el motor de Safari)
/// con su propio almacén de datos, sin las cookies ni las cuentas del Safari del usuario.
///
/// Ejecuta las peticiones del motor (`perform`) y lo que hace el usuario en el panel. Los permisos
/// los decide el motor; aquí sólo se avisa de lo que los necesita (enviar un formulario, escribir una
/// contraseña) y se bloquea lo que nadie aprobó: direcciones locales, descargas y ventanas nuevas.
@MainActor
public final class BrowserEngine: NSObject {

    // MARK: - Constantes

    /// Tope de una descarga: más grande, se cancela y se borra lo descargado.
    public static let maxDownloadBytes: Int64 = 200 * 1024 * 1024

    // MARK: - Propiedades

    @Published public private(set) var url: URL?
    @Published public private(set) var title = ""
    @Published public private(set) var canGoBack = false
    @Published public private(set) var canGoForward = false
    @Published public private(set) var isLoading = false
    @Published public private(set) var progress = 0.0
    /// Lo que está haciendo el agente ahora («Pulsando «Entrar»»), para el panel.
    @Published public private(set) var activity: String?
    /// Lo último que hizo el agente, aunque ya haya terminado.
    @Published public private(set) var lastActivity: String?
    /// Un aviso para el usuario: una descarga o una dirección bloqueada, un diálogo de la página.
    @Published public private(set) var notice: String?
    /// El panel del navegador está abierto en la ventana de la sesión.
    @Published public var isPanelOpen = false
    /// Selección hecha por la persona; el comentario sólo llega al compositor cuando lo confirma.
    @Published public var isSelectingAnnotation = false
    @Published public var annotation: BrowserAnnotation?
    public var onAnnotation: ((BrowserAnnotation, String) -> Void)?
    var annotationNavigation = 0
    var annotationSelection = 0

    @Published public internal(set) var handoff: BrowserHandoff?
    var handoffContinuation: CheckedContinuation<BrowserReply, Never>?
    var handoffDeadline: Task<Void, Never>?
    /// Relevos en la cola: true si el motor ya los canceló antes de que llegaran a ejecutarse.
    var queuedHandoffs: [String: Bool] = [:]
    /// Tamaño CSS pedido por el motor; nil conserva el tamaño libre del panel.
    @Published public private(set) var viewport: CGSize?

    public let webView: WKWebView

    // MARK: - Propiedades privadas

    private let diagnostics = BrowserDiagnostics()
    private let world = WKContentWorld.world(name: "alfalfa")
    /// Donde corre browser_eval sin permiso: no cambia nada ni lee contraseñas (BrowserScripts.readOnlyWorld).
    private let readingWorld = WKContentWorld.world(name: "alfalfa-lectura")
    /// Orígenes locales o de la red de casa («host:puerto») que aprobó el motor o escribió el usuario.
    private var allowedOrigins: Set<String> = []
    private var started = 0
    private var finished = 0
    /// La navegación que lanzó el agente y espera (sólo esa: las de la página o del usuario no se apuntan).
    private var expected: WKNavigation?
    /// Cómo acabó `expected`: nil mientras carga, .some(nil) si cargó, el error si no.
    private var expectedOutcome: String??
    private var blockedAddress: URL?
    private var blockedDownload: URL?
    private var observations: [NSKeyValueObservation] = []
    /// Una ventana fuera de la pantalla para que la página se pinte (y se pueda capturar) con el panel cerrado.
    private var parking: NSWindow?
    private var downloads: [DownloadWaiter] = []
    /// Las reglas que bloquean los subrecursos locales (BrowserRules) y para qué orígenes se compilaron.
    private var rules: WKContentRuleList?
    private var rulesOrigins: Set<String>?
    /// La última petición del agente: la siguiente espera a que acabe (tras un tiempo agotado en el motor,
    /// dos peticiones no se pisan).
    private var tail: Task<Void, Never>?
    /// `close()`: ya no hace nada.
    public private(set) var isClosed = false

    /// Lo que espera una descarga antes de cancelarla (el motor espera 300 s la respuesta).
    var downloadTimeout: TimeInterval = 280

    // MARK: - Inicializadores

    /// `dataStore`: por defecto uno no persistente, sólo de esta sesión y sólo en memoria.
    public init(dataStore: WKWebsiteDataStore? = nil) {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = dataStore ?? .nonPersistent()
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        configuration.applicationNameForUserAgent = "Version/18.0 Safari/605.1.15 AlfalfaCode"
        // En todos los marcos, antes que nada: un marco sin las barreras sería una salida.
        configuration.userContentController.addUserScript(
            WKUserScript(source: BrowserScripts.readOnlyWorld, injectionTime: .atDocumentStart,
                         forMainFrameOnly: false, in: readingWorld))
        configuration.userContentController.add(diagnostics, name: BrowserDiagnostics.handler)
        configuration.userContentController.addUserScript(
            WKUserScript(source: BrowserDiagnosticsScripts.observe, injectionTime: .atDocumentStart,
                         forMainFrameOnly: true))
        webView = WKWebView(frame: NSRect(x: 0, y: 0, width: 1100, height: 800), configuration: configuration)
        super.init()
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = true
        observations = [
            webView.observe(\.url) { [weak self] view, _ in MainActor.assumeIsolated { self?.url = view.url } },
            webView.observe(\.title) { [weak self] view, _ in
                MainActor.assumeIsolated { self?.title = view.title ?? "" }
            },
            webView.observe(\.canGoBack) { [weak self] view, _ in
                MainActor.assumeIsolated { self?.canGoBack = view.canGoBack }
            },
            webView.observe(\.canGoForward) { [weak self] view, _ in
                MainActor.assumeIsolated { self?.canGoForward = view.canGoForward }
            },
            webView.observe(\.isLoading) { [weak self] view, _ in
                MainActor.assumeIsolated { self?.isLoading = view.isLoading }
            },
            webView.observe(\.estimatedProgress) { [weak self] view, _ in
                MainActor.assumeIsolated { self?.progress = view.estimatedProgress }
            }
        ]
    }

    // MARK: - Métodos

    /// Una petición del agente, de una en una. Abre el panel: el usuario ve lo que hace.
    public func perform(_ request: BrowserRequest) async -> BrowserReply {
        if request.action == "cancelar_relevo" {
            if queuedHandoffs[request.id] != nil { queuedHandoffs[request.id] = true }
            cancelHandoff(id: request.id)
            return .success(request.id, [:])
        }
        if handoff != nil {
            return .failure(request.id, "El navegador está esperando a la persona.")
        }
        if request.action == "relevo" {
            guard queuedHandoffs.count < 100 else {
                return .failure(request.id, "Hay demasiados relevos pendientes del navegador.")
            }
            queuedHandoffs[request.id] = false
        }
        let previous = tail
        let task = Task { @MainActor [weak self] () -> BrowserReply in
            await previous?.value
            guard let self, !self.isClosed else {
                return .failure(request.id, "El navegador de la sesión se cerró.")
            }
            if request.action == "relevo", self.queuedHandoffs.removeValue(forKey: request.id) == true {
                return .failure(request.id, "El motor canceló el relevo antes de que empezara.")
            }
            guard self.handoff == nil else {
                return .failure(request.id, "El navegador está esperando a la persona.")
            }
            return await self.performNow(request)
        }
        tail = Task { _ = await task.value }
        return await task.value
    }

    /// Cierra el navegador para siempre: para la carga, deja la página en blanco, cierra la ventana fuera de
    /// la pantalla y suelta delegados y observadores. Quien lo tiene suelta luego la referencia.
    public func close() {
        guard !isClosed else { return }
        cancelHandoff()
        queuedHandoffs.removeAll()
        isClosed = true
        cancelAnnotation()
        isPanelOpen = false
        activity = nil
        webView.stopLoading()
        webView.loadHTMLString("", baseURL: URL(string: "about:blank"))
        webView.navigationDelegate = nil
        webView.uiDelegate = nil
        webView.removeFromSuperview()
        let controller = webView.configuration.userContentController
        controller.removeAllContentRuleLists()
        controller.removeAllUserScripts()
        controller.removeScriptMessageHandler(forName: BrowserDiagnostics.handler)
        diagnostics.reset()
        observations.removeAll()
        parking?.contentView = nil
        parking?.close()
        parking = nil
    }

    private func performNow(_ request: BrowserRequest) async -> BrowserReply {
        if isSelectingAnnotation { cancelAnnotation() }
        isPanelOpen = true
        host()
        activity = Self.describe(request)
        lastActivity = activity
        defer { activity = nil }
        do {
            if request.action == "abrir" { allowedOrigins.formUnion(request.strings("red_local")) }
            await updateRules()
            if request.action == "relevo" { return await beginHandoff(request) }
            var result = try await run(request)
            if let permission = result.removeValue(forKey: "permiso") as? [String: Any],
               let kind = permission["tipo"] as? String {
                return .permission(request.id, kind: kind, detail: permission["detalle"] as? String ?? "",
                                   result: state())
            }
            result.merge(state()) { mine, _ in mine }
            return .success(request.id, result)
        } catch {
            return .failure(request.id, Self.message(for: error), result: state())
        }
    }

    /// Lo que el usuario escribe en la barra de dirección. Escribirla es su permiso para esa dirección.
    public func userOpen(_ text: String) {
        guard !isClosed else { return }
        guard let url = BrowserAddress.normalized(text) else {
            notice = "«\(text)» no es una dirección http o https."
            return
        }
        notice = nil
        allowedOrigins.insert(BrowserAddress.origin(of: url))
        Task {
            await updateRules()
            webView.load(URLRequest(url: url))
        }
    }

    public func goBack() { webView.goBack() }

    public func goForward() { webView.goForward() }

    public func reload() { if webView.isLoading { webView.stopLoading() } else { webView.reload() } }

    public func dismissNotice() { notice = nil }

    // MARK: - Métodos privados

    /// Pone las reglas de la red local al día con los orígenes aprobados, antes de cargar nada.
    private func updateRules() async {
        guard rulesOrigins != allowedOrigins else { return }
        let origins = allowedOrigins
        do {
            let list = try await BrowserRules.compile(allowing: origins)
            let controller = webView.configuration.userContentController
            if let rules { controller.remove(rules) }
            controller.add(list)
            rules = list
            rulesOrigins = origins
        } catch {
            notice = "No se pudieron preparar las reglas que bloquean la red local: " + error.localizedDescription
        }
    }

    /// La página en la que está: va en todas las respuestas.
    private func state() -> [String: Any] {
        ["url": webView.url?.absoluteString ?? "", "titulo": webView.title ?? ""]
    }

    private func run(_ request: BrowserRequest) async throws -> [String: Any] {
        switch request.action {
        case "abrir":
            guard let text = request.string("url"), let url = URL(string: text),
                  ["http", "https"].contains(url.scheme ?? "") else {
                throw BrowserFailure("El navegador sólo abre direcciones http y https.")
            }
            allowedOrigins.formUnion(request.strings("red_local"))
            return try await navigate { $0.load(URLRequest(url: url, timeoutInterval: 30)) }
        case "atras":
            guard webView.canGoBack else { throw BrowserFailure("No hay página anterior.") }
            return try await navigate { $0.goBack() }
        case "adelante":
            guard webView.canGoForward else { throw BrowserFailure("No hay página siguiente.") }
            return try await navigate { $0.goForward() }
        case "recargar":
            return try await navigate { $0.reload() }
        case "leer":
            return try await evaluate(BrowserScripts.read, request.fields)
        case "consola", "red":
            let limit = (request.fields["limite"] as? Int) ?? BrowserDiagnostics.capacity
            return diagnostics.result(network: request.action == "red", limit: limit)
        case "viewport":
            return try await resizeViewport(request)
        case "pulsar", "escribir":
            return try await interact(request)
        case "desplazar":
            return try await evaluate(BrowserScripts.scroll, request.fields)
        case "captura":
            return try await snapshot(to: request.string("ruta"))
        case "js":
            guard let code = request.string("codigo"), !code.isEmpty else { throw BrowserFailure("Falta el código.") }
            // Con permiso, en la página; sin él, en el mundo de sólo lectura (con sus barreras de nuevo,
            // por si el documento empezó antes de que hubiera script de usuario).
            let modifies = request.bool("modifica")
            var result = try await evaluate((modifies ? "" : BrowserScripts.readOnlyWorld + "\n")
                                            + BrowserScripts.evaluate(code), [:],
                                            in: modifies ? .page : readingWorld)
            if let text = result["valor"] as? String {
                result["valor"] = text.count > 15000 ? String(text.prefix(15000)) + "…" : text
                if text.count > 15000 { result["recortado"] = true }
            }
            if !modifies, let changes = result["cambios_dom"] as? Int, changes > 0 {
                result["nota"] = "El DOM cambió \(changes) veces mientras corría (puede ser la propia página)."
            }
            return result
        case "descargar":
            return try await download(request.string("url"), to: request.string("ruta"))
        case "estado":
            return [:]
        default:
            throw BrowserFailure("Acción del navegador desconocida: \(request.action).")
        }
    }

    private func resizeViewport(_ request: BrowserRequest) async throws -> [String: Any] {
        let size = try BrowserViewport.size(request.fields)
        viewport = size
        if webView.window === parking { parking?.setContentSize(size) }
        webView.setFrameSize(size)
        try await Task.sleep(nanoseconds: 100_000_000)
        return ["ancho": Int(size.width), "alto": Int(size.height),
                "nota": "Sólo cambia el tamaño; no emula un dispositivo ni cambia la identidad del navegador."]
    }

    private func navigate(_ start: (WKWebView) -> WKNavigation?) async throws -> [String: Any] {
        blockedAddress = nil
        blockedDownload = nil
        let startedBefore = started
        let begin = Date()
        expectedOutcome = nil
        guard let navigation = start(webView) else { throw BrowserFailure("No se pudo empezar a cargar la página.") }
        expected = navigation
        defer {
            expected = nil
            expectedOutcome = nil
        }
        let loaded = await waitUntil(timeout: 30) { [unowned self] in
            if expectedOutcome != nil || blockedAddress != nil || blockedDownload != nil { return true }
            // Dentro del mismo documento (#ancla, pushState) no hay didFinish: si en medio segundo no empezó
            // ninguna carga y no está cargando, ya está.
            return Date().timeIntervalSince(begin) > 0.5 && started == startedBefore && !webView.isLoading
        }
        try checkBlocked()
        if case let .some(.some(error)) = expectedOutcome { throw BrowserFailure("No se pudo abrir la página: \(error)") }
        return loaded ? [:] : ["nota": "La página sigue cargando; léela igualmente o espera y vuelve a leerla."]
    }

    private func interact(_ request: BrowserRequest) async throws -> [String: Any] {
        let before = started
        blockedAddress = nil
        blockedDownload = nil
        var result = try await evaluate(request.action == "pulsar" ? BrowserScripts.click : BrowserScripts.type,
                                        request.fields)
        if result["permiso"] != nil { return result }
        if let link = result.removeValue(forKey: "descarga") as? String {
            throw BrowserFailure("Ese enlace descarga un archivo (\(link)). Si hace falta, usa browser_download con "
                                 + "esa url y una ruta del proyecto.")
        }
        // Un clic puede lanzar una navegación un poco después: se espera a que termine.
        _ = await waitUntil(timeout: 0.4) { [unowned self] in started > before }
        if started > before || webView.isLoading {
            _ = await waitUntil(timeout: 15) { [unowned self] in finished >= started && !webView.isLoading }
        }
        try? await Task.sleep(nanoseconds: 150_000_000)
        try checkBlocked()
        result["navego"] = started > before
        return result
    }

    private func checkBlocked() throws {
        if let blockedAddress {
            throw BrowserFailure("La página quiso ir a \(blockedAddress.absoluteString), una dirección local o de la "
                                 + "red de casa que no está aprobada. Ábrela con browser_navigate para preguntar.")
        }
        if let blockedDownload {
            throw BrowserFailure("La página quiso descargar \(blockedDownload.absoluteString); no se descargó. Si hace "
                                 + "falta, usa browser_download.")
        }
    }

    private func evaluate(_ body: String, _ arguments: [String: Any],
                          in contentWorld: WKContentWorld? = nil) async throws -> [String: Any] {
        let value = try await webView.callAsyncJavaScript(body, arguments: ["args": arguments], in: nil,
                                                          contentWorld: contentWorld ?? world)
        return value as? [String: Any] ?? ["valor": value ?? NSNull()]
    }

    /// Espera a que se cumpla la condición, sin bloquear el hilo principal. false si se acabó el tiempo.
    private func waitUntil(timeout: TimeInterval, _ condition: () -> Bool) async -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition() {
            if Date() >= deadline { return false }
            try? await Task.sleep(nanoseconds: 50_000_000)
        }
        return true
    }

    /// Sin panel, la página vive en una ventana fuera de la pantalla: se sigue pintando y capturando.
    private func host() {
        guard webView.window == nil, !isClosed else { return }
        let window = parking ?? NSWindow(contentRect: NSRect(x: -20000, y: -20000, width: 1100, height: 800),
                                         styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.ignoresMouseEvents = true
        let content = window.contentView ?? NSView()
        window.contentView = content
        if let viewport { window.setContentSize(viewport) }
        webView.frame = content.bounds
        webView.autoresizingMask = [.width, .height]
        content.addSubview(webView)
        window.orderBack(nil)
        parking = window
    }

}

// MARK: - ObservableObject

extension BrowserEngine: ObservableObject {}

// MARK: - Captura, descarga y textos

extension BrowserEngine {
    private func snapshot(to path: String?) async throws -> [String: Any] {
        guard let path, path.hasPrefix("/"), path.hasSuffix(".png") else {
            throw BrowserFailure("La captura necesita una ruta absoluta .png.")
        }
        let configuration = WKSnapshotConfiguration()
        configuration.snapshotWidth = NSNumber(value: Double(webView.bounds.width))
        let image: NSImage = try await withCheckedThrowingContinuation { continuation in
            webView.takeSnapshot(with: configuration) { image, error in
                if let image { continuation.resume(returning: image) } else {
                    continuation.resume(throwing: error ?? BrowserFailure("No se pudo hacer la captura."))
                }
            }
        }
        guard let png = Self.png(of: image, maxSide: Self.maxScreenshotSide) else {
            throw BrowserFailure("No se pudo convertir la captura a PNG.")
        }
        try png.data.write(to: URL(fileURLWithPath: path), options: .atomic)
        return ["ancho": png.width, "alto": png.height]
    }

    /// El lado más largo de una captura: el tope con el que los modelos de visión no la reducen otra vez.
    static let maxScreenshotSide = 1568.0

    /// La imagen a 1x (un píxel por punto, no los 2x de una pantalla Retina), reducida para que su lado
    /// más largo no pase de `maxSide`.
    static func png(of image: NSImage, maxSide: Double) -> (data: Data, width: Int, height: Int)? {
        let size = image.size
        guard size.width > 0, size.height > 0 else { return nil }
        let scale = min(1, maxSide / max(size.width, size.height))
        let width = max(1, Int((size.width * scale).rounded()))
        let height = max(1, Int((size.height * scale).rounded()))
        guard let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: width, pixelsHigh: height,
                                            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
              let context = NSGraphicsContext(bitmapImageRep: bitmap) else { return nil }
        bitmap.size = NSSize(width: width, height: height)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = context
        context.imageInterpolation = .high
        image.draw(in: NSRect(x: 0, y: 0, width: width, height: height), from: .zero, operation: .copy, fraction: 1)
        NSGraphicsContext.restoreGraphicsState()
        guard let data = bitmap.representation(using: .png, properties: [:]) else { return nil }
        return (data, width, height)
    }

    private func download(_ text: String?, to path: String?) async throws -> [String: Any] {
        guard let text, let url = URL(string: text), ["http", "https"].contains(url.scheme ?? "") else {
            throw BrowserFailure("Sólo se descarga de direcciones http o https.")
        }
        if let reason = Self.downloadBlockReason(for: url) { throw BrowserFailure(reason) }
        guard let path, path.hasPrefix("/"), !FileManager.default.fileExists(atPath: path) else {
            throw BrowserFailure("La descarga necesita una ruta absoluta que no exista.")
        }
        let destination = URL(fileURLWithPath: path)
        let waiter = DownloadWaiter(destination: destination, limit: Self.maxDownloadBytes)
        downloads.append(waiter)
        defer { downloads.removeAll { $0 === waiter } }
        let download = await webView.startDownload(using: URLRequest(url: url))
        do {
            try await waiter.wait(for: download, timeout: downloadTimeout)
        } catch {
            // Lo que se quedó a medias no se deja en el proyecto.
            try? FileManager.default.removeItem(at: destination)
            throw error
        }
        return ["ruta": path]
    }

    /// Por qué no se descarga de esa dirección, o nil si se puede: sólo de direcciones públicas, también
    /// después de cada redirección. El motor ya lo mira antes de preguntar (navegador.destino_resuelto).
    static func downloadBlockReason(for url: URL) -> String? {
        guard ["http", "https"].contains(url.scheme ?? "") else {
            return "Sólo se descarga de direcciones http o https (\(url.absoluteString))."
        }
        guard BrowserAddress.kind(of: url) == .publicInternet else {
            return "Sólo se descarga de direcciones públicas, no de este Mac ni de la red de casa "
                + "(\(url.absoluteString))."
        }
        return nil
    }

    private static func describe(_ request: BrowserRequest) -> String {
        let target = request.string("ref") ?? request.string("objetivo").map { "«\($0)»" } ?? ""
        switch request.action {
        case "abrir": return "Abriendo " + (request.string("url") ?? "")
        case "atras": return "Volviendo a la página anterior"
        case "adelante": return "Yendo a la página siguiente"
        case "recargar": return "Recargando la página"
        case "leer": return "Leyendo la página"
        case "consola": return "Leyendo la consola de la página"
        case "red": return "Leyendo las peticiones de red de la página"
        case "viewport": return "Cambiando el tamaño de la página"
        case "pulsar": return "Pulsando " + target
        case "escribir": return "Escribiendo en " + target
        case "desplazar": return "Desplazando la página"
        case "captura": return "Haciendo una captura"
        case "js":
            return request.bool("modifica") ? "Ejecutando JavaScript que cambia la página" : "Ejecutando JavaScript"
        case "relevo": return "Esperando a la persona"
        case "descargar": return "Descargando " + (request.string("url") ?? "")
        default: return request.action
        }
    }

    private static func message(for error: Error) -> String {
        if let failure = error as? BrowserFailure { return failure.message }
        let nsError = error as NSError
        if let detail = nsError.userInfo["WKJavaScriptExceptionMessage"] as? String {
            return "Error de JavaScript: " + detail
        }
        return nsError.localizedDescription
    }
}

// MARK: - WKNavigationDelegate

extension BrowserEngine: WKNavigationDelegate {
    public func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                        decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { return decisionHandler(.cancel) }
        if action.shouldPerformDownload {
            blockedDownload = url
            notice = "Se bloqueó una descarga de \(url.host ?? url.absoluteString)."
            return decisionHandler(.cancel)
        }
        if action.targetFrame?.isMainFrame ?? true {
            let scheme = url.scheme ?? ""
            guard ["http", "https", "about", "blob", "data"].contains(scheme) else { return decisionHandler(.cancel) }
            if scheme == "http" || scheme == "https", BrowserAddress.kind(of: url) != .publicInternet,
               !allowedOrigins.contains(BrowserAddress.origin(of: url)) {
                blockedAddress = url
                notice = "Se bloqueó \(url.absoluteString): es una dirección local o de la red de casa."
                return decisionHandler(.cancel)
            }
        }
        decisionHandler(.allow)
    }

    public func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                        decisionHandler: @escaping @MainActor (WKNavigationResponsePolicy) -> Void) {
        let http = response.response as? HTTPURLResponse
        let disposition = http?.value(forHTTPHeaderField: "Content-Disposition") ?? ""
        if response.isForMainFrame, !response.canShowMIMEType || disposition.lowercased().hasPrefix("attachment") {
            blockedDownload = response.response.url
            notice = "Se bloqueó una descarga de \(response.response.url?.host ?? "la página")."
            return decisionHandler(.cancel)
        }
        decisionHandler(.allow)
    }

    public func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        diagnostics.reset()
        annotationNavigation += 1
        cancelAnnotation()
        started += 1
    }

    public func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        finished += 1
        if let navigation, navigation === expected { expectedOutcome = .some(nil) }
    }

    public func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        failed(navigation, error)
    }

    public func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                        withError error: Error) {
        failed(navigation, error)
    }

    private func failed(_ navigation: WKNavigation?, _ error: Error) {
        let nsError = error as NSError
        // Una navegación que sustituye a otra (o que cancelamos nosotros) no es un fallo de la página.
        let replaced = (nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorCancelled)
            || (nsError.domain == "WebKitErrorDomain" && nsError.code == 102)
        finished += 1
        if let navigation, navigation === expected {
            expectedOutcome = .some(replaced ? nil : nsError.localizedDescription)
        }
    }
}

// MARK: - WKUIDelegate

extension BrowserEngine: WKUIDelegate {
    /// Una ventana nueva (target=_blank, window.open) se abre en el mismo panel.
    public func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                        for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if action.targetFrame == nil, let url = action.request.url { webView.load(URLRequest(url: url)) }
        return nil
    }

    public func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                        initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping @MainActor () -> Void) {
        notice = "La página dijo: " + String(message.prefix(200))
        completionHandler()
    }

    public func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                        initiatedByFrame frame: WKFrameInfo,
                        completionHandler: @escaping @MainActor (Bool) -> Void) {
        notice = "La página preguntó «\(String(message.prefix(200)))» y se respondió que no."
        completionHandler(false)
    }

    public func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String,
                        defaultText: String?, initiatedByFrame frame: WKFrameInfo,
                        completionHandler: @escaping @MainActor (String?) -> Void) {
        notice = "La página pidió un texto («\(String(prompt.prefix(200)))») y se canceló."
        completionHandler(nil)
    }
}

// MARK: - Tipos auxiliares

struct BrowserFailure: Error {
    let message: String
    init(_ message: String) { self.message = message }
}

/// Espera una descarga del navegador hasta que queda en su destino. La cancela si pasa del tope de
/// tamaño o de tiempo, o si una redirección lleva a una dirección que no es pública.
@MainActor
private final class DownloadWaiter: NSObject {
    private let destination: URL
    private let limit: Int64
    private var continuation: CheckedContinuation<Void, Error>?
    private var download: WKDownload?
    private var observation: NSKeyValueObservation?

    init(destination: URL, limit: Int64) {
        self.destination = destination
        self.limit = limit
    }

    func wait(for download: WKDownload, timeout: TimeInterval) async throws {
        self.download = download
        let timer = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(max(0, timeout) * 1_000_000_000))
            guard !Task.isCancelled else { return }
            self?.fail(BrowserFailure("La descarga tardó más de \(Int(timeout)) s: se canceló y no quedó nada."))
        }
        defer {
            timer.cancel()
            observation = nil
        }
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            self.continuation = continuation
            download.delegate = self
            observation = download.progress.observe(\.completedUnitCount) { [weak self] progress, _ in
                let received = progress.completedUnitCount
                DispatchQueue.main.async { MainActor.assumeIsolated { self?.check(received) } }
            }
        }
    }

    private func check(_ received: Int64) {
        if received > limit { fail(tooBig) }
    }

    private var tooBig: BrowserFailure {
        BrowserFailure("La descarga pasa de \(limit / 1_048_576) MB: se canceló y no quedó nada.")
    }

    /// Termina la espera con un error y cancela la descarga. Lo que llegue después se ignora.
    private func fail(_ error: Error) {
        guard let continuation else { return }
        self.continuation = nil
        download?.cancel { _ in }
        continuation.resume(throwing: error)
    }
}

// MARK: - WKDownloadDelegate

extension DownloadWaiter: WKDownloadDelegate {
    func download(_ download: WKDownload, willPerformHTTPRedirection response: HTTPURLResponse,
                  newRequest request: URLRequest,
                  decisionHandler: @escaping @MainActor (WKDownload.RedirectPolicy) -> Void) {
        if let url = request.url, let reason = BrowserEngine.downloadBlockReason(for: url) {
            decisionHandler(.cancel)
            fail(BrowserFailure("La descarga redirigía a otra dirección. " + reason))
            return
        }
        decisionHandler(.allow)
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping @MainActor (URL?) -> Void) {
        if let url = response.url, let reason = BrowserEngine.downloadBlockReason(for: url) {
            completionHandler(nil)
            fail(BrowserFailure(reason))
            return
        }
        if response.expectedContentLength > limit {
            completionHandler(nil)
            fail(tooBig)
            return
        }
        completionHandler(destination)
    }

    func downloadDidFinish(_ download: WKDownload) {
        continuation?.resume()
        continuation = nil
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        continuation?.resume(throwing: error)
        continuation = nil
    }
}
