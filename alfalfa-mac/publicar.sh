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
#                                   (hoy: PERFIL=CuyCleaner, la del mismo equipo; «AlfalfaCode» aún no existe)
#       ./publicar.sh --local     → DMG con firma ad hoc y sin notarizar, sólo para probar en este Mac
#                                   (en otro Mac Gatekeeper no lo deja abrir)
#       CONFIGURACION=debug ./publicar.sh --local → DMG con una app de depuración
#
# Escribe también dist/version.json (versión, URL del DMG en la release, huella, tamaño y las notas
# de NOTAS-VERSION.txt) y el bottle de Homebrew de la CLI (alfalfa-<versión>.arm64_sonoma.bottle.tar.gz),
# y enseña la orden para publicar los tres en GitHub. No publica nada.
set -euo pipefail
cd "$(dirname "$0")"
. ../distribucion/cuybrowser.sh

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
  CONFIGURACION="${CONFIGURACION:-release}"
else
  CONFIGURACION=release
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
IDENTIDAD="$IDENTIDAD" VERSION="$VERSION" CONFIGURACION="$CONFIGURACION" ./build-app.sh

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
test "$(plutil -extract AlfalfaCuyBrowserVersion raw -o - "$APP/Contents/Info.plist")" = "$CUYBROWSER_VERSION" \
  || { echo "✗ la app no declara el CuyBrowser fijado ($CUYBROWSER_VERSION)" >&2; exit 1; }
cuybrowser_comprobar_app "$APP" "$TMP"
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

# --- 6. version.json: lo lee el actualizador de la app (AppUpdater) en la última release ----------
ETIQUETA="v$VERSION"
/usr/bin/python3 - "$DMG" "$VERSION" "$ETIQUETA" dist/version.json <<'PY'
import datetime, hashlib, json, os, sys
dmg, version, etiqueta, destino = sys.argv[1:5]
notas = []
if os.path.exists("NOTAS-VERSION.txt"):
    notas = [l.strip().lstrip("-• ").strip() for l in open("NOTAS-VERSION.txt", encoding="utf-8") if l.strip()]
json.dump({
    "version": version,
    "url": f"https://github.com/0sw2ld0/AlfalfaCode/releases/download/{etiqueta}/{os.path.basename(dmg)}",
    "sha256": hashlib.sha256(open(dmg, "rb").read()).hexdigest(),
    "tamano": os.path.getsize(dmg),
    "fecha": datetime.date.today().isoformat(),
    "notas": notas,
}, open(destino, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("version.json →", destino)
PY
# --- 7. Bottle de Homebrew de la CLI: el motor del DMG tal cual (ver distribucion/botella.sh) ----
BOTELLA="dist/alfalfa-$VERSION.arm64_sonoma.bottle.tar.gz"
echo "bottle de la fórmula alfalfa → $BOTELLA ($(../distribucion/botella.sh "$DMG" "$VERSION" dist))"

# --- 8. Copia con nombre fijo: el botón «Descargar para Mac» de la web apunta a
#        releases/latest/download/AlfalfaCode.dmg, que sólo existe si cada release la trae.
FIJO="dist/AlfalfaCode.dmg"
cp "$DMG" "$FIJO"
echo
echo "Para publicarlo (la release tiene que quedar como «Latest»; el actualizador lee releases/latest):"
echo "  gh release create $ETIQUETA $DMG $FIJO dist/version.json $BOTELLA --latest --repo 0sw2ld0/AlfalfaCode --title \"AlfalfaCode $VERSION\" --notes-file NOTAS-VERSION.txt"
echo "Al publicarla, .github/workflows/tap.yml pone el tap 0sw2ld0/homebrew-tap (cask y fórmula) en $VERSION."
echo "instalar.sh (curl) no necesita nada más: lee version.json y el DMG de la última release."
