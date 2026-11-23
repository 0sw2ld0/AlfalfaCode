# Instalar

En un Mac con Apple Silicon y macOS 14 o posterior, sin Python, git ni Xcode:

```bash
brew install --cask 0sw2ld0/tap/alfalfacode   # la app de Mac
brew install 0sw2ld0/tap/alfalfa              # la CLI
```

o, sólo la CLI y sin Homebrew ni sudo:

```bash
curl -fsSL https://alfalfacode.cuycoders.dev/instalar.sh | sh
```

También vale bajar el DMG de la
[última release](https://github.com/0sw2ld0/AlfalfaCode/releases/latest) y
arrastrar la app a Aplicaciones.

**En un Mac con Intel** (también macOS 14 o posterior), Homebrew todavía no sirve: usa
`instalar.sh`, que baja solo la versión para Intel, o el DMG `AlfalfaCode-<versión>-intel.dmg`
de la release. La app de Intel se actualiza sola con los DMG de Intel. Sin chip T2 no hay
Secure Enclave, así que el código de cortesía del proveedor Alfalfa no se puede activar;
los demás proveedores funcionan igual.

Las tres formas usan el mismo DMG de la release, firmado y notarizado:

- **El cask** instala la app. Se actualiza sola (`auto_updates`), así que
  `brew upgrade` no la toca.
- **La fórmula** instala la CLI desde un bottle, que es el motor de ese DMG tal
  cual. Homebrew no reescribe sus binarios, así que siguen con la firma Developer ID
  del equipo FZ8TW9AWLE.
  No compila nada.
- **`instalar.sh`** ([alfalfa/instalar.sh](../../alfalfa/instalar.sh)) hace esto:
  1. Lee `version.json` de la última release (en Intel, `version-intel.json`).
  2. Baja el DMG y comprueba su huella SHA-256.
  3. Monta el DMG en sólo lectura y comprueba la firma de la app: que sea del
     equipo FZ8TW9AWLE, con el identificador `com.cuycoders.alfalfacode`, la
     misma versión, notarizada (`spctl`) y del procesador de este Mac.
  4. Copia el motor a `~/.local/share/alfalfa` y deja `~/.local/bin/alfalfa`.
  5. Si hace falta, añade `~/.local/bin` al `PATH` en `~/.zshrc`, entre marcas.
     Con `ALFALFA_SIN_PATH=1` no toca ese archivo.

  `ALFALFA_PREFIJO` cambia la carpeta de destino, que por defecto es `~/.local`.

Para desinstalar:

```bash
brew uninstall alfalfa && brew uninstall --cask alfalfacode
curl -fsSL https://alfalfacode.cuycoders.dev/instalar.sh | sh -s -- --desinstalar
```

Ninguna de las dos borra `~/.alfalfa`, donde viven tus sesiones y ajustes.

## Desde el código

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Después `alfalfa` queda en `.venv/bin/`. También se puede lanzar con
`scripts/alfalfa`, que admite un enlace simbólico desde cualquier carpeta del `PATH`.

## La app de Mac desde el código

Hacen falta macOS 14 o posterior y Xcode (o sus herramientas de línea de órdenes) con Swift 5.9
o posterior. La app no lleva el motor dentro cuando se compila así: usa el `scripts/alfalfa`
del repositorio, que a su vez necesita el `.venv` del apartado anterior. Así que primero el
motor y después la app:

```bash
git clone https://github.com/0sw2ld0/AlfalfaCode.git
cd AlfalfaCode
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cd alfalfa-mac
swift run AlfalfaCode
```

La primera vez, `swift run` baja la dependencia CuyTerm y compila la app; tarda un poco.

La app busca el motor subiendo desde la carpeta actual y desde su ejecutable hasta encontrar
`scripts/alfalfa`. Si la lanzas desde fuera del clon, indícale dónde está con `ALFALFA_REPO`:

```bash
ALFALFA_REPO=~/ruta/a/AlfalfaCode swift run --package-path ~/ruta/a/AlfalfaCode/alfalfa-mac AlfalfaCode
```

Si falta el `.venv`, la app avisa con «falta el .venv del repositorio».

### Una AlfalfaCode.app para este Mac

Para tener la app en Aplicaciones, con su propio Python y el motor dentro y sin depender del
clon, empaquétala con `build-app.sh`. Necesita además [uv](https://docs.astral.sh/uv/)
(`brew install uv`) y conexión, porque baja el Python, ripgrep y CuyBrowser:

```bash
cd alfalfa-mac
IDENTIDAD=- ./build-app.sh
```

La app sale en `alfalfa-mac/dist/AlfalfaCode.app` con firma ad hoc, que sólo vale en este Mac.
Cópiala a `/Applications`. Sin `IDENTIDAD=-` usa tu certificado Developer ID si hay uno. El DMG
firmado y notarizado (`publicar.sh`) y la versión para Intel están en
[alfalfa-mac/EMPAQUETADO.md](../../alfalfa-mac/EMPAQUETADO.md).
