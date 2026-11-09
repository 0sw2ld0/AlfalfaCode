import SwiftUI

extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255, blue: Double(hex & 0xFF) / 255)
    }
}

/// Paleta de la maqueta aprobada (docs/arnes/maqueta): oscura, con acento verde alfalfa.
enum Theme {
    static let background = Color(hex: 0x262624)
    static let sidebar = Color(hex: 0x1F1F1D)
    static let border = Color(hex: 0x353532)
    static let selection = Color(hex: 0x33332F)
    static let card = Color(hex: 0x2D2D2A)
    static let cardBorder = Color(hex: 0x45443F)
    static let field = Color(hex: 0x232321)
    static let text = Color(hex: 0xE8E6E1)
    static let secondary = Color(hex: 0xB5B3AD)
    static let muted = Color(hex: 0x8E8C86)
    static let alfalfa = Color(hex: 0x6FBF73)
    static let alfalfaLight = Color(hex: 0x8FD08F)
    static let warning = Color(hex: 0xE5B23E)
    static let danger = Color(hex: 0xE88A80)
    static let light = Color(hex: 0xF2F0EB)
    static let dark = Color(hex: 0x1C1C1A)
    static let mono = Font.system(size: 12.5, design: .monospaced)
    // Conversación, con las medidas de la app de Codex: texto de 15 pt con aire entre
    // líneas y una columna centrada que no pasa de 760 pt.
    static let body = Font.system(size: 15)
    static let bodyLineSpacing: CGFloat = 5
    static let column: CGFloat = 760
    static let bubble = Color(hex: 0x353532)
    static let link = Color(hex: 0x7BA7F0)
}

/// Hoja de alfalfa: los mismos trazos que el icono de la maqueta (rejilla de 24).
struct LeafShape: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        let k = min(rect.width, rect.height) / 24
        func p(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: rect.minX + x * k, y: rect.minY + y * k) }
        path.move(to: p(12, 21)); path.addLine(to: p(12, 10))
        path.move(to: p(12, 13))
        path.addCurve(to: p(5, 6), control1: p(8, 13), control2: p(5, 10.5))
        path.addCurve(to: p(12, 13), control1: p(9.5, 6), control2: p(12, 8.5))
        path.closeSubpath()
        path.move(to: p(12, 10))
        path.addCurve(to: p(19, 3.5), control1: p(12, 6), control2: p(14.5, 3.5))
        path.addCurve(to: p(12, 10), control1: p(19, 8), control2: p(16, 10))
        path.closeSubpath()
        return path
    }
}

struct LeafIcon: View {
    var size: CGFloat = 18
    var body: some View {
        LeafShape()
            .stroke(Theme.alfalfa, style: StrokeStyle(lineWidth: size / 12, lineCap: .round, lineJoin: .round))
            .frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}
