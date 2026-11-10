import AlfalfaKit
import AppKit
import SwiftUI

/// La hoja de alfalfa del indicador de actividad: tres foliolos y un tallo, dibujados con
/// Canvas para que se vean nítidos a 14–18 pt. Pensando y trabajando se anima (ver
/// `LeafMotion`); esperando un permiso queda quieta en ámbar y al terminar, quieta en verde.
///
/// El reloj de la animación se para cuando la ventana no se ve (otra app delante, minimizada,
/// otro escritorio) y en las fases quietas, así que no gasta CPU.
struct AlfalfaActivityIcon: View {

    // MARK: - Propiedades

    let phase: LiveActivity.Phase
    var size: CGFloat = 16
    /// La ventana se ve; si no, el dibujo queda quieto.
    var visible = true

    var body: some View {
        let animated = LeafMotion.isAnimated(phase) && visible
        TimelineView(.animation(minimumInterval: 1.0 / 30, paused: !animated)) { context in
            let time = context.date.timeIntervalSinceReferenceDate
            Canvas { graphics, canvasSize in
                draw(in: &graphics, size: canvasSize, time: time)
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }

    // MARK: - Propiedades privadas

    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var color: Color {
        let light = colorScheme == .light
        switch phase {
        // En claro, tonos más oscuros: el verde de la marca sobre blanco no llega a 3:1.
        case .waiting: return light ? Color(hex: 0xA86B12) : Theme.warning
        default: return light ? Color(hex: 0x3B8F45) : Theme.alfalfa
        }
    }

    // MARK: - Métodos privados

    private func draw(in graphics: inout GraphicsContext, size: CGSize, time: Double) {
        let side = min(size.width, size.height)
        let pivot = CGPoint(x: size.width / 2, y: size.height / 2 + side * 0.44)
        let center = CGPoint(x: size.width / 2, y: size.height / 2 + side * 0.10)
        let sway = LeafMotion.sway(phase, time: time, reduceMotion: reduceMotion)
        if sway != 0 {
            graphics.translateBy(x: pivot.x, y: pivot.y)
            graphics.rotate(by: .degrees(sway))
            graphics.translateBy(x: -pivot.x, y: -pivot.y)
        }
        let shading = GraphicsContext.Shading.color(color)
        // Tallo: corto, para que se lea como hoja y no como tres puntos.
        var stem = Path()
        stem.move(to: center)
        stem.addLine(to: pivot)
        graphics.stroke(stem, with: shading, style: StrokeStyle(lineWidth: max(1, side / 13), lineCap: .round))
        // Izquierdo, terminal (con su peciolulo, un poco más arriba) y derecho.
        let leaflets: [(angle: Double, base: CGPoint)] = [
            (-74, center),
            (0, CGPoint(x: center.x, y: center.y - side * 0.05)),
            (74, center)
        ]
        for (index, leaflet) in leaflets.enumerated() {
            let pose = LeafMotion.pose(phase, leaflet: index, time: time, reduceMotion: reduceMotion)
            let transform = CGAffineTransform(translationX: leaflet.base.x, y: leaflet.base.y)
                .rotated(by: leaflet.angle * .pi / 180)
                .scaledBy(x: pose.scale, y: pose.scale)
            var layer = graphics
            layer.opacity = pose.opacity
            layer.fill(Self.leaflet(length: side * 0.42, width: side * 0.32).applying(transform), with: shading)
        }
    }

    /// Un foliolo obovado (estrecho en la base, ancho cerca de la punta) que nace en (0, 0) y
    /// apunta hacia arriba.
    private static func leaflet(length: CGFloat, width: CGFloat) -> Path {
        let half = width / 2
        var path = Path()
        path.move(to: .zero)
        path.addCurve(to: CGPoint(x: half, y: -length * 0.66),
                      control1: CGPoint(x: half * 0.25, y: -length * 0.18),
                      control2: CGPoint(x: half, y: -length * 0.40))
        path.addCurve(to: CGPoint(x: 0, y: -length),
                      control1: CGPoint(x: half, y: -length * 0.90),
                      control2: CGPoint(x: half * 0.45, y: -length))
        path.addCurve(to: CGPoint(x: -half, y: -length * 0.66),
                      control1: CGPoint(x: -half * 0.45, y: -length),
                      control2: CGPoint(x: -half, y: -length * 0.90))
        path.addCurve(to: .zero,
                      control1: CGPoint(x: -half, y: -length * 0.40),
                      control2: CGPoint(x: -half * 0.25, y: -length * 0.18))
        path.closeSubpath()
        return path
    }
}

/// La fila viva: hoja animada, frase en gerundio, tiempo transcurrido y, si se puede abrir,
/// el triángulo. VoiceOver lee «Pensando, 38 segundos» al llegar a ella, sin anunciar cada
/// segundo.
struct LiveActivityRow: View {

    // MARK: - Propiedades

    let activity: LiveActivity
    /// Con valor, la fila se abre y se cierra (las llamadas del grupo); sin él, no lleva triángulo.
    var expanded: Binding<Bool>?

    var body: some View {
        SecondTicker(since: activity.since, active: visible && activity.phase != .done) { now in
            if let expanded {
                Button { withAnimation(.easeOut(duration: 0.15)) { expanded.wrappedValue.toggle() } } label: {
                    content(now: now, chevron: expanded.wrappedValue)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(activity.spoken(at: now))
                .accessibilityHint(expanded.wrappedValue ? "Plegar las llamadas" : "Ver cada llamada")
                .accessibilityAddTraits(.updatesFrequently)
            } else {
                content(now: now, chevron: nil)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(activity.spoken(at: now))
                    .accessibilityAddTraits(.updatesFrequently)
            }
        }
        .background(WindowVisibilityReader(visible: $visible))
    }

    // MARK: - Propiedades privadas

    @State private var visible = true

    // MARK: - Métodos privados

    private func content(now: Date, chevron open: Bool?) -> some View {
        HStack(spacing: 8) {
            AlfalfaActivityIcon(phase: activity.phase, size: 16, visible: visible)
            Text(activity.phrase).font(.system(size: 14))
                .foregroundStyle(activity.phase == .waiting ? Theme.warning : Theme.secondary)
                .lineLimit(1).truncationMode(.middle)
            if let seconds = activity.elapsed(at: now) {
                Text(ElapsedTime.text(seconds)).font(.system(size: 13)).monospacedDigit()
                    .foregroundStyle(Theme.muted).fixedSize()
            }
            if let open {
                Image(systemName: "chevron.right").font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(Theme.muted).rotationEffect(.degrees(open ? 90 : 0))
            }
        }
        .padding(.vertical, 6).contentShape(Rectangle())
    }
}

/// Vuelve a pintar su contenido cada segundo desde `since` mientras está `active`. La hora
/// sale de los eventos: el contador no se reinicia aunque la vista se vuelva a crear.
struct SecondTicker<Content: View>: View {
    let since: Double?
    let active: Bool
    @ViewBuilder let content: (Date) -> Content

    var body: some View {
        if active, let since {
            TimelineView(.periodic(from: Date(timeIntervalSince1970: since), by: 1)) { context in
                content(context.date)
            }
        } else {
            content(Date())
        }
    }
}

/// Dice si la ventana de la vista se ve: con otra app delante, minimizada o en otro escritorio
/// los relojes de la fila viva se paran.
struct WindowVisibilityReader: NSViewRepresentable {
    final class ObservingView: NSView {
        var onChange: ((Bool) -> Void)?
        private var observer: NSObjectProtocol?

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            if let observer { NotificationCenter.default.removeObserver(observer) }
            observer = nil
            guard let window else { return }
            observer = NotificationCenter.default.addObserver(
                forName: NSWindow.didChangeOcclusionStateNotification, object: window, queue: .main
            ) { [weak self] _ in self?.report() }
            report()
        }

        private func report() {
            let visible = window?.occlusionState.contains(.visible) ?? false
            // Fuera del pase de pintado: cambiar el estado ahí avisa de un ciclo.
            DispatchQueue.main.async { [weak self] in self?.onChange?(visible) }
        }

        deinit {
            if let observer { NotificationCenter.default.removeObserver(observer) }
        }
    }

    @Binding var visible: Bool

    func makeNSView(context: Context) -> ObservingView {
        let view = ObservingView()
        view.onChange = { visible = $0 }
        return view
    }

    func updateNSView(_ view: ObservingView, context: Context) {
        view.onChange = { if visible != $0 { visible = $0 } }
    }
}
