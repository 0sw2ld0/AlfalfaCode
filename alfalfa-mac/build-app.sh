#!/bin/bash
# Ensambla dist/AlfalfaCode.app autónoma: la app de Mac, un Python propio y el motor
# `alfalfa` con sus dependencias (httpx, mcp, jsonschema, cuysearch). Quien la instale no
# necesita clonar el repositorio, ni un .venv, ni pip, ni las herramientas de línea de órdenes.
#
# Un procesador por app: Apple Silicon (arm64, por defecto) o Intel (ARQ=x86_64), no universal.
# El Python de uv (python-build-standalone) no se publica en versión universal, y hacerla a mano
# obliga a unir con lipo el intérprete y cada extensión nativa (pydantic-core, rpds-py,
# cryptography, cffi) de dos instalaciones distintas. Ver alfalfa-mac/EMPAQUETADO.md.
#
# Uso:  ./build-app.sh                       → firma Developer ID si hay certificado, si no ad hoc
#       ARQ=x86_64 ./build-app.sh            → la app para Intel, en dist/x86_64/ (en Apple
#                                              Silicon, con Rosetta para comprobar el motor)
#       IDENTIDAD=- ./build-app.sh           → firma ad hoc aunque haya certificado (para este Mac)
#       IDENTIDAD=- CONFIGURACION=debug ./build-app.sh → compila una app de depuración
#       REGENERAR_REQUISITOS=1 ./build-app.sh → vuelve a fijar las versiones de motor-requisitos.txt
set -euo pipefail
cd "$(dirname "$0")"
MAC="$(pwd)"
RAIZ="$(cd .. && pwd)"
ARQ="${ARQ:-arm64}"
case "$ARQ" in
  arm64)  PYTHON_ARQ="aarch64"; APP="dist/AlfalfaCode.app" ;;
  x86_64) PYTHON_ARQ="x86_64";  APP="dist/x86_64/AlfalfaCode.app"
          # Las comprobaciones del final arrancan el motor: en Apple Silicon, con Rosetta.
          arch -x86_64 /usr/bin/true 2>/dev/null \
            || { echo "✗ para la app de Intel en Apple Silicon hace falta Rosetta: softwareupdate --install-rosetta" >&2; exit 1; } ;;
  *) echo "✗ ARQ debe ser arm64 o x86_64" >&2; exit 1 ;;
esac
. "$RAIZ/distribucion/cuybrowser.sh"
. "$RAIZ/distribucion/alfalfadesktop.sh"

CACHE=".build/empaquetado"
REQUISITOS="motor-requisitos.txt"
VERSION="${VERSION:-$(sed -n "s/^__version__ = '\(.*\)'$/\1/p" "$RAIZ/alfalfa/__init__.py")}"
COMPILACION="${COMPILACION:-$(date +%Y%m%d%H%M)}"
PYTHON_VERSION="${PYTHON_VERSION:-3.13.14}"
PYTHON_CLAVE="cpython-$PYTHON_VERSION-macos-$PYTHON_ARQ-none"
PYTHON_MENOR="${PYTHON_VERSION%.*}"
CONFIGURACION="${CONFIGURACION:-release}"
[ -n "$VERSION" ] || { echo "✗ no se pudo leer __version__ de alfalfa/__init__.py" >&2; exit 1; }
command -v uv >/dev/null || { echo "✗ falta uv (brew install uv)" >&2; exit 1; }
case "$CONFIGURACION" in
  debug|release) ;;
  *) echo "✗ CONFIGURACION debe ser debug o release" >&2; exit 1 ;;
esac

CUYBROWSER_ORIGEN="$CACHE/$CUYBROWSER_ASSET-$CUYBROWSER_VERSION"
cuybrowser_preparar "$CUYBROWSER_ORIGEN"
ALFALFADESKTOP_ORIGEN="$CACHE/$ALFALFADESKTOP_ASSET-$ALFALFADESKTOP_VERSION"
alfalfadesktop_preparar "$ALFALFADESKTOP_ORIGEN"

# --- 1. Python propio (en caché: sólo se descarga la primera vez) ---------------------
PYTHON_ORIGEN="$CACHE/python/$PYTHON_CLAVE"
if [ ! -x "$PYTHON_ORIGEN/bin/python3" ]; then
  uv python install "$PYTHON_CLAVE" --install-dir "$CACHE/python" --no-bin
fi

