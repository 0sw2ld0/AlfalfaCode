import AlfalfaKit
import AppKit
import SwiftUI

/// Demo propia: la alfalfa que crece con el contexto. Sólo instancia `Timeline` y las vistas de la
/// planta; nunca AppModel, sesiones guardadas ni el Llavero. Pinta cada vista en su propia
/// `NSHostingView`, fuera de pantalla: no abre ventanas ni roba el foco.
@main
struct AlfalfaGrowthDemo {
    /// Un turno de la sesión: lo que manda el motor (`contexto`) y lo que hace el agente.
    struct Step {
        let caption: String
        let line: String?
        let phase: LiveActivity.Phase
        let phrase: String
    }

    static let steps: [Step] = [
        Step(caption: "Sesión nueva", line: nil, phase: .thinking, phrase: "Pensando"),
        Step(caption: "Leyendo el proyecto", line: context(26_000, estimate: 27_500), phase: .working,
             phrase: "Leyendo Sources/App.swift"),
        Step(caption: "Primeros cambios", line: context(61_000, estimate: 64_000), phase: .working,
             phrase: "Editando Timeline.swift"),
        Step(caption: "Pasando pruebas", line: context(100_000, estimate: 104_000), phase: .working,
             phrase: "Pasando las pruebas"),
        Step(caption: "Cerca de compactar", line: context(140_000, estimate: 152_000), phase: .thinking,
             phrase: "Pensando"),
        Step(caption: "Tras compactar", line: context(13_000, estimate: 13_000), phase: .thinking, phrase: "Pensando")
    ]

    static func context(_ tokens: Int, estimate: Int) -> String {
        #"{"version":2,"tipo":"contexto","tokens":\#(tokens),"limite":200000,"estimado":\#(estimate),"umbral":160000}"#
    }

    @MainActor static func main() {
        let folder = URL(fileURLWithPath: CommandLine.arguments[1])
        var timeline = Timeline()
        var states: [(step: Step, growth: Double, percent: Int)] = []
        for step in steps {
            if let line = step.line, let message = AlfalfaMessage.decode(line: line) { timeline.apply(message) }
            states.append((step, timeline.plantGrowth, Int((timeline.contextFraction * 100).rounded())))
        }
        for scheme in [ColorScheme.dark, .light] {
            let name = scheme == .dark ? "etapas.png" : "etapas-claro.png"
            render(stages(states, scheme: scheme), to: folder.appendingPathComponent(name))
        }
        render(motion(reduceMotion: false), to: folder.appendingPathComponent("movimiento.png"))
        render(motion(reduceMotion: true), to: folder.appendingPathComponent("movimiento-reducido.png"))
        let report = states.map { state in
            ["paso": state.step.caption, "usado": state.percent, "camino_a_compactar": Int((state.growth * 100).rounded()),
             "etapa": AlfalfaPlant.stage(for: state.growth).label,
             "hojas": AlfalfaPlant(growth: state.growth).leafCount] as [String: Any]
        }
        let data = try! JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
        try! data.write(to: folder.appendingPathComponent("etapas.json"))
        print(String(decoding: data, as: UTF8.self))
    }

    /// Cada turno: la cabecera de la sesión (planta de 22 pt, quieta), la fila viva (16 pt,
    /// animada) y la fila de la barra lateral (16 pt, del color del estado).
    @MainActor static func stages(_ states: [(step: Step, growth: Double, percent: Int)],
                                  scheme: ColorScheme) -> some View {
        let background = scheme == .dark ? Theme.background : Color(hex: 0xF7F6F2)
        let text = scheme == .dark ? Theme.text : Color(hex: 0x262624)
        return HStack(alignment: .top, spacing: 18) {
            ForEach(Array(states.enumerated()), id: \.offset) { _, state in
                VStack(alignment: .leading, spacing: 12) {
                    AlfalfaActivityIcon(phase: .done, size: 96)
                        .environment(\.alfalfaGrowth, state.growth)
                        .frame(maxWidth: .infinity)
                    Text(state.step.caption).font(.system(size: 13, weight: .semibold)).foregroundStyle(text)
                    Text("\(state.percent) % usado · \(AlfalfaPlant.stage(for: state.growth).label)")
                        .font(.system(size: 12)).foregroundStyle(Theme.muted)
                    Divider()
                    HStack(spacing: 10) {
                        AlfalfaActivityIcon(phase: .done, size: 22).environment(\.alfalfaGrowth, state.growth)
                        Text("Arreglar la suma").font(.system(size: 15, weight: .semibold)).foregroundStyle(text)
                            .lineLimit(1)
                    }
                    LiveActivityRow(activity: LiveActivity(phase: state.step.phase, phrase: state.step.phrase, since: nil))
                        .environment(\.alfalfaGrowth, state.growth)
                    HStack(spacing: 10) {
                        AlfalfaActivityIcon(phase: .thinking, size: 16, tint: Theme.alfalfa)
                            .environment(\.alfalfaGrowth, state.growth)
                        Text("Arreglar la suma").font(.system(size: 13)).foregroundStyle(text).lineLimit(1)
                    }
                    .padding(.horizontal, 10).frame(minHeight: 36)
                    .background(scheme == .dark ? Theme.sidebar : Color(hex: 0xEDEBE6), in: RoundedRectangle(cornerRadius: 8))
                }
                .frame(width: 168)
            }
        }
        .padding(24).background(background).environment(\.colorScheme, scheme)
    }

    /// La planta crecida a lo largo de dos segundos trabajando: se dobla desde la base, las hojas
    /// aletean desfasadas y la de la punta respira. Con «Reducir movimiento», sólo opacidad.
    @MainActor static func motion(reduceMotion: Bool) -> some View {
        HStack(spacing: 8) {
            ForEach(Array(stride(from: 0.0, to: 2.0, by: 0.2)), id: \.self) { time in
                Canvas { graphics, size in
                    AlfalfaPlantDrawing(plant: AlfalfaPlant(growth: 0.9), phase: .working, time: time,
                                        reduceMotion: reduceMotion, leaf: Theme.alfalfa, flower: Color(hex: 0xB49AEB))
                        .draw(in: &graphics, size: size)
                }
                .frame(width: 80, height: 80)
            }
        }
        .padding(16).background(Theme.background)
    }

    @MainActor static func render(_ view: some View, to url: URL) {
        let host = NSHostingView(rootView: view)
        host.setFrameSize(host.fittingSize)
        host.layoutSubtreeIfNeeded()
        let bitmap = host.bitmapImageRepForCachingDisplay(in: host.bounds)!
        host.cacheDisplay(in: host.bounds, to: bitmap)
        try! bitmap.representation(using: .png, properties: [:])!.write(to: url)
    }
}
