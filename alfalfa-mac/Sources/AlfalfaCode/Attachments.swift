import AlfalfaKit
import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// Miniaturas de las imágenes que se van a enviar, con su botón para quitarlas.
struct AttachmentStrip: View {
    @Binding var images: [URL]

    var body: some View {
        if !images.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(Array(images.enumerated()), id: \.element) { index, url in
                        ZStack(alignment: .topTrailing) {
                            Thumbnail(url: url, size: 88)
                                .help("[Image #\(index + 1)] \(url.lastPathComponent)")
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

/// Elegir, soltar o pegar imágenes en un compositor.
enum ImagePicker {
    @MainActor static func choose(into images: inout [URL]) {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.image]
        panel.allowsMultipleSelection = true
        panel.prompt = "Adjuntar"
        panel.message = "Imágenes para el agente"
        if panel.runModal() == .OK { add(panel.urls, to: &images) }
    }

    static func add(_ urls: [URL], to images: inout [URL]) {
        for url in urls.map(keep) where Attachments.isImage(url) && !images.contains(url) && images.count < Attachments.maximum {
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

    /// Soltar archivos de imagen sobre el compositor.
    static func drop(_ providers: [NSItemProvider], into images: Binding<[URL]>) -> Bool {
        var accepted = false
        for provider in providers where provider.canLoadObject(ofClass: URL.self) {
            accepted = true
            _ = provider.loadObject(ofClass: URL.self) { url, _ in
                guard let url else { return }
                DispatchQueue.main.async { add([url], to: &images.wrappedValue) }
            }
        }
        return accepted
    }

    /// ⌘V con una imagen en el portapapeles (y sin texto): se guarda como PNG en los adjuntos.
    static func pastedImage(from pasteboard: NSPasteboard = .general) -> URL? {
        if pasteboard.string(forType: .string) != nil { return nil }
        if let urls = pasteboard.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL],
           let image = urls.first(where: Attachments.isImage) {
            return image
        }
        guard let image = NSImage(pasteboard: pasteboard), let tiff = image.tiffRepresentation,
              let png = NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:]) else { return nil }
        return try? Attachments.store(png: png, in: Attachments.directory())
    }
}
