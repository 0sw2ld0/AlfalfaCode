# Versión del ayudante de AlfalfaCode.app. ARQ elige el procesador: arm64 (Apple Silicon, por
# defecto) o x86_64 (Intel). Las huellas son las de SHA256SUMS de la release de AlfalfaBrowser.
CUYBROWSER_VERSION="0.7.0"
case "${ARQ:-arm64}" in
  arm64)
    CUYBROWSER_SHA256="452230c41a6167fbdca8f87e8dd239344373ef7cc9ec5e223e906e0c67eb5eb8"
    CUYBROWSER_ASSET="cuybrowser-macos-arm64" ;;
  x86_64)
    CUYBROWSER_SHA256="3c800a2465d6a9f644168fc6fc9e0e62b822379fb3ce6208a036c3925d110dc0"
    CUYBROWSER_ASSET="cuybrowser-macos-x64" ;;
  *) echo "✗ ARQ debe ser arm64 o x86_64, no ${ARQ}" >&2; return 1 2>/dev/null || exit 1 ;;
esac

cuybrowser_huella_correcta() {
  [ -f "$1" ] && [ ! -L "$1" ] && [ "$(shasum -a 256 "$1" | cut -d' ' -f1)" = "$CUYBROWSER_SHA256" ]
}

cuybrowser_preparar() (
  local destino="$1" temporal
  if cuybrowser_huella_correcta "$destino"; then return 0; fi
  mkdir -p "$(dirname "$destino")"
  temporal="$(mktemp "$destino.XXXXXX")"
  trap 'rm -f "$temporal"' EXIT
  curl -q -fsSL --proto '=https' --proto-redir '=https' --tlsv1.2 --retry 2 -o "$temporal" \
    "https://github.com/0sw2ld0/AlfalfaBrowser/releases/download/v$CUYBROWSER_VERSION/$CUYBROWSER_ASSET"
  cuybrowser_huella_correcta "$temporal" \
    || { echo "✗ AlfalfaBrowser $CUYBROWSER_VERSION no coincide con su huella SHA-256 fijada" >&2; return 1; }
  mv -f "$temporal" "$destino"
)

cuybrowser_firmar() {
  local ejecutable="$1" identidad="$2" entitlements="$3"
  local opciones=(--force --options runtime --identifier com.cuycoders.alfalfacode.cuybrowser)
  if [ "$identidad" != "-" ]; then opciones+=(--timestamp); fi
  codesign "${opciones[@]}" --entitlements "$entitlements" --sign "$identidad" "$ejecutable"
}

cuybrowser_comprobar_app() {
  local app="$1" casa="$2" plist tipo version ejecutable salida
  plist="$app/Contents/Info.plist"
  # Los DMG anteriores a CuyBrowser no declaran la clave.
  tipo="$(plutil -type AlfalfaCuyBrowserVersion "$plist" 2>/dev/null)" || return 0
  [ "$tipo" = string ] \
    || { echo "✗ AlfalfaCuyBrowserVersion no es un texto" >&2; return 1; }
  version="$(plutil -extract AlfalfaCuyBrowserVersion raw -o - "$plist")"
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || { echo "✗ AlfalfaCuyBrowserVersion no es una versión válida" >&2; return 1; }
  ejecutable="$(cd "$(dirname "$app")" && pwd)/$(basename "$app")/Contents/Helpers/cuybrowser"
  if [ ! -f "$ejecutable" ] || [ ! -x "$ejecutable" ] || [ -L "$ejecutable" ]; then
    echo "✗ La app declara CuyBrowser $version y no trae su ejecutable" >&2; return 1
  fi
  codesign --verify --strict "$ejecutable" || return 1
  if ! salida="$(cd "$casa" && env -i HOME="$PWD" PATH=/usr/bin:/bin "$ejecutable" --version)" \
    || [ "$salida" != "$version" ]; then
    echo "✗ El ejecutable no declara CuyBrowser $version de la app" >&2; return 1
  fi
}
