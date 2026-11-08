#!/bin/bash
# Ensambla dist/AlfalfaCode.app autónoma: la app de Mac, un Python propio y el motor
# `alfalfa` con sus dependencias (httpx, mcp, jsonschema, cuysearch). Quien la instale no
# necesita clonar el repositorio, ni un .venv, ni pip, ni las herramientas de línea de órdenes.
#
# Sólo Apple Silicon (arm64): el Python de uv (python-build-standalone) no se publica en
# versión universal, y hacerla a mano obliga a unir con lipo el intérprete y cada extensión
# nativa (pydantic-core, rpds-py, cryptography, cffi) de dos instalaciones distintas, y a
# probar las dos. macOS 26 es el último que corre en Intel. Ver alfalfa-mac/EMPAQUETADO.md.
#
# Uso:  ./build-app.sh                       → firma Developer ID si hay certificado, si no ad hoc
#       IDENTIDAD=- ./build-app.sh           → firma ad hoc aunque haya certificado (para este Mac)
#       REGENERAR_REQUISITOS=1 ./build-app.sh → vuelve a fijar las versiones de motor-requisitos.txt
set -euo pipefail
cd "$(dirname "$0")"
MAC="$(pwd)"
RAIZ="$(cd .. && pwd)"

APP="dist/AlfalfaCode.app"
CACHE=".build/empaquetado"
REQUISITOS="motor-requisitos.txt"
VERSION="${VERSION:-$(sed -n "s/^__version__ = '\(.*\)'$/\1/p" "$RAIZ/alfalfa/__init__.py")}"
COMPILACION="${COMPILACION:-$(date +%Y%m%d%H%M)}"
PYTHON_VERSION="${PYTHON_VERSION:-3.13.14}"
PYTHON_CLAVE="cpython-$PYTHON_VERSION-macos-aarch64-none"
PYTHON_MENOR="${PYTHON_VERSION%.*}"
[ -n "$VERSION" ] || { echo "✗ no se pudo leer __version__ de alfalfa/__init__.py" >&2; exit 1; }
command -v uv >/dev/null || { echo "✗ falta uv (brew install uv)" >&2; exit 1; }

# --- 1. Python propio (en caché: sólo se descarga la primera vez) ---------------------
PYTHON_ORIGEN="$CACHE/python/$PYTHON_CLAVE"
if [ ! -x "$PYTHON_ORIGEN/bin/python3" ]; then
  uv python install "$PYTHON_CLAVE" --install-dir "$CACHE/python" --no-bin
fi

# --- 2. Versiones fijadas del motor ----------------------------------------------------
# Se versionan: la app que se publica lleva exactamente lo que se probó.
if [ ! -f "$REQUISITOS" ] || [ -n "${REGENERAR_REQUISITOS:-}" ]; then
  { printf '%s\n' "# Versiones que lleva el motor de AlfalfaCode.app (build-app.sh). Para cambiarlas:" \
                  "# REGENERAR_REQUISITOS=1 ./build-app.sh" ""
    uv pip compile "$RAIZ/pyproject.toml" --python-version "$PYTHON_MENOR" \
       --python-platform aarch64-apple-darwin --no-header --no-annotate --quiet
  } > "$REQUISITOS.tmp"
  mv "$REQUISITOS.tmp" "$REQUISITOS"
fi

# --- 3. La app de Mac ------------------------------------------------------------------
swift build -c release --arch arm64
BIN="$(swift build -c release --arch arm64 --show-bin-path)/AlfalfaCode"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$APP/Contents/Helpers"
cp "$BIN" "$APP/Contents/MacOS/AlfalfaCode"
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
  <key>CFBundleExecutable</key>         <string>AlfalfaCode</string>
  <key>CFBundlePackageType</key>        <string>APPL</string>
  <key>CFBundleIconFile</key>           <string>AppIcon</string>
  <key>LSMinimumSystemVersion</key>     <string>14.0</string>
  <key>LSArchitecturePriority</key>     <array><string>arm64</string></array>
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

cat > "$MOTOR/alfalfa" <<'LANZADOR'
#!/bin/sh
# El motor de AlfalfaCode dentro de la app. -I: ni PYTHONPATH, ni el site del usuario, ni la
# carpeta actual (la del proyecto) en sys.path. -B: no escribe .pyc en el bundle firmado.
motor=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
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
swiftc -O -target arm64-apple-macos14 "$FIRMANTE" -o "$APP/Contents/Helpers/alfalfa-firma-$(huella "$FIRMANTE")"
swiftc -O -target arm64-apple-macos14 "$AYUDANTE" -o "$APP/Contents/Helpers/alfalfa-apps-$(huella "$AYUDANTE")"

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
    *) firmar "$f"; n=$((n+1)) ;;
  esac
done < "$BINARIOS"
firmar "$APP" AlfalfaCode.entitlements
codesign --verify --deep --strict "$APP"
echo "firmados $n binarios del motor y la app con: $([ "$IDENTIDAD" = "-" ] && echo 'ad hoc (sólo este Mac)' || echo "$IDENTIDAD")"

# --- 7. Comprobación: el motor arranca desde la app y no escribe en ella ---------------------
MARCA="$(mktemp)"
PRUEBA_HOME="$(mktemp -d)"
env -i HOME="$PRUEBA_HOME" PATH=/usr/bin:/bin "$MOTOR/alfalfa" --version
env -i HOME="$PRUEBA_HOME" PATH=/usr/bin:/bin "$PY" -I -B -c \
  'import alfalfa.terminal, alfalfa.empaquetado as e, cuysearch, mcp, jsonschema, httpx; assert e.en_la_app(), "no se ve dentro de la app"'
if [ -n "$(find "$APP" -newer "$MARCA" -print -quit)" ]; then
  echo "✗ el motor escribió dentro de la app:" >&2; find "$APP" -newer "$MARCA" >&2; exit 1
fi
rm -rf "$MARCA" "$PRUEBA_HOME"
codesign --verify --deep --strict "$APP"

echo "listo -> $MAC/$APP  (versión $VERSION, compilación $COMPILACION, $(du -sh "$APP" | cut -f1))"