# --- 2. Versiones fijadas del motor ----------------------------------------------------
# Se versionan: la app que se publica lleva exactamente lo que se probó. La de Intel tiene su
# archivo: cryptography dejó de publicar ruedas para Mac con Intel en la 49 (sólo arm64), así que
# allí se queda en la última que las trae. Lo demás es igual en las dos.
if [ "$ARQ" = x86_64 ]; then
  REQUISITOS="motor-requisitos-x86_64.txt"
  CABECERA=("# Versiones que lleva el motor de la app para Intel (ARQ=x86_64 ./build-app.sh): las mismas que"
            "# motor-requisitos.txt salvo cryptography, que desde la 49 ya no publica ruedas para Mac con Intel"
            "# (sólo arm64). Para cambiarlas: ARQ=x86_64 REGENERAR_REQUISITOS=1 ./build-app.sh")
  mkdir -p "$CACHE"
  echo 'cryptography<49' > "$CACHE/restricciones-x86_64.txt"
  RESTRICCIONES=(--constraint "$CACHE/restricciones-x86_64.txt")
else
  CABECERA=("# Versiones que lleva el motor de AlfalfaCode.app (build-app.sh). Para cambiarlas:"
            "# REGENERAR_REQUISITOS=1 ./build-app.sh")
  RESTRICCIONES=()
fi
if [ ! -f "$REQUISITOS" ] || [ -n "${REGENERAR_REQUISITOS:-}" ]; then
  { printf '%s\n' "${CABECERA[@]}" ""
    uv pip compile "$RAIZ/pyproject.toml" --python-version "$PYTHON_MENOR" ${RESTRICCIONES[@]+"${RESTRICCIONES[@]}"} \
       --python-platform "$PYTHON_ARQ-apple-darwin" --no-header --no-annotate --quiet
  } > "$REQUISITOS.tmp"
  mv "$REQUISITOS.tmp" "$REQUISITOS"
fi

# --- 3. La app de Mac ------------------------------------------------------------------
swift build --disable-keychain --disable-netrc -c "$CONFIGURACION" --arch "$ARQ"
BIN="$(swift build --disable-keychain --disable-netrc -c "$CONFIGURACION" --arch "$ARQ" --show-bin-path)/AlfalfaCode"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$APP/Contents/Helpers"
cp "$BIN" "$APP/Contents/MacOS/AlfalfaCode"
CUYBROWSER="$APP/Contents/Helpers/alfalfabrowser"
install -m 755 "$CUYBROWSER_ORIGEN" "$CUYBROWSER"
cuybrowser_huella_correcta "$CUYBROWSER" \
  || { echo "✗ AlfalfaBrowser copiado no coincide con su huella SHA-256 fijada" >&2; exit 1; }
ALFALFADESKTOP="$APP/Contents/Helpers/alfalfa-desktop"
install -m 755 "$ALFALFADESKTOP_ORIGEN" "$ALFALFADESKTOP"
alfalfadesktop_huella_correcta "$ALFALFADESKTOP" \
  || { echo "✗ AlfalfaDesktop copiado no coincide con su huella SHA-256 fijada" >&2; exit 1; }
# El icono va en Resources: la app empaquetada no usa la carpeta de recursos de SwiftPM.
cp Sources/AlfalfaCode/Resources/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>               <string>AlfalfaCode</string>
  <key>CFBundleDisplayName</key>        <string>AlfalfaCode</string>
  <key>CFBundleIdentifier</key>         <string>com.cuycoders.alfalfacode</string>
  <key>CFBundleDevelopmentRegion</key>  <string>es</string>
  <key>CFBundleLocalizations</key>      <array><string>es</string></array>
  <key>CFBundleVersion</key>            <string>$COMPILACION</string>
  <key>CFBundleShortVersionString</key> <string>$VERSION</string>
  <key>AlfalfaBrowserVersion</key>    <string>$CUYBROWSER_VERSION</string>
  <key>AlfalfaDesktopVersion</key>       <string>$ALFALFADESKTOP_VERSION</string>
  <key>CFBundleExecutable</key>         <string>AlfalfaCode</string>
  <key>CFBundlePackageType</key>        <string>APPL</string>
  <key>CFBundleIconFile</key>           <string>AppIcon</string>
  <key>LSMinimumSystemVersion</key>     <string>14.0</string>
  <key>LSArchitecturePriority</key>     <array><string>$ARQ</string></array>
  <key>NSHighResolutionCapable</key>    <true/>
  <key>NSHumanReadableCopyright</key>   <string>© CuyCoders · MIT</string>
  <!-- Un agente puede usar osascript; macOS pregunta antes en nombre de la app. -->
  <key>NSAppleEventsUsageDescription</key>
  <string>Un agente de AlfalfaCode quiere controlar otra app con AppleScript. Sólo pasa si se lo pides en un encargo.</string>
