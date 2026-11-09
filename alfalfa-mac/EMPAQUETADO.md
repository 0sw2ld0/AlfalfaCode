# AlfalfaCode.app autónoma

`build-app.sh` hace una app que funciona sin el repositorio: quien la descarga abre el DMG,
la arrastra a Aplicaciones y la abre. Después no se instala nada más: ni clonar, ni un `.venv`,
ni pip, ni las herramientas de línea de órdenes de Apple.

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
  Resources/motor/bin/rg            ripgrep 15.2.0 oficial (fijado por huella), al final del PATH
  Resources/motor/sin-herramientas/git  delante en el PATH sólo si faltan las herramientas de Apple
  Resources/motor/licencias/        licencias de lo que va dentro y no es nuestro
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

Nada que haya que instalar después de copiar la app.

| Qué | Cómo va | Si falta |
|---|---|---|
| Python y dependencias (`cuysearch` incluida) | Dentro, en `motor/python`. | — |
| `firmante.swift`, `ayudante.swift` | Compilados al empaquetar, en `Contents/Helpers`; el motor los usa antes de intentar compilar. | — (en un clon sin `swiftc` fallan con «necesita swiftc») |
| Perfiles `*.sbpl` de Seatbelt | Dentro del paquete `alfalfa`; `sandbox-exec` es parte de macOS. `build-app.sh` comprueba que están. | — |
| `rg` | Dentro, en `motor/bin`, al final del PATH: si el usuario tiene el suyo, se usa el suyo. Funciona dentro del sandbox. | — |
| `git` | No va dentro (ver abajo). | Sin las herramientas de Apple, `/usr/bin/git` es un atajo que abre el diálogo de macOS para instalarlas. Para que eso no pase, el lanzador pone delante `sin-herramientas/git`: usa otro git del PATH si existe (Homebrew) y, si no, falla con un aviso claro. El agente lo sabe desde el principio (`- Git: no está en este Mac` en su contexto) y trabaja sin ramas ni commits (si aun así llama a `/usr/bin/git` con la ruta entera, ese atajo puede abrir el diálogo). La app no ejecuta `/usr/bin/git` en ese caso y el panel Entorno dice «git no está en este Mac». |
| `codex` | No va dentro. | AlfalfaCode.app nunca lo usa: sólo una aplicación que integra el motor y registra `ajustes.vision_delegada` (Chiyo) delega en él la visión, y si falta lo dice («requiere Codex instalado»). |
| Ollama | — | Sin Ollama, se configura OpenAI o el servicio Alfalfa en Ajustes ▸ Proveedores. |

Las herramientas propias de cada proyecto (Xcode, `swift`, `node`, `python3`…) no son del
motor: si el agente las necesita, son las del usuario.

### Llevar git dentro (propuesta)

Lo que de verdad quita el requisito es compilar git al empaquetar, como `rg` pero desde su
fuente oficial (kernel.org, fijada por huella): reubicable (`RUNTIME_PREFIX`), sólo con
bibliotecas de macOS (CommonCrypto y el curl del sistema), con `git-credential-osxkeychain` y un
`etc/gitconfig` que lo usa. Coste: unos 15–25 MB más, un par de minutos de compilación (en
caché), y vigilar las versiones de seguridad de git en cada publicación. Es GPL-2.0: va como
programa aparte, con su licencia y el enlace a su fuente exacta.

## Actualizaciones

La app empaquetada se actualiza sola, con el mismo esquema que CuyCleaner
(`Sources/AlfalfaKit/AppUpdate.swift` las reglas, `Sources/AlfalfaCode/AppUpdater.swift` la parte
con interfaz):

1. Al abrirse, como mucho una vez cada 20 horas, lee
   `https://github.com/0sw2ld0/AlfalfaCode/releases/latest/download/version.json`
   (también desde Ajustes ▸ Actualizaciones o el menú «Buscar actualización»).
2. Si hay una versión más nueva, ofrece «Omitir esta versión», «Más tarde» o «Instalar y reiniciar».
3. Descarga el DMG (sólo de las releases de 0sw2ld0/AlfalfaCode por https), comprueba la huella
   SHA-256 de `version.json`, la firma (`codesign --verify --deep --strict`), que el equipo sea
   FZ8TW9AWLE, el identificador y la versión del `Info.plist`, y la notarización (`spctl`).
4. Copia la app nueva junto a la instalada, cierra, la cambia y la vuelve a abrir. Si algo falla
   no se toca la instalada. Si macOS abrió la app desde una copia temporal (App Translocation)
   explica que hay que moverla a Aplicaciones con Finder.

`publicar.sh` escribe `dist/version.json` y enseña la orden `gh release create` para subir el DMG y
ese archivo a la release (que tiene que quedar como «Latest»). Las notas salen de
`NOTAS-VERSION.txt`.

Para probar el circuito sin GitHub: un `version.json` local que apunte a un DMG local y
`open AlfalfaCode.app --env ALFALFA_ACTUALIZACION_FEED=file:///ruta/version.json --args -actualizarAhora`.
Sólo se acepta un feed `file://`, y con él sólo un DMG local.

Desde el clon (`swift run`) no cambia nada: `alfalfa --actualizar` actualiza el repositorio.
