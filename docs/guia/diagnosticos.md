# Errores al editar (servidores de lenguaje)

Como OpenCode, el agente ve los errores de lo que acaba de editar sin pedirlos: tras cada
`write_file`, `edit_file` o `apply_patch`, el resultado de esa misma llamada trae los
diagnósticos **nuevos** de los archivos tocados, con archivo, línea y columna, como
mucho 20, primero los errores. «Nuevo» se decide alineando con difflib las líneas de
antes y de después de la edición: un error viejo que bajó porque se añadieron líneas
encima no cuenta, y uno igual a otro que se arregló en otro sitio sí. En un parche de
varios archivos, lo de antes de cada uno se calcula con los demás también como antes.
Ningún documento queda abierto en el servidor entre ediciones, así que lo que cambie
una orden en disco se vuelve a leer.

```json
{"path": "Sources/Demo/Suma.swift", "before": "…", "after": "…",
 "diagnosticos": {"nuevos": ["Sources/Demo/Suma.swift:2:16 error [SourceKit] Cannot convert value of type 'String' to expected argument type 'Int'"],
                  "errores": 1, "revisado_con": ["sourcekit-lsp"],
                  "nota": "Diagnósticos automáticos tras editar: orientan, pero no son una comprobación…"}}
```

Usa lo que ya está instalado; no instala nada:

| Archivos | Servidor de lenguaje | Si no hay |
|---|---|---|
| Swift | `sourcekit-lsp` (viene con Xcode), con una base de datos de compilación propia. **Apagado por defecto** (ver abajo) | `swift build` o `swift test` (y SwiftLint si el proyecto lo usa) |
| Python | `basedpyright-langserver` o `pyright-langserver` | `ruff check` y, sin ruff (o si falla), los errores de sintaxis del propio Python |
| TypeScript/JavaScript | `typescript-language-server` (el del PATH antes que el de `node_modules`) | — |

Además, si el proyecto tiene su linter configurado (`ruff.toml` o `[tool.ruff]`,
la configuración de ESLint, `.swiftlint.yml`), se pasa sólo sobre los archivos tocados.
Un linter que sale con un código que no es de «sin avisos» o «con avisos» (configuración
rota, regla que no existe) cuenta como fallido, nunca como limpio.

- Los servidores arrancan la primera vez que hacen falta, siguen vivos durante la sesión
  (también para sus subagentes) y se cierran con ella.
- **No alargan el turno**: cada edición espera como mucho 3 s
  (`ALFALFA_DIAGNOSTICOS_ESPERA`). Lo que no contesta a tiempo se queda fuera y el
  resultado lo dice en `sin_respuesta` («sourcekit-lsp no contestó en 3 s (sigue
  arrancando…)»); un servidor que arranca sigue arrancando y sirve en la siguiente
  edición. Uno que no termina de arrancar en 90 s se da por perdido en la sesión.
  Con un paquete Swift, la primera edición prepara el paquete en el sandbox (unos 2 s, más
  la caché de módulos del SDK la primera vez en el Mac, que se comparte entre proyectos) y
  suele decir «prepara el paquete…»; después, centésimas. Si se cancela el turno, no queda
  nada corriendo.
- Un fallo de los diagnósticos nunca hace fallar la edición: la edición se hizo y el
  resultado lo dice.
- **No son una comprobación**: la evidencia sólo cuenta `run_checks`, y el resultado lo
  recuerda en `nota`. Que no haya diagnósticos nuevos no verifica nada.