</dict>
</plist>
PLIST
plutil -lint "$APP/Contents/Info.plist" >/dev/null

# --- 4. El motor: Python + alfalfa + dependencias ---------------------------------------
MOTOR="$APP/Contents/Resources/motor"
PY="$MOTOR/python/bin/python3"
LIB="$MOTOR/python/lib/python$PYTHON_MENOR"
mkdir -p "$MOTOR"
ditto "$PYTHON_ORIGEN" "$MOTOR/python"
ditto "$RAIZ/distribucion/licencias/cuybrowser" "$MOTOR/licencias/cuybrowser"
ditto "$RAIZ/distribucion/licencias/alfalfadesktop" "$MOTOR/licencias/alfalfadesktop"

# Lo que un motor sin interfaz gráfica ni compilador no usa: menos peso y menos binarios que firmar.
rm -rf "$MOTOR/python/include" "$MOTOR/python/share" "$MOTOR/python/lib/pkgconfig" \
       "$MOTOR/python/lib/"{tcl,tk,itcl,thread}* "$MOTOR/python/lib/lib"{tcl,tk}* \
       "$LIB/"{test,idlelib,tkinter,turtledemo,ensurepip,lib2to3} "$LIB/config-$PYTHON_MENOR-darwin" \
       "$LIB/lib-dynload/_tkinter"*.so "$LIB/site-packages/"pip*
find "$MOTOR/python/bin" -mindepth 1 \( -type f -o -type l \) \
     ! -name python3 ! -name "python$PYTHON_MENOR" -delete
find "$MOTOR/python" -name __pycache__ -type d -prune -exec rm -rf {} +

uv pip install --python "$PY" --target "$LIB/site-packages" --quiet -r "$REQUISITOS"
# setuptools deja build/ en el repositorio: uno viejo metería archivos que ya no existen.
rm -rf "$RAIZ/build" "$RAIZ/alfalfa.egg-info"
uv pip install --python "$PY" --target "$LIB/site-packages" --quiet --no-deps --reinstall-package alfalfa "$RAIZ"
rm -rf "$RAIZ/build" "$RAIZ/alfalfa.egg-info"
for f in alfalfa/seatbelt/seatbelt_base_policy.sbpl alfalfa/proveedores/firmante.swift alfalfa/apps/ayudante.swift; do
  [ -f "$LIB/site-packages/$f" ] || { echo "✗ falta $f en el motor" >&2; exit 1; }
done
[ ! -e "$LIB/site-packages/alfalfa-mac" ] || { echo "✗ el paquete arrastra alfalfa-mac/" >&2; exit 1; }
rm -rf "$LIB/site-packages/bin"   # los lanzadores de pip apuntan a rutas de esta compilación

# Bytecode compilado aquí y con huella (no con fecha): la app nunca escribe .pyc en el bundle
# firmado (el lanzador usa -B) y lo que se copia del DMG sigue siendo válido.
"$PY" -I -m compileall -q -j 0 --invalidation-mode unchecked-hash "$LIB" >/dev/null \
  || echo "aviso: compileall no pudo compilar algún archivo (se interpretará al importarlo)"

# rg (ripgrep): el binario oficial, fijado por versión y huella. Sólo enlaza bibliotecas de macOS.
RG_VERSION="15.2.0"
if [ "$ARQ" = x86_64 ]; then
  RG_SHA256="af7825fcc69a2afc7a7aea55fc9af90e26421d8f20fe59df32e233c0b8a231c1"
else
  RG_SHA256="3750b2e93f37e0c692657da574d7019a101c0084da05a790c83fd335bad973e4"
fi
RG_NOMBRE="ripgrep-$RG_VERSION-$PYTHON_ARQ-apple-darwin"
RG_TGZ="$CACHE/$RG_NOMBRE.tar.gz"
rg_valido() { [ -f "$RG_TGZ" ] && [ "$(shasum -a 256 "$RG_TGZ" | cut -d' ' -f1)" = "$RG_SHA256" ]; }
if ! rg_valido; then
  curl -fsSL -o "$RG_TGZ" "https://github.com/BurntSushi/ripgrep/releases/download/$RG_VERSION/$RG_NOMBRE.tar.gz"
  rg_valido || { rm -f "$RG_TGZ"; echo "✗ ripgrep descargado no coincide con su huella" >&2; exit 1; }
