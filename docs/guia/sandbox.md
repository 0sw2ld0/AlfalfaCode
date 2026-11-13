# SwiftPM y apps dentro del sandbox

SwiftPM abre su propio sandbox y macOS no deja anidarlo. Dentro del de AlfalfaCode,
`swift build/run/test/package` recibe `--disable-sandbox`, también cuando lo llama
un script (`sh "Abrir app.command"`, `make`): las órdenes del sandbox llevan en el
PATH envoltorios de `swift`, `swift-build`… y `xcrun`. Abrir una app (`open`) no
funciona dentro del sandbox; el agente lo pide directamente con
`sandbox_permissions: "require_escalated"`, como en Codex.
