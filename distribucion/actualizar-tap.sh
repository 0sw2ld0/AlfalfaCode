#!/bin/bash
# Pone el cask `alfalfacode` y la fórmula `alfalfa` en la versión de una release publicada:
# versión, URL y huellas SHA-256 del DMG y del bottle.
#
#   distribucion/actualizar-tap.sh 0.99.0                 → cambia distribucion/homebrew/
#   distribucion/actualizar-tap.sh 0.99.0 ../homebrew-tap → cambia un clon del tap
#
# Qué hace:
#   1. Baja version.json y el DMG de la release v<versión> de 0sw2ld0/AlfalfaCode y comprueba
#      que la huella del DMG es la de version.json.
#   2. Si la release ya trae el bottle (alfalfa-<versión>.arm64_sonoma.bottle.tar.gz, lo deja
#      publicar.sh), toma su huella; si no, lo hace con botella.sh y lo deja en la carpeta de
#      trabajo para subirlo (con --subir-botella lo sube él: `gh release upload`).
#   3. Escribe la versión y las huellas en Casks/alfalfacode.rb y Formula/alfalfa.rb de la
#      carpeta del tap (copiando antes las plantillas de distribucion/homebrew si es otra).
#
# No hace commit ni push: eso lo hace el workflow .github/workflows/tap.yml, o tú a mano.
#
# Para probar sin GitHub: DMG=ruta/al.dmg VERSION_JSON=ruta/version.json (y BOTELLA=ruta si
# ya está hecho); entonces no se baja nada.
set -euo pipefail
cd "$(dirname "$0")"
DIST="$(pwd)"

SUBIR=""
ARGS=()
for a in "$@"; do
  case "$a" in
    --subir-botella) SUBIR=1 ;;
    -*) echo "✗ opción desconocida: $a" >&2; exit 2 ;;
    *) ARGS+=("$a") ;;
  esac
done
VERSION="${ARGS[0]:?uso: actualizar-tap.sh <versión> [carpeta del tap] [--subir-botella]}"
VERSION="${VERSION#v}"
TAP="${ARGS[1]:-$DIST/homebrew}"
REPO="0sw2ld0/AlfalfaCode"
ETIQUETA="v$VERSION"
BASE="https://github.com/$REPO/releases/download/$ETIQUETA"
NOMBRE_DMG="AlfalfaCode-$VERSION.dmg"
NOMBRE_BOTELLA="alfalfa-$VERSION.arm64_sonoma.bottle.tar.gz"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "✗ versión rara: $VERSION" >&2; exit 2; }

TRABAJO="${TRABAJO:-$(mktemp -d)}"
mkdir -p "$TRABAJO"
bajar() { curl -fsSL --proto '=https' --retry 2 -o "$2" "$1"; }

# --- 1. version.json y DMG ------------------------------------------------------------------
if [ -n "${VERSION_JSON:-}" ]; then cp "$VERSION_JSON" "$TRABAJO/version.json"; else bajar "$BASE/version.json" "$TRABAJO/version.json"; fi
campo() { plutil -extract "$1" raw -o - "$TRABAJO/version.json"; }
[ "$(campo version)" = "$VERSION" ] || { echo "✗ version.json es de $(campo version), no de $VERSION" >&2; exit 1; }
if [ -z "${DMG:-}" ]; then
  [ "$(campo url)" = "$BASE/$NOMBRE_DMG" ] || { echo "✗ version.json apunta a $(campo url)" >&2; exit 1; }
  DMG="$TRABAJO/$NOMBRE_DMG"
  bajar "$BASE/$NOMBRE_DMG" "$DMG"
fi
HUELLA_DMG="$(shasum -a 256 "$DMG" | cut -d' ' -f1)"
[ "$HUELLA_DMG" = "$(campo sha256)" ] || { echo "✗ la huella del DMG no es la de version.json" >&2; exit 1; }
echo "DMG $NOMBRE_DMG: $HUELLA_DMG"

# --- 2. Bottle ----------------------------------------------------------------------------------
if [ -z "${BOTELLA:-}" ] && [ -z "${VERSION_JSON:-}" ] \
   && curl -fsSL --proto '=https' -o "$TRABAJO/$NOMBRE_BOTELLA" "$BASE/$NOMBRE_BOTELLA" 2>/dev/null; then
  BOTELLA="$TRABAJO/$NOMBRE_BOTELLA"
  echo "La release ya trae el bottle."
elif [ -z "${BOTELLA:-}" ]; then
  "$DIST/botella.sh" "$DMG" "$VERSION" "$TRABAJO" >/dev/null
  BOTELLA="$TRABAJO/$NOMBRE_BOTELLA"
  if [ -n "$SUBIR" ]; then
    gh release upload "$ETIQUETA" "$BOTELLA" --repo "$REPO"
    echo "Bottle subido a la release $ETIQUETA."
  else
    echo "Bottle hecho (súbelo a la release $ETIQUETA): $BOTELLA"
  fi
fi
HUELLA_BOTELLA="$(shasum -a 256 "$BOTELLA" | cut -d' ' -f1)"
echo "Bottle $NOMBRE_BOTELLA: $HUELLA_BOTELLA"

# --- 3. Cask y fórmula ------------------------------------------------------------------------
mkdir -p "$TAP/Casks" "$TAP/Formula"
if [ "$(cd "$TAP" && pwd)" != "$DIST/homebrew" ]; then
  cp "$DIST/homebrew/Casks/alfalfacode.rb" "$TAP/Casks/alfalfacode.rb"
  cp "$DIST/homebrew/Formula/alfalfa.rb" "$TAP/Formula/alfalfa.rb"
  [ -f "$TAP/README.md" ] || cp "$DIST/homebrew/README.md" "$TAP/README.md"
fi
CASK="$TAP/Casks/alfalfacode.rb"
FORMULA="$TAP/Formula/alfalfa.rb"
sed -i '' -E \
  -e "s|^  version \"[^\"]*\"|  version \"$VERSION\"|" \
  -e "s|^  sha256 \"[0-9a-f]{64}\"|  sha256 \"$HUELLA_DMG\"|" "$CASK"
sed -i '' -E \
  -e "s|^  url \"https://github.com/$REPO/releases/download/v[^/]*/AlfalfaCode-[^\"]*\.dmg\"|  url \"$BASE/$NOMBRE_DMG\"|" \
  -e "s|^  sha256 \"[0-9a-f]{64}\"|  sha256 \"$HUELLA_DMG\"|" \
  -e "s|^    root_url \"[^\"]*\"|    root_url \"$BASE\"|" \
  -e "s|arm64_sonoma: \"[0-9a-f]{64}\"|arm64_sonoma: \"$HUELLA_BOTELLA\"|" "$FORMULA"
for f in "$CASK" "$FORMULA"; do
  grep -q "$HUELLA_DMG" "$f" || { echo "✗ no se pudo poner la huella en $f" >&2; exit 1; }
done
grep -q "$HUELLA_BOTELLA" "$FORMULA" || { echo "✗ no se pudo poner la huella del bottle" >&2; exit 1; }
grep -q "v$VERSION/AlfalfaCode-$VERSION.dmg" "$FORMULA" || { echo "✗ no se pudo poner la URL" >&2; exit 1; }
echo "✓ Tap en $VERSION: $CASK y $FORMULA"
