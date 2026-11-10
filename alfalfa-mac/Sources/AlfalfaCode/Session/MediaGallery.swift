import AlfalfaKit
import AppKit
import AVKit
import ImageIO
import Quartz
import SwiftUI
import UniformTypeIdentifiers

/// Las imágenes y vídeos locales que el agente cita en su respuesta (`![texto](ruta)`), como
/// en la app de Codex: una sola va al ancho del mensaje; varias seguidas, en cuadrícula. Un
/// clic la abre en grande con Vista rápida y el menú contextual la muestra en Finder o la copia.
struct MediaGallery: View {

    // MARK: - Propiedades

    let items: [MarkdownMedia]

    var body: some View {
        if items.count == 1, let item = items.first {
            MediaTile(item: item, gallery: items, compact: false)
        } else {
            let count = items.count == 4 ? 2 : min(items.count, 3)
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 10, alignment: .top), count: count),
                      alignment: .leading, spacing: 12) {
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    MediaTile(item: item, gallery: items, compact: true)
                }
            }
        }
    }
}

/// Una imagen o un vídeo con su pie de foto.
private struct MediaTile: View {

    // MARK: - Constantes

    /// Alto máximo de una imagen sola y alto de cada celda de la cuadrícula.
    static let maximumHeight: CGFloat = 420
    static let cellHeight: CGFloat = 180

    // MARK: - Propiedades

    let item: MarkdownMedia
    let gallery: [MarkdownMedia]
    let compact: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Group {
                if missing {
                    MissingMedia(item: item)
                } else if item.kind == .video {
                    InlineVideo(item: item, compact: compact, missing: $missing)
                } else if let loaded {
                    picture(loaded)
                } else {
                    loading
                }
            }
            .help(item.source)
            // Los controles del reproductor siguen siendo accesibles; una imagen es un solo elemento.
            .accessibilityElement(children: item.kind == .image ? .ignore : .contain)
            .accessibilityLabel(label)
            .accessibilityAddTraits(item.kind == .image ? .isImage : [])
            caption
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .task(id: item.url) {
            guard item.kind == .image else { return }
            if let cached = MediaLoader.cached(item.url) { loaded = cached; return }
            if let image = await MediaLoader.load(item.url) { loaded = image } else { missing = true }
        }
    }

    // MARK: - Propiedades privadas

    @State private var loaded: LoadedImage?
    @State private var missing = false

    private var label: String {
        let name = item.alt.isEmpty ? item.url.lastPathComponent : item.alt
        return (item.kind == .video ? "Vídeo: " : "Imagen: ") + name
    }

    private var loading: some View {
        RoundedRectangle(cornerRadius: 8).fill(Theme.field)
            .frame(maxWidth: compact ? .infinity : 360)
            .frame(height: compact ? Self.cellHeight : 220)
            .overlay(ProgressView().controlSize(.small))
    }

    @ViewBuilder private var caption: some View {
        if item.kind == .video || !item.alt.isEmpty {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                if !item.alt.isEmpty {
                    Text(MarkdownText.inline(item.alt)).font(.system(size: 12.5)).foregroundStyle(Theme.muted)
                        .lineLimit(compact ? 2 : 4).textSelection(.enabled)
                }
                Spacer(minLength: 0)
                // El reproductor se queda los clics: sus acciones van en este menú.
                if item.kind == .video, !missing {
                    Menu { MediaMenu(item: item, gallery: gallery) } label: {
                        Image(systemName: "ellipsis.circle").foregroundStyle(Theme.muted)
                    }
                    .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
                    .help("Más acciones").accessibilityLabel("Más acciones del vídeo")
                }
            }
        }
    }

    private func picture(_ loaded: LoadedImage) -> some View {
        let size = loaded.size
        let ratio = size.width / max(size.height, 1)
        // Un sprite pequeño se amplía sin suavizar, para que se vea el píxel.
        let pixelArt = max(size.width, size.height) <= 256
        let natural = pixelArt ? size.width * 3 : size.width
        return Group {
            if loaded.animated {
                AnimatedImage(image: loaded.image)
            } else {
                Image(nsImage: loaded.image).resizable().interpolation(pixelArt ? .none : .high)
            }
        }
        .aspectRatio(ratio, contentMode: .fit)
        .frame(maxWidth: compact ? .infinity : min(natural, Self.maximumHeight * ratio))
        .frame(height: compact ? Self.cellHeight : nil)
        .frame(maxWidth: compact ? .infinity : nil)
        .background(compact ? Theme.field : .clear)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.border))
        .contentShape(Rectangle())
        .onTapGesture { MediaPreview.shared.show(gallery.map(\.url), selecting: item.url) }
        .contextMenu { MediaMenu(item: item, gallery: gallery) }
    }
}

/// Abrir en grande, Mostrar en Finder, Copiar.
private struct MediaMenu: View {
    let item: MarkdownMedia
    let gallery: [MarkdownMedia]

