import AlfalfaKit
import Charts
import SwiftUI

/// `Chart`: barras, líneas o tarta con Swift Charts. Las series vienen como `{name, values}` y las etiquetas del eje,
/// en `labels`.
struct ScreenChart: View {

    // MARK: - Tipos

    struct Point: Identifiable {
        let id: String
        let series: String
        let label: String
        let value: Double
    }

    // MARK: - Propiedades

    let node: ScreenNode
    let context: ScreenContext

    var body: some View {
        let kind = context.text("kind", of: node) ?? "bar"
        Chart(points) { point in
            switch kind {
            case "line":
                LineMark(x: .value("Etiqueta", point.label), y: .value("Valor", point.value))
                    .foregroundStyle(by: .value("Serie", point.series))
            case "pie":
                SectorMark(angle: .value("Valor", max(0, point.value)), innerRadius: .ratio(0.5))
                    .foregroundStyle(by: .value("Etiqueta", point.label))
            default:
                BarMark(x: .value("Etiqueta", point.label), y: .value("Valor", point.value))
                    .foregroundStyle(by: .value("Serie", point.series))
                    .position(by: .value("Serie", point.series))
            }
        }
        .chartLegend(seriesCount > 1 || kind == "pie" ? .visible : .hidden)
        .frame(maxWidth: 520, minHeight: 180, maxHeight: 220)
        .accessibilityLabel(summary)
    }

    // MARK: - Propiedades privadas

    private var series: [(name: String, values: [Double])] {
        (node.prop("series", in: context.state)?.array ?? []).compactMap { row in
            guard let fields = row.object, let values = fields["values"]?.array else { return nil }
            return (fields["name"]?.text ?? "", values.compactMap(\.number))
        }
    }

    private var seriesCount: Int { series.count }

    private var points: [Point] {
        let labels = node.prop("labels", in: context.state)?.array?.map(\.text) ?? []
        return series.enumerated().flatMap { offset, serie in
            serie.values.enumerated().map { index, value in
                let label = index < labels.count ? labels[index] : String(index + 1)
                return Point(id: "\(offset)-\(index)", series: serie.name.isEmpty ? "Serie \(offset + 1)" : serie.name,
                             label: label, value: value)
            }
        }
    }

    private var summary: String {
        series.map { $0.name + ": " + $0.values.map { ScreenValue.number($0).text }.joined(separator: ", ") }
            .joined(separator: "; ")
    }
}
