extension Array {

    /// El elemento en `index`, o nil si se sale de la lista.
    subscript(safe index: Int) -> Element? { indices.contains(index) ? self[index] : nil }
}