    var body: some View {
        Button("Abrir en grande") { MediaPreview.shared.show(gallery.map(\.url), selecting: item.url) }
        Button("Mostrar en Finder") { NSWorkspace.shared.activateFileViewerSelecting([item.url]) }
        Divider()
        Button(item.kind == .video ? "Copiar vídeo" : "Copiar imagen") { MediaClipboard.copy(item) }
        Button("Copiar ruta") {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(item.url.path, forType: .string)
        }
    }
}

/// El archivo ya no está: un marcador tranquilo con la ruta, sin alarmas.
private struct MissingMedia: View {
    let item: MarkdownMedia

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: item.kind == .video ? "film" : "photo").font(.system(size: 18))
                .foregroundStyle(Theme.muted)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.kind == .video ? "Vídeo no disponible" : "Imagen no disponible")
                    .font(.system(size: 12.5)).foregroundStyle(Theme.secondary)
                Text(item.source).font(Theme.mono).foregroundStyle(Theme.muted)
                    .lineLimit(2).truncationMode(.middle).textSelection(.enabled)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.field, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.border, style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
    }
}

// MARK: - Carga

/// Imagen ya decodificada y reducida, lista para pintar.
final class LoadedImage: @unchecked Sendable {
    let image: NSImage
    /// Tamaño original en píxeles, ya girado según su orientación.
    let size: CGSize
    /// Un GIF de varios fotogramas: se pinta con NSImageView, que lo anima.
    let animated: Bool

    init(image: NSImage, size: CGSize, animated: Bool) {
        self.image = image
        self.size = size
        self.animated = animated
    }
}

/// Decodifica fuera del hilo principal, reduce a 2400 px como mucho y guarda en caché por
/// ruta y fecha de modificación: volver a pintar la conversación no vuelve a leer el disco.
enum MediaLoader {
    static let maximumPixels = 2400

    private static let cache: NSCache<NSString, LoadedImage> = {
        let cache = NSCache<NSString, LoadedImage>()
        cache.totalCostLimit = 300 * 1024 * 1024
        return cache
    }()

    static func cached(_ url: URL) -> LoadedImage? { key(url).flatMap { cache.object(forKey: $0) } }

    static func load(_ url: URL) async -> LoadedImage? {
        guard let key = key(url) else { return nil }
        let image = await Task.detached(priority: .userInitiated) { decode(url) }.value
        if let image {
            cache.setObject(image, forKey: key, cost: Int(image.size.width * image.size.height) * 4)
        }
        return image
    }

    private static func key(_ url: URL) -> NSString? {
        guard let attributes = try? FileManager.default.attributesOfItem(atPath: url.path) else { return nil }
        let modified = (attributes[.modificationDate] as? Date)?.timeIntervalSince1970 ?? 0
        return "\(url.path)|\(modified)" as NSString
    }

    private static func decode(_ url: URL) -> LoadedImage? {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, [kCGImageSourceShouldCache: false] as CFDictionary),
              CGImageSourceGetCount(source) > 0 else { return nil }
        let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any] ?? [:]
        var width = (properties[kCGImagePropertyPixelWidth] as? NSNumber)?.doubleValue ?? 0
        var height = (properties[kCGImagePropertyPixelHeight] as? NSNumber)?.doubleValue ?? 0
        if let orientation = (properties[kCGImagePropertyOrientation] as? NSNumber)?.intValue, (5...8).contains(orientation) {
            swap(&width, &height)
        }
        let type = CGImageSourceGetType(source) as String?
        if CGImageSourceGetCount(source) > 1, type == UTType.gif.identifier, let image = NSImage(contentsOf: url) {
            return LoadedImage(image: image, size: CGSize(width: width, height: height), animated: true)
        }
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceShouldCacheImmediately: true,
            kCGImageSourceThumbnailMaxPixelSize: maximumPixels
        ]
        guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else { return nil }
        if width == 0 || height == 0 { width = Double(image.width); height = Double(image.height) }
        return LoadedImage(image: NSImage(cgImage: image, size: NSSize(width: image.width, height: image.height)),
                           size: CGSize(width: width, height: height), animated: false)
    }
}

/// GIF animado: SwiftUI pinta sólo el primer fotograma; NSImageView lo mueve.
private struct AnimatedImage: NSViewRepresentable {
    let image: NSImage

    func makeNSView(context: Context) -> NSImageView {
        let view = NSImageView()
        view.imageScaling = .scaleProportionallyUpOrDown
        view.animates = true
        view.setContentHuggingPriority(.defaultLow, for: .horizontal)
        view.setContentHuggingPriority(.defaultLow, for: .vertical)
        view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        view.setContentCompressionResistancePriority(.defaultLow, for: .vertical)
        view.image = image
        return view
    }

    func updateNSView(_ view: NSImageView, context: Context) {
        if view.image !== image { view.image = image }
    }
}

// MARK: - Vídeo

/// MP4 o MOV en línea: sin sonido, con controles y, si dura poco, en bucle desde el principio.
private struct InlineVideo: View {

    // MARK: - Constantes

    /// Hasta esta duración el clip se reproduce solo y en bucle, como un GIF.
    static let loopLimit: Double = 30

