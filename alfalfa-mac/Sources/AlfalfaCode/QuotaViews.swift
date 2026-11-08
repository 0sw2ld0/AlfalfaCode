import AlfalfaKit
import SwiftUI

/// Los límites del código de cortesía, como los de un plan: por cada parte (GPT-6 Luna, Ollama)
/// el tope de hoy, si lo hay, y el del mes, cada uno con su barra, su % y cuándo se restablece.
struct QuotaBars: View {

    // MARK: - Propiedades

    let quotas: [Quota]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(quotas) { quota in
                VStack(alignment: .leading, spacing: 8) {
                    Text(quota.label).font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.secondary)
                    if let daily = quota.daily {
                        QuotaRow(title: "Hoy", percent: daily.usedPercent, resets: QuotaText.today(daily.renews))
                    }
                    QuotaRow(title: "Este mes", percent: quota.usedPercent, resets: QuotaText.month(quota.renews))
                }
            }
        }
    }
}

/// Una fila: «Hoy · Se restablece a las 7:00 p. m. · 10 %» y su barra debajo.
struct QuotaRow: View {

    // MARK: - Propiedades

    let title: String
    let percent: Double
    let resets: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(title).font(.system(size: 12.5))
                Spacer(minLength: 8)
                if let resets {
                    Text(resets).font(.system(size: 11.5)).foregroundStyle(Theme.muted).lineLimit(1)
                }
                Text(String(format: "%.0f %%", percent)).font(.system(size: 11.5, design: .monospaced))
                    .foregroundStyle(Theme.secondary)
            }
            ProgressView(value: min(100, max(0, percent)), total: 100).tint(QuotaText.tint(percent))
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(title): \(Int(percent)) por ciento usado. \(resets ?? "")")
    }
}

/// Los límites en una línea, encima del cuadro de texto de una sesión con Alfalfa. La barra
/// enseña el que más aprieta; el detalle de hoy y del mes va en el texto y en la ayuda.
struct QuotaLine: View {

    // MARK: - Propiedades

    let quotas: [Quota]

    var body: some View {
        HStack(spacing: 14) {
            ForEach(quotas) { quota in
                HStack(spacing: 6) {
                    Text(quota.label).font(.system(size: 11)).foregroundStyle(Theme.secondary)
                        .lineLimit(1).fixedSize()
                    ProgressView(value: min(100, max(0, quota.tightestPercent)), total: 100).frame(width: 60)
                        .tint(QuotaText.tint(quota.tightestPercent))
                    Text(QuotaText.short(quota)).font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(Theme.secondary).lineLimit(1).fixedSize()
                }
                .accessibilityElement(children: .combine)
                .accessibilityLabel(QuotaText.help(quota))
                .help(QuotaText.help(quota))
            }
        }
    }
}

/// Los textos de los límites, en español y en la hora de este Mac.
enum QuotaText {

    // MARK: - Propiedades

    /// El formato de este Mac si está en español (respeta 12 o 24 horas); si no, español a secas.
    private static var spanish: Locale {
        Locale.current.language.languageCode?.identifier == "es" ? Locale.current : Locale(identifier: "es")
    }

    // MARK: - Métodos

    static func tint(_ percent: Double) -> Color {
        percent >= 90 ? Theme.danger : percent >= 70 ? Theme.warning : Theme.alfalfa
    }

    /// «hoy 10 % · mes 15 %», o sólo «15 %» si el código no tiene tope de hoy.
    static func short(_ quota: Quota) -> String {
        let month = String(format: "%.0f %%", quota.usedPercent)
        guard let daily = quota.daily else { return month }
        return String(format: "hoy %.0f %% · mes ", daily.usedPercent) + month
    }

    static func help(_ quota: Quota) -> String {
        var lines = [quota.label]
        if let daily = quota.daily {
            lines.append(String(format: "Hoy: %.0f %%", daily.usedPercent)
                         + (today(daily.renews).map { " · \($0)" } ?? ""))
        }
        lines.append(String(format: "Este mes: %.0f %%", quota.usedPercent)
                     + (month(quota.renews).map { " · \($0)" } ?? ""))
        return lines.joined(separator: "\n")
    }

    /// «Se restablece a las 7:00 p. m.» a partir de la medianoche UTC que manda el servicio.
    static func today(_ iso: String?) -> String? {
        guard let iso, let date = ISO8601DateFormatter().date(from: iso) else { return nil }
        let time = date.formatted(Date.FormatStyle(date: .omitted, time: .shortened).locale(spanish))
        if Calendar.current.isDateInToday(date) || date.timeIntervalSinceNow <= 0 {
            return "Se restablece a las \(time)"
        }
        let day = date.formatted(Date.FormatStyle().weekday(.abbreviated).locale(spanish))
        return "Se restablece el \(day), \(time)"
    }

    /// «Se restablece el 1 dic» a partir de `AAAA-MM-DD`.
    static func month(_ text: String?) -> String? {
        guard let text else { return nil }
        let parser = DateFormatter()
        parser.locale = Locale(identifier: "en_US_POSIX")
        parser.timeZone = TimeZone(identifier: "UTC")
        parser.dateFormat = "yyyy-MM-dd"
        guard let date = parser.date(from: text) else { return "Se restablece el \(text)" }
        var style = Date.FormatStyle().day().month(.abbreviated).locale(spanish)
        style.timeZone = TimeZone(identifier: "UTC") ?? .current
        return "Se restablece el \(date.formatted(style))"
    }

}
