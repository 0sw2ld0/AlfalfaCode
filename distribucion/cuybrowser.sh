# Versión del ayudante de AlfalfaCode.app. ARQ elige el procesador: arm64 (Apple Silicon, por
# defecto) o x86_64 (Intel). Las huellas son las de SHA256SUMS de la release de AlfalfaBrowser.
CUYBROWSER_VERSION="0.8.0"
case "${ARQ:-arm64}" in
  arm64)
    CUYBROWSER_SHA256="501d6e0d9483ab78e22338c827f5b2a33d421f1e68eda72ce43b1fd54610faa7"
    CUYBROWSER_ASSET="alfalfabrowser-macos-arm64" ;;
  x86_64)
    CUYBROWSER_SHA256="46381ae67ecde9233a71f16df6ece173cf05ca01567881c1e525a7fab8cb79bc"
    CUYBROWSER_ASSET="alfalfabrowser-macos-x64" ;;
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
  local clave=AlfalfaBrowserVersion binario=alfalfabrowser nombre=AlfalfaBrowser
  plist="$app/Contents/Info.plist"
  if ! tipo="$(plutil -type "$clave" "$plist" 2>/dev/null)"; then
    clave=AlfalfaCuyBrowserVersion binario=cuybrowser nombre=CuyBrowser
    tipo="$(plutil -type "$clave" "$plist" 2>/dev/null)" || return 0
  fi
  [ "$tipo" = string ] \
    || { echo "✗ $clave no es un texto" >&2; return 1; }
  version="$(plutil -extract "$clave" raw -o - "$plist")"
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || { echo "✗ $clave no es una versión válida" >&2; return 1; }
  ejecutable="$(cd "$(dirname "$app")" && pwd)/$(basename "$app")/Contents/Helpers/$binario"
  if [ ! -f "$ejecutable" ] || [ ! -x "$ejecutable" ] || [ -L "$ejecutable" ]; then
    echo "✗ La app declara $nombre $version y no trae su ejecutable" >&2; return 1
  fi
  codesign --verify --strict "$ejecutable" || return 1
  if ! salida="$(cd "$casa" && env -i HOME="$PWD" PATH=/usr/bin:/bin "$ejecutable" --version)" \
    || [ "$salida" != "$version" ]; then
    echo "✗ El ejecutable no declara $nombre $version de la app" >&2; return 1
  fi
}
