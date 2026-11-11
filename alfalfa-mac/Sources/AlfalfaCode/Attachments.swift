import AlfalfaKit
import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// Miniaturas de las imágenes y chips de los archivos que se van a enviar, con su botón para
/// quitarlos. Con `blocked` (el modelo elegido no ve imágenes) se marcan las imágenes: no
/// llegarían al modelo. Los archivos no dependen de eso: el motor manda su texto.
struct AttachmentStrip: View {
    @Binding var images: [URL]
    var blocked = false

    var body: some View {
        if !images.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(Array(images.enumerated()), id: \.element) { index, url in
                        ZStack(alignment: .topTrailing) {
                            if Attachments.isImage(url) { thumbnail(url, index: index) } else { FileChip(url: url, height: 88) }
                            Button { images.removeAll { $0 == url } } label: {
                                Image(systemName: "xmark.circle.fill").font(.system(size: 15))
                                    .foregroundStyle(Theme.light, Theme.dark)
                            }
                            .buttonStyle(.plain).offset(x: 6, y: -6)
                            .accessibilityLabel("Quitar \(url.lastPathComponent)")
                        }
                    }
                }
                .padding(.top, 6).padding(.trailing, 6)
            }
        }
    }

    private func thumbnail(_ url: URL, index: Int) -> some View {
        let number = images.prefix(index + 1).filter(Attachments.isImage).count
        return Thumbnail(url: url, size: 88)
                                .opacity(blocked ? 0.45 : 1)
                                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.danger, lineWidth: blocked ? 2 : 0))
                                .overlay(alignment: .bottomLeading) {
                                    if blocked {
                                        Image(systemName: "eye.slash.fill").font(.system(size: 13))
                                            .foregroundStyle(Theme.danger).padding(5)
                                    }
                                }
                                .help(blocked ? ImageSupport.notice + ": no se enviará"
                                      : "[Image #\(number)] \(url.lastPathComponent)")
                                .accessibilityLabel(blocked ? url.lastPathComponent + ". " + ImageSupport.notice
                                                    : url.lastPathComponent)
    }
}

/// Un archivo adjunto (PDF, texto, código, Word, Excel): su icono, su nombre y su tamaño. Un
/// clic lo abre con su app; si ya no está (se movió o borró), lo dice.
struct FileChip: View {
    let url: URL
    var height: CGFloat = 56

    var body: some View {
        let exists = FileManager.default.fileExists(atPath: url.path)
        Button { if exists { NSWorkspace.shared.open(url) } } label: {
            HStack(spacing: 8) {
                Image(nsImage: NSWorkspace.shared.icon(forFile: url.path)).resizable().frame(width: 32, height: 32)
                    .opacity(exists ? 1 : 0.4)
                VStack(alignment: .leading, spacing: 2) {
                    Text(url.lastPathComponent).font(.system(size: 12, weight: .medium)).lineLimit(2)
                        .truncationMode(.middle).foregroundStyle(Theme.text)
                    Text(exists ? detail : "Ya no está en su carpeta").font(.system(size: 11))
                        .foregroundStyle(Theme.muted)
                }
            }
            .padding(.horizontal, 10)
            .frame(width: 190, height: height, alignment: .leading)
            .background(Theme.bubble, in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.border))
        }
        .buttonStyle(.plain)
        .help(url.path)
        .accessibilityLabel("Archivo adjunto " + url.lastPathComponent)
    }

    private var detail: String {
        let kind = url.pathExtension.isEmpty ? "Texto" : url.pathExtension.uppercased()
        let bytes = (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize).flatMap { $0 } ?? 0
        return kind + " · " + ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)
    }
}

struct Thumbnail: View {
    let url: URL
    var size: CGFloat = 56

    var body: some View {
        Group {
            if let image = NSImage(contentsOf: url) {
                Image(nsImage: image).resizable().scaledToFill()
            } else {
                Image(systemName: "photo").foregroundStyle(Theme.muted)
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.border))
    }
}

/// Elegir, soltar o pegar imágenes y archivos en un compositor.
enum ImagePicker {
    /// `withImages` false: el modelo no ve imágenes y sólo se ofrecen archivos.
    @MainActor static func choose(into images: inout [URL], withImages: Bool = true) {
        let panel = NSOpenPanel()
        let files = Attachments.fileExtensions.compactMap { UTType(filenameExtension: $0) }
        panel.allowedContentTypes = (withImages ? [.image] : []) + files + [.plainText, .sourceCode]
        panel.allowsMultipleSelection = true
        panel.prompt = "Adjuntar"
        panel.message = withImages ? "Imágenes, PDF, texto, código, Word o Excel para el agente"
            : "PDF, texto, código, Word o Excel para el agente (este modelo no ve imágenes)"
        if panel.runModal() == .OK { add(panel.urls, to: &images, withImages: withImages) }
    }

    /// Hasta ocho imágenes y ocho archivos; nada de enlaces simbólicos (el motor los rechaza).
    static func add(_ urls: [URL], to images: inout [URL], withImages: Bool = true) {
        for url in urls.map(keep) where !images.contains(url) {
            let image = Attachments.isImage(url)
            guard image ? withImages : Attachments.isFile(url) else { continue }
            guard images.filter({ Attachments.isImage($0) == image }).count < Attachments.maximum else { continue }
            images.append(url)
        }
    }

    /// Una captura arrastrada desde su miniatura flotante vive en una carpeta temporal que
    /// macOS borra enseguida: se copia a los adjuntos para que siga ahí al enviar.
    static func keep(_ url: URL) -> URL {
        let path = url.resolvingSymlinksInPath().path
        let temporary = path.hasPrefix("/private/var/folders/") || path.hasPrefix("/var/folders/")
            || path.hasPrefix(FileManager.default.temporaryDirectory.resolvingSymlinksInPath().path)
        guard temporary, let data = try? Data(contentsOf: url) else { return url }
        let directory = Attachments.directory()
        let copy = directory.appendingPathComponent("soltada-\(UUID().uuidString.prefix(8))-" + url.lastPathComponent)
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            try data.write(to: copy, options: .atomic)
            return copy
        } catch {
            return url
        }
    }

    /// Soltar imágenes y archivos sobre el compositor.
    static func drop(_ providers: [NSItemProvider], into images: Binding<[URL]>, withImages: Bool = true,
                     refused: @escaping () -> Void = {}) -> Bool {
        var accepted = false
        for provider in providers where provider.canLoadObject(ofClass: URL.self) {
            accepted = true
            _ = provider.loadObject(ofClass: URL.self) { url, _ in
                guard let url else { return }
                DispatchQueue.main.async {
                    if Attachments.isImage(url), !withImages { refused() }
                    add([url], to: &images.wrappedValue, withImages: withImages)
                }
            }
        }
        return accepted
    }

    /// ⌘V con una imagen en el portapapeles (y sin texto): se guarda como PNG en los adjuntos.
    /// Un archivo copiado en Finder (PDF, texto…) se adjunta tal cual.
    static func pastedImage(from pasteboard: NSPasteboard = .general) -> URL? {
        if pasteboard.string(forType: .string) != nil { return nil }
        if let urls = pasteboard.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL],
           let image = urls.first(where: Attachments.isAttachable) {
            return image
        }
        guard let image = NSImage(pasteboard: pasteboard), let tiff = image.tiffRepresentation,
              let png = NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:]) else { return nil }
        return try? Attachments.store(png: png, in: Attachments.directory())
    }
}