fi
mkdir -p "$MOTOR/bin" "$MOTOR/licencias/ripgrep" "$MOTOR/sin-herramientas"
tar -xzf "$RG_TGZ" -C "$MOTOR/licencias/ripgrep" --strip-components 1 \
    "$RG_NOMBRE/rg" "$RG_NOMBRE/COPYING" "$RG_NOMBRE/UNLICENSE" "$RG_NOMBRE/LICENSE-MIT"
mv "$MOTOR/licencias/ripgrep/rg" "$MOTOR/bin/rg"
otool -L "$MOTOR/bin/rg" | tail -n +2 | grep -qv '^[[:space:]]*/usr/lib/' \
  && { echo "✗ rg enlaza bibliotecas que no son de macOS" >&2; exit 1; }

# Sin las herramientas de línea de órdenes de Apple, /usr/bin/git es un atajo que abre el
# diálogo de macOS para instalarlas. Este git va delante en ese caso: usa otro git de verdad
# si lo hay (Homebrew, por ejemplo) y, si no, explica qué pasa y sale con error.
cat > "$MOTOR/sin-herramientas/git" <<'GIT'
#!/bin/sh
yo=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
viejo_ifs=$IFS; IFS=:
for d in $PATH; do
  case "$d" in ""|"$yo"|/usr/bin) continue ;; esac
  if [ -x "$d/git" ]; then IFS=$viejo_ifs; exec "$d/git" "$@"; fi
done
echo "AlfalfaCode: git no está disponible en este Mac. macOS lo trae con sus herramientas de línea de órdenes, que no están instaladas. Se puede trabajar sin git, pero sin ramas, diffs ni commits. Para tenerlo: xcode-select --install" >&2
exit 1
GIT
chmod 755 "$MOTOR/sin-herramientas/git"

cat > "$MOTOR/alfalfa" <<'LANZADOR'
#!/bin/sh
# El motor de AlfalfaCode dentro de la app. -I: ni PYTHONPATH, ni el site del usuario, ni la
# carpeta actual (la del proyecto) en sys.path. -B: no escribe .pyc en el bundle firmado.
motor=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# Lo que trae la app (rg) va detrás: si el usuario tiene el suyo, manda el suyo.
PATH="${PATH:-/usr/bin:/bin}:$motor/bin"
# Sin las herramientas de Apple, delante va el git que no abre el diálogo de instalarlas.
/usr/bin/xcode-select -p >/dev/null 2>&1 || PATH="$motor/sin-herramientas:$PATH"
export PATH
exec "$motor/python/bin/python3" -I -B -m alfalfa.terminal "$@"
LANZADOR
chmod 755 "$MOTOR/alfalfa"

# --- 5. Ayudantes Swift ya compilados -----------------------------------------------------
# alfalfa los busca en Contents/Helpers con el nombre que tendrían en $ALFALFA_HOME/bin
# (prefijo + huella de la fuente). Así no hace falta swiftc en el Mac de quien la usa.
huella() { shasum -a 256 "$1" | cut -c1-12; }
FIRMANTE="$LIB/site-packages/alfalfa/proveedores/firmante.swift"
AYUDANTE="$LIB/site-packages/alfalfa/apps/ayudante.swift"
export CLANG_MODULE_CACHE_PATH="$MAC/$CACHE/clang"
swiftc -O -target "$ARQ-apple-macos14" "$FIRMANTE" -o "$APP/Contents/Helpers/alfalfa-firma-$(huella "$FIRMANTE")"
swiftc -O -target "$ARQ-apple-macos14" "$AYUDANTE" -o "$APP/Contents/Helpers/alfalfa-apps-$(huella "$AYUDANTE")"

# --- 6. Firma: de dentro hacia fuera --------------------------------------------------------
# Con «Developer ID Application»: hardened runtime y sello de tiempo en cada binario, que es
# lo que pide la notarización. Sin él, ad hoc: vale para este Mac, Gatekeeper la bloquea en otros.
IDENTIDAD="${IDENTIDAD:-$(security find-identity -v -p codesigning 2>/dev/null \
  | sed -n 's/.*"\(Developer ID Application: .*\)"/\1/p' | head -1)}"
