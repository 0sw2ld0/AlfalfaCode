# AlfalfaCode.app autónoma

`build-app.sh` hace una app que funciona sin el repositorio: quien la descarga abre el DMG,
la arrastra a Aplicaciones y la abre. No hace falta clonar nada, ni un `.venv`, ni pip, ni
las herramientas de línea de órdenes de Xcode para el motor.

```bash
cd alfalfa-mac
./build-app.sh              # dist/AlfalfaCode.app (Developer ID si hay certificado; si no, ad hoc)
./publicar.sh --local       # dist/AlfalfaCode-<versión>-local.dmg, ad hoc y sin notarizar: sólo este Mac
./publicar.sh               # dist/AlfalfaCode-<versión>.dmg firmado, notarizado y grapado
```

## Qué lleva dentro

```
AlfalfaCode.app/Contents/
  MacOS/AlfalfaCode                 la app de Mac (SwiftUI)
  Helpers/alfalfa-firma-<huella>    firmante.swift ya compilado (Secure Enclave, proveedor Alfalfa)
  Helpers/alfalfa-apps-<huella>     ayudante.swift ya compilado (control de apps)
  Resources/AppIcon.icns
  Resources/motor/alfalfa           lanzador: python3 -I -B -m alfalfa.terminal
  Resources/motor/python/           CPython 3.13 de uv (python-build-standalone)
      lib/python3.13/site-packages/ alfalfa, httpx, mcp, jsonschema, cuysearch… (motor-requisitos.txt)
```

- La app busca el motor en este orden (`Launcher.locate`): `ALFALFA_REPO/scripts/alfalfa` si
  esa variable existe (modo desarrollo con la app empaquetada), el del bundle, y por último
  `scripts/alfalfa` subiendo desde el ejecutable (la app compilada con `swift run` en un clon).
- El motor nunca escribe en el bundle: el bytecode va compilado con huella
  (`--invalidation-mode unchecked-hash`), el lanzador usa `-B`, y `alfalfa --actualizar` no
  instala nada dentro de la app. `build-app.sh` lo comprueba al final.
- `-I` aísla el Python: no lee `PYTHONPATH` ni el site del usuario, y no pone la carpeta del
  proyecto en `sys.path` (un `json.py` del proyecto no se importaría).
- Las versiones de las dependencias están fijadas en `motor-requisitos.txt` (se versiona).
  `REGENERAR_REQUISITOS=1 ./build-app.sh` las actualiza.
- Las preferencias de la app compilada con `swift run` (dominio `AlfalfaCode`) se copian una
  vez al de la app empaquetada (`com.cuycoders.alfalfacode`).

## Sólo Apple Silicon

La app sale para arm64. El Python de uv no se publica en versión universal: hacerla a mano
obliga a unir con `lipo` el intérprete, `libpython` y cada extensión nativa (pydantic-core,
rpds-py, cryptography, cffi) de dos instalaciones distintas, y a probar las dos. macOS 26 es el
último que corre en Intel, Ollama local en Intel va muy lento y el Secure Enclave del proveedor
Alfalfa sólo existe en Intel con chip T2. Si hiciera falta Intel, lo razonable es un segundo
DMG x86_64 hecho con el mismo script (`PYTHON_CLAVE` x86_64 y `--arch x86_64`), no uno universal.

## Firma y notarización

Se firma de dentro hacia fuera: las bibliotecas (`.so`, `.dylib`), después los ejecutables
(el Python con `Motor.entitlements`, los dos ayudantes) y al final la app con
`AlfalfaCode.entitlements`. Con «Developer ID Application» cada binario lleva hardened runtime
y sello de tiempo, que es lo que pide la notarización. El Python no necesita desactivar la
validación de bibliotecas: todas sus extensiones van firmadas por el mismo equipo.

`publicar.sh` sigue el modelo de CuyCleaner: notariza y grapa la app, hace el DMG con un acceso
a Aplicaciones, lo firma, lo notariza, lo grapa y lo comprueba con `spctl`. La credencial de
notarytool se guarda una vez en el llavero con el nombre `AlfalfaCode`.

## Qué necesita el motor del Mac de quien lo usa

| Qué | Si falta | Cómo se resuelve |
|---|---|---|
| `firmante.swift`, `ayudante.swift` | En un clon sin `swiftc`, el código del proveedor Alfalfa y el control de apps fallan con «necesita swiftc». | La app los lleva compilados en `Contents/Helpers`; el motor los usa antes de intentar compilar. |
| Perfiles `*.sbpl` de Seatbelt | Sin ellos no hay sandbox y no se ejecuta ninguna orden. | Van dentro del paquete `alfalfa` (datos del paquete); `sandbox-exec` es parte de macOS. `build-app.sh` comprueba que están. |
| `git` | `/usr/bin/git` es un atajo de las herramientas de línea de órdenes: sin ellas, la primera llamada abre el diálogo de macOS para instalarlas. Mientras tanto las sesiones salen sin rama ni cambios y las órdenes `git` de los agentes fallan. | Instalarlas (`xcode-select --install`, o aceptar ese diálogo). El aviso de versiones de la app ya no usa git: consulta GitHub por HTTPS. |
| `rg` | El motor no lo llama; sólo un agente puede pedirlo en una orden. Falla con «command not found» y el modelo usa `grep`. | Opcional: `brew install ripgrep`. Se podría incluir en `Helpers` (5 MB) si se ve que los modelos insisten. |
| `codex` | Sólo se usa para delegar la visión a Codex; sin él, esa subtarea dice «requiere Codex instalado». | Instalar Codex con su propia cuenta. No se incluye: es un producto de otro con su propio inicio de sesión. |
| Ollama | Sin Ollama ni otro proveedor no hay modelo. | Instalar Ollama, o configurar OpenAI o el servicio Alfalfa en Ajustes ▸ Proveedores. |
| Xcode, `swift`, `node`… del proyecto | Los agentes compilan y prueban con las herramientas del usuario. | Son las del proyecto: las instala quien programa. |

## Actualizaciones

Hoy la app empaquetada comprueba si hay versión nueva (etiquetas de GitHub, por HTTPS) y, si
la hay, su botón «Descargar» abre la página de la última versión: se descarga el DMG y se
arrastra a Aplicaciones. `alfalfa --actualizar` dentro de la app no toca el bundle y lo explica.
La actualización automática dentro de la app está pendiente de decidir (ver el PR).
