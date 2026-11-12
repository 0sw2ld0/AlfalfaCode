#!/bin/bash
# Hace el bottle de Homebrew de la fórmula `alfalfa` a partir del DMG de una release, sin
# compilar nada y sin Homebrew: el motor de la app y sus ayudantes, con su firma Developer ID.
#
#   distribucion/botella.sh AlfalfaCode-0.99.0.dmg 0.99.0 carpeta/de/salida
#
# Deja `alfalfa-<versión>.arm64_sonoma.bottle.tar.gz` y escribe su huella SHA-256 en la
# salida estándar. Va en la release junto al DMG; la fórmula lo declara con
# `cellar: :any_skip_relocation`, así que Homebrew lo vierte sin reescribir los binarios
# (no rompe su firma) y no pide las herramientas de línea de órdenes al día. Sirve en
# Sonoma y en cualquier macOS posterior (Homebrew usa el bottle más nuevo que no pase del suyo).
#
# El contenido es el mismo que deja `install` de la fórmula (que se usa sólo con
# --build-from-source): libexec/Resources/motor, libexec/Helpers, libexec/instalacion y
# bin/alfalfa. Si cambias uno, cambia el otro.
set -euo pipefail
. "$(dirname "$0")/cuybrowser.sh"

DMG="${1:?uso: botella.sh <dmg> <versión> <salida>}"
VERSION="${2:?falta la versión}"
SALIDA="${3:?falta la carpeta de salida}"
ETIQUETA="arm64_sonoma"
EQUIPO="FZ8TW9AWLE"
NOMBRE="alfalfa-$VERSION.$ETIQUETA.bottle.tar.gz"

TMP="$(mktemp -d)"
VOLUMEN="$TMP/volumen"
trap 'hdiutil detach -quiet -force "$VOLUMEN" 2>/dev/null || true; rm -rf "$TMP"' EXIT
mkdir "$VOLUMEN"
hdiutil attach -nobrowse -readonly -noautoopen -quiet -mountpoint "$VOLUMEN" "$DMG"
APP="$VOLUMEN/AlfalfaCode.app"
codesign --verify --deep --strict "$APP"
if [ -z "${BOTELLA_SIN_EQUIPO:-}" ]; then
  [ "$(codesign -dv --verbose=2 "$APP" 2>&1 | sed -n 's/^TeamIdentifier=//p')" = "$EQUIPO" ] \
    || { echo "✗ la app del DMG no es del equipo $EQUIPO" >&2; exit 1; }
fi
[ "$(plutil -extract CFBundleShortVersionString raw -o - "$APP/Contents/Info.plist")" = "$VERSION" ] \
  || { echo "✗ la app del DMG no es la versión $VERSION" >&2; exit 1; }
cuybrowser_comprobar_app "$APP" "$TMP"

KEG="$TMP/alfalfa/$VERSION"
mkdir -p "$KEG/libexec/Resources" "$KEG/bin"
ditto "$APP/Contents/Resources/motor" "$KEG/libexec/Resources/motor"
ditto "$APP/Contents/Helpers" "$KEG/libexec/Helpers"
printf 'brew\n%s\n' "$VERSION" > "$KEG/libexec/instalacion"
cat > "$KEG/bin/alfalfa" <<'LANZADOR'
#!/bin/sh
# AlfalfaCode (Homebrew): el motor está en ../libexec respecto a este archivo, sin rutas fijas.
yo=$0
while [ -L "$yo" ]; do
  d=$(cd -P -- "$(dirname -- "$yo")" && pwd)
  l=$(readlink "$yo")
  case "$l" in /*) yo=$l ;; *) yo=$d/$l ;; esac
done
keg=$(cd -P -- "$(dirname -- "$yo")/.." && pwd)
exec "$keg/libexec/Resources/motor/alfalfa" "$@"
LANZADOR
chmod 755 "$KEG/bin/alfalfa"
"$KEG/bin/alfalfa" --version >/dev/null

mkdir -p "$SALIDA"
COPYFILE_DISABLE=1 tar --no-mac-metadata -czf "$SALIDA/$NOMBRE" -C "$TMP" alfalfa
shasum -a 256 "$SALIDA/$NOMBRE" | cut -d' ' -f1