IDENTIDAD="${IDENTIDAD:--}"
firmar() {   # firmar <ruta> [entitlements]
  if [ "$IDENTIDAD" = "-" ]; then
    codesign --force --sign - "$1"
  else
    codesign --force --options runtime --timestamp ${2:+--entitlements "$2"} --sign "$IDENTIDAD" "$1"
  fi
}
xattr -cr "$APP"
BINARIOS="$(mktemp)"; trap 'rm -f "$BINARIOS"' EXIT
find "$MOTOR" "$APP/Contents/Helpers" -type f \( -name '*.so' -o -name '*.dylib' -o -perm -u+x \) -print0 \
  | while IFS= read -r -d '' f; do file -b "$f" | grep -q '^Mach-O' && echo "$f"; done > "$BINARIOS"
# Primero las bibliotecas, después los ejecutables que las cargan.
n=0
while IFS= read -r f; do case "$f" in *.so|*.dylib) firmar "$f"; n=$((n+1));; esac; done < "$BINARIOS"
while IFS= read -r f; do
  case "$f" in
    *.so|*.dylib) ;;
    */python/bin/*) firmar "$f" Motor.entitlements; n=$((n+1)) ;;
    "$ALFALFADESKTOP") codesign --verify --strict "$f" ;; # conserva la firma Developer ID original
    "$CUYBROWSER") cuybrowser_firmar "$f" "$IDENTIDAD" CuyBrowser.entitlements; n=$((n+1)) ;;
    *) firmar "$f"; n=$((n+1)) ;;
  esac
done < "$BINARIOS"
firmar "$APP" AlfalfaCode.entitlements
codesign --verify --deep --strict "$APP"
echo "firmados $n binarios del motor y la app con: $([ "$IDENTIDAD" = "-" ] && echo 'ad hoc (sólo este Mac)' || echo "$IDENTIDAD")"

# --- 7. Comprobación: el motor arranca desde la app y no escribe en ella ---------------------
# Cada binario tiene que correr en $ARQ: una rueda de otra plataforma no avisa hasta que se carga.
echo "$APP/Contents/MacOS/AlfalfaCode" >> "$BINARIOS"
while IFS= read -r f; do
  lipo -archs "$f" | tr ' ' '\n' | grep -qx "$ARQ" \
    || { echo "✗ $f no es para $ARQ ($(lipo -archs "$f"))" >&2; exit 1; }
done < "$BINARIOS"
echo "todos los binarios son para $ARQ"
MARCA="$(mktemp)"
PRUEBA_HOME="$(mktemp -d)"
env -i HOME="$PRUEBA_HOME" PATH=/usr/bin:/bin "$MOTOR/alfalfa" --version
env -i HOME="$PRUEBA_HOME" PATH=/usr/bin:/bin "$PY" -I -B -c \
  'import alfalfa.terminal, alfalfa.empaquetado as e, cuysearch, mcp, jsonschema, httpx; assert e.en_la_app(), "no se ve dentro de la app"'
cuybrowser_comprobar_app "$APP" "$PRUEBA_HOME"
echo "AlfalfaBrowser $CUYBROWSER_VERSION: firma y arranque comprobados."
alfalfadesktop_comprobar_app "$APP" "$PRUEBA_HOME"
echo "AlfalfaDesktop $ALFALFADESKTOP_VERSION: firma y arranque comprobados."
"$MOTOR/bin/rg" --version | head -1
# Sin otro git en el PATH, el de sustitución explica y falla; nunca llama a /usr/bin/git.
AVISO_GIT="$(env -i PATH=/usr/bin:/bin "$MOTOR/sin-herramientas/git" status 2>&1 || true)"
if [[ "$AVISO_GIT" == *"git no está disponible"* ]]; then
  echo "git de sustitución: ok"
else
  echo "✗ el git de sustitución no avisa" >&2; exit 1
fi
if [ -n "$(find "$APP" -newer "$MARCA" -print -quit)" ]; then
  echo "✗ el motor escribió dentro de la app:" >&2; find "$APP" -newer "$MARCA" >&2; exit 1
fi
rm -rf "$MARCA" "$PRUEBA_HOME"
codesign --verify --deep --strict "$APP"

echo "listo -> $MAC/$APP  (versión $VERSION, compilación $COMPILACION, $(du -sh "$APP" | cut -f1))"