- **Aislamiento y permisos**, porque estos programas cargan código del proyecto:
  - pyright, `typescript-language-server`, ESLint, ruff y SwiftLint corren en el mismo
    sandbox Seatbelt que las órdenes del agente (sin red, escribiendo sólo en el proyecto
    y en las carpetas temporales). Sin Seatbelt no se lanzan.
  - En **Manual** y **Aceptar ediciones**, donde apruebas cada orden, los que ejecutan
    código del proyecto (pyright con el intérprete de su configuración, tsserver con sus
    plugins, ESLint con su configuración JS) piden permiso una vez por sesión. Un programa
    de `node_modules/.bin` lo pide en cualquier modo. ruff y SwiftLint sólo leen y no lo
    piden. Un «no» también vale para toda la sesión.
  - `sourcekit-lsp` no puede ir en Seatbelt (su sourcekitd es un servicio XPC de Xcode que
    el sandbox bloquea): ejecuta el compilador de Swift sobre los archivos editados con tus
    permisos. Por eso viene **apagado**, como el LSP de OpenCode, y se enciende aparte.
    Encendido, pide su propio permiso, **«Diagnósticos de Swift (opcional)»** (motivo
    `diagnosticos_swift`), distinto del «Salir del sandbox» de las órdenes: es una función de
    AlfalfaCode, no una acción del agente. Una vez por paquete y sesión; un «no» sólo apaga
    los diagnósticos de Swift el resto de la sesión, en todos sus paquetes, y no se vuelve a
    preguntar (Omitir permisos lo concede solo). Apagado, el resultado de la primera edición
    de un `.swift` lo recuerda en una línea: se comprueba con `swift build` o `swift test`.
  - **`sourcekit-lsp` nunca evalúa un `Package.swift`.** Un manifiesto es código y puede pedir
    cualquier opción al compilador sin escribir su nombre (con `dlsym` o `@_silgen_name`,
    por ejemplo, cargar un programa del repositorio como plugin de macros), así que ninguna
    lista de nombres prohibidos lo cierra. En su lugar, AlfalfaCode evalúa el manifiesto
    **dentro del sandbox** con `swift package describe` y sólo se queda con datos: los
    objetivos de Swift, sus fuentes dentro del paquete y las dependencias entre ellos. Compila
    en el sandbox, con `swiftc -emit-module` y opciones fijas, los módulos de los objetivos de
    los que dependen otros, y escribe un `compile_commands.json` en
    `~/Library/Caches/alfalfa/sourcekit`, donde el sandbox de las órdenes no escribe. Sus únicas
    opciones son `-module-name`, `-sdk`, `-target`, `-swift-version`, `-parse-as-library` y
    `-I`/`-F` de esa caché y de la plataforma: ninguna sale del manifiesto. `sourcekit-lsp` se
    lanza en modo base de datos de compilación con esa carpeta como raíz y sólo recibe
    archivos que están en la base; uno que no estuviera (el propio `Package.swift`) le haría
    buscar un paquete. Los archivos sueltos van igual, cada uno con su orden fija. Si los
    ajustes no se pueden sacar así, ese paquete se queda sin diagnósticos de Swift (se dice):
    nunca se cae al modo SwiftPM.
  - Por eso no se cargan las macros ni los plugins del paquete, ni las dependencias de otros
    paquetes, ni los objetivos de C: los diagnósticos del código que los usa quedan
    incompletos. Los módulos de otros objetivos se vuelven a compilar cuando cambian sus fuentes.
  - `sourcekit-lsp` **no se lanza** si el proyecto trae algo que le haría ejecutar órdenes
    o cambiar su configuración (`buildServer.json`, `.bsp/`, `.sourcekit-lsp/`,
    `compile_commands.json`, `compile_flags.txt`, también en `build/`), desde la carpeta
    del archivo hasta la raíz. Si aparece uno con el servidor en marcha, se cierra. Si
    cambia el manifiesto o qué archivos hay, el paquete se prepara de nuevo y el servidor
    se reinicia con la base nueva.
  - Un programa del PATH que en realidad está dentro del proyecto (un `node_modules/.bin` o
    un `.venv/bin` del repo puesto en el PATH) cuenta como del proyecto y pide permiso.
- En la app, la fila de la edición enseña «2 errores · 1 aviso» y «incompleto» si algún
  servidor no contestó.

Está **encendido por defecto**. Para apagarlo:

- en la terminal, `ALFALFA_DIAGNOSTICOS=0 alfalfa "…"` o `alfalfa --sin-diagnosticos "…"`;
- para el usuario, `{"diagnosticos": false}` en `~/.alfalfa/ajustes.json`;
- en un proyecto, `{"diagnosticos": false}` en `.alfalfa/ajustes.json`. El proyecto sólo
  puede apagarlo: nunca enciende lo que el usuario apagó;
- en la app, **Ajustes ▸ General ▸ Ver los errores al editar**. Vale desde la siguiente
  edición, también en las sesiones abiertas. Por `--stdio`:
  `{"tipo":"diagnosticos","activo":false}`.

Los de Swift (`sourcekit-lsp`) están **apagados por defecto**. Para encenderlos:

- en la terminal, `ALFALFA_DIAGNOSTICOS_SWIFT=1 alfalfa "…"` o `alfalfa --diagnosticos-swift "…"`;
- para el usuario, `{"diagnosticos_swift": true}` en `~/.alfalfa/ajustes.json`;
- en la app, **Ajustes ▸ General ▸ Diagnósticos de Swift (sourcekit-lsp, fuera del sandbox)**.
  Por `--stdio`: `{"tipo":"diagnosticos_swift","activo":true}`.

Un proyecto puede apagarlos con `{"diagnosticos_swift": false}` en `.alfalfa/ajustes.json`,
pero nunca encenderlos.