    // MARK: - Propiedades

    let item: MarkdownMedia
    let compact: Bool
    @Binding var missing: Bool

    var body: some View {
        Group {
            if let model {
                let ratio = model.size.width / max(model.size.height, 1)
                PlayerView(player: model.player)
                    .aspectRatio(ratio, contentMode: .fit)
                    .frame(maxWidth: compact ? .infinity : min(model.size.width, MediaTile.maximumHeight * ratio))
                    .frame(height: compact ? MediaTile.cellHeight : nil)
                    .onAppear { if model.loops { model.player.play() } }
                    .onDisappear { model.player.pause() }
            } else {
                RoundedRectangle(cornerRadius: 8).fill(Theme.field)
                    .frame(maxWidth: compact ? .infinity : 360)
                    .frame(height: compact ? MediaTile.cellHeight : 220)
                    .overlay(ProgressView().controlSize(.small))
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.border))
        .task(id: item.url) {
            if let loaded = await VideoModel.load(item.url, loopLimit: Self.loopLimit) {
                model = loaded
                if loaded.loops { loaded.player.play() }
            } else {
                missing = true
            }
        }
    }

    // MARK: - Propiedades privadas

    @State private var model: VideoModel?
}

/// El reproductor de un clip y, si es corto, el que lo repite.
@MainActor private final class VideoModel {
    let player: AVQueuePlayer
    let size: CGSize
    let loops: Bool
    private let looper: AVPlayerLooper?

    private init(player: AVQueuePlayer, size: CGSize, loops: Bool, looper: AVPlayerLooper?) {
        self.player = player
        self.size = size
        self.loops = loops
        self.looper = looper
    }

    static func load(_ url: URL, loopLimit: Double) async -> VideoModel? {
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        let asset = AVURLAsset(url: url)
        guard let duration = try? await asset.load(.duration),
              let track = try? await asset.loadTracks(withMediaType: .video).first,
              let (natural, transform) = try? await track.load(.naturalSize, .preferredTransform) else { return nil }
        let turned = natural.applying(transform)
        let size = CGSize(width: abs(turned.width), height: abs(turned.height))
        let loops = duration.seconds.isFinite && duration.seconds <= loopLimit
        let player = AVQueuePlayer()
        player.isMuted = true
        player.actionAtItemEnd = loops ? .advance : .pause
        let item = AVPlayerItem(asset: asset)
        let looper = loops ? AVPlayerLooper(player: player, templateItem: item) : nil
        if !loops { player.insert(item, after: nil) }
        return VideoModel(player: player, size: size.width > 0 ? size : CGSize(width: 16, height: 9),
                          loops: loops, looper: looper)
    }
}

private struct PlayerView: NSViewRepresentable {
    let player: AVPlayer

    func makeNSView(context: Context) -> AVPlayerView {
        let view = AVPlayerView()
        view.controlsStyle = .floating
        view.showsFullScreenToggleButton = true
        view.videoGravity = .resizeAspect
        view.setContentHuggingPriority(.defaultLow, for: .horizontal)
        view.setContentHuggingPriority(.defaultLow, for: .vertical)
        view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        view.setContentCompressionResistancePriority(.defaultLow, for: .vertical)
        view.player = player
        return view
    }

    func updateNSView(_ view: AVPlayerView, context: Context) {
        if view.player !== player { view.player = player }
    }
}

// MARK: - Acciones

/// Vista rápida con todas las imágenes del bloque: las flechas pasan de una a otra.
final class MediaPreview: NSObject, QLPreviewPanelDataSource {
    static let shared = MediaPreview()

    private var urls: [URL] = []

    func show(_ urls: [URL], selecting url: URL) {
        guard let panel = QLPreviewPanel.shared() else { return }
        self.urls = urls
        panel.dataSource = self
        panel.reloadData()
        panel.currentPreviewItemIndex = urls.firstIndex(of: url) ?? 0
        if panel.isVisible { panel.refreshCurrentPreviewItem() } else { panel.makeKeyAndOrderFront(nil) }
    }

    func numberOfPreviewItems(in panel: QLPreviewPanel!) -> Int { urls.count }

    func previewPanel(_ panel: QLPreviewPanel!, previewItemAt index: Int) -> QLPreviewItem! {
        urls.indices.contains(index) ? urls[index] as NSURL : nil
    }
}

enum MediaClipboard {
    /// La imagen (en su formato y en TIFF, para las apps que sólo pegan eso) y el archivo.
    static func copy(_ item: MarkdownMedia) {
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        let entry = NSPasteboardItem()
        if item.kind == .image {
            if let data = try? Data(contentsOf: item.url), let type = UTType(filenameExtension: item.url.pathExtension) {
                entry.setData(data, forType: NSPasteboard.PasteboardType(type.identifier))
            }
            if let tiff = NSImage(contentsOf: item.url)?.tiffRepresentation { entry.setData(tiff, forType: .tiff) }
        }
        entry.setString(item.url.absoluteString, forType: .fileURL)
        pasteboard.writeObjects([entry])
    }
}
