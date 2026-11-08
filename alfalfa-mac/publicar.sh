#!/bin/bash
# Genera el DMG de AlfalfaCode para distribuir fuera del App Store:
#   compila (build-app.sh) → firma Developer ID → notariza y grapa la app → DMG con acceso a
#   Aplicaciones → firma el DMG → notariza y grapa el DMG → lo verifica como el Mac de un usuario.
#
# Requisitos (una sola vez):
#   1. Certificado «Developer ID Application» en el llavero (Xcode → Ajustes → Cuentas).
#   2. Credencial de notarización guardada en el llavero con el nombre «AlfalfaCode»:
#        xcrun notarytool store-credentials "AlfalfaCode" --apple-id "TU_CORREO" --team-id "TU_TEAM_ID"
#      (pide una contraseña específica de app de appleid.apple.com; queda sólo en tu llavero).
#
# Uso:  ./publicar.sh             → dist/AlfalfaCode-<versión>.dmg firmado y notarizado
#       PERFIL=otro ./publicar.sh → si guardaste la credencial con otro nombre
#       ./publicar.sh --local     → DMG con firma ad hoc y sin notarizar, sólo para probar en este Mac
#                                   (en otro Mac Gatekeeper no lo deja abrir)
set -euo pipefail
cd "$(dirname "$0")"

LOCAL=""
[ "${1:-}" = "--local" ] && LOCAL=1
PERFIL="${PERFIL:-AlfalfaCode}"
VERSION="${VERSION:-$(sed -n "s/^__version__ = '\(.*\)'$/\1/p" ../alfalfa/__init__.py)}"
APP="dist/AlfalfaCode.app"
DMG="dist/AlfalfaCode-$VERSION.dmg"
[ -n "$LOCAL" ] && DMG="dist/AlfalfaCode-$VERSION-local.dmg"

# --- Comprobaciones previas: fallar pronto y explicando qué falta ---------------------------
if [ -n "$LOCAL" ]; then
  IDENTIDAD="-"
else
  IDENTIDAD="$(security find-identity -v -p codesigning 2>/dev/null \
    | sed -n 's/.*"\(Developer ID Application: .*\)"/\1/p' | head -1)"
  if [ -z "$IDENTIDAD" ]; then
    echo "✗ Falta el certificado «Developer ID Application»."
    echo "  Créalo en Xcode → Ajustes → Cuentas → Gestionar certificados → + → Developer ID Application."
    echo "  Para probar sólo en este Mac: ./publicar.sh --local"
    exit 1
  fi
  if ! xcrun notarytool history --keychain-profile "$PERFIL" >/dev/null 2>&1; then
    echo "✗ Falta la credencial de notarización «$PERFIL» en el llavero. Créala con:"
    echo "  xcrun notarytool store-credentials \"$PERFIL\" --apple-id \"TU_CORREO\" --team-id \"TU_TEAM_ID\""
    exit 1
  fi
  # Lo que se publica es lo que hay en git: nada de cambios a medias dentro del DMG.
  if [ -n "$(git -C .. status --porcelain --untracked-files=no)" ]; then
    echo "✗ Hay cambios sin guardar en el repositorio; haz commit antes de publicar."
    exit 1
  fi
fi

# --- 1. Compilar y firmar la app (motor incluido) ----------------------------------------------
IDENTIDAD="$IDENTIDAD" VERSION="$VERSION" ./build-app.sh

# --- 1b. Notarizar y grapar la propia app ------------------------------------------------------
# Gatekeeper verifica la app que va dentro del DMG. Con el ticket grapado en ella se valida
# también sin conexión, y sigue valiendo después de copiarla a Aplicaciones.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
if [ -z "$LOCAL" ]; then
  /usr/bin/ditto -c -k --keepParent "$APP" "$TMP/AlfalfaCode.zip"
  echo "Notarizando la app…"
  xcrun notarytool submit "$TMP/AlfalfaCode.zip" --keychain-profile "$PERFIL" --wait
  xcrun stapler staple "$APP"
  xcrun stapler validate "$APP"
  rm -f "$TMP/AlfalfaCode.zip"
fi

# --- 2. DMG con acceso directo a Aplicaciones -----------------------------------------------------
mkdir -p "$TMP/dmg"
ditto "$APP" "$TMP/dmg/AlfalfaCode.app"
ln -s /Applications "$TMP/dmg/Aplicaciones"
rm -f "$DMG"
hdiutil create -volname "AlfalfaCode $VERSION" -srcfolder "$TMP/dmg" -ov -format UDZO "$DMG" >/dev/null
if [ -n "$LOCAL" ]; then
  echo "✓ DMG de prueba (firma ad hoc, sin notarizar; sólo para este Mac): $DMG ($(du -h "$DMG" | cut -f1))"
  shasum -a 256 "$DMG"
  exit 0
fi
codesign --force --timestamp --sign "$IDENTIDAD" "$DMG"
echo "DMG: $DMG ($(du -h "$DMG" | cut -f1))"

# --- 3. Notarización del DMG: Apple lo escanea y emite un sello -------------------------------
echo "Enviando el DMG a notarizar (suele tardar entre 1 y 10 minutos)…"
if ! xcrun notarytool submit "$DMG" --keychain-profile "$PERFIL" --wait --output-format json > "$TMP/notarizacion.json"; then
  cat "$TMP/notarizacion.json"
  exit 1
fi
ESTADO="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status",""))' "$TMP/notarizacion.json")"
if [ "$ESTADO" != "Accepted" ]; then
  ID="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id",""))' "$TMP/notarizacion.json")"
  echo "✗ Notarización: $ESTADO. Detalle de Apple:"
  xcrun notarytool log "$ID" --keychain-profile "$PERFIL"
  exit 1
fi

# --- 4. Grapar el sello: así Gatekeeper lo valida también sin internet --------------------------
xcrun stapler staple "$DMG"

# --- 5. Verificar como lo haría el Mac de un usuario ------------------------------------------------
spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG"
spctl --assess --type execute --verbose=2 "$APP"
echo "✓ Listo para publicar: $DMG"
shasum -a 256 "$DMG"
