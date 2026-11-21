# Versión y SHA-256 de los ejecutables oficiales de AlfalfaDesktop, por arquitectura.
ALFALFADESKTOP_VERSION="0.1.1"
case "${ARQ:-arm64}" in
  arm64)
    ALFALFADESKTOP_ASSET="alfalfa-desktop-macos-arm64"
    ALFALFADESKTOP_SHA256="8c9ad6a5fa07b7cf1a88b9d93caa17c7452b45adabaf7c9c10a350efb21da954" ;;
  x86_64)
    ALFALFADESKTOP_ASSET="alfalfa-desktop-macos-x64"
    ALFALFADESKTOP_SHA256="316c84f8fca481720f59908079db2d5e21fa7bae2d7047fcb5ac058bfb951049" ;;
  *) echo "✗ ARQ debe ser arm64 o x86_64, no ${ARQ}" >&2; return 1 2>/dev/null || exit 1 ;;
esac

alfalfadesktop_huella_correcta() {
  [ -f "$1" ] && [ ! -L "$1" ] && [ "$(shasum -a 256 "$1" | cut -d' ' -f1)" = "$ALFALFADESKTOP_SHA256" ]
}

alfalfadesktop_preparar() (
  local destino="$1" temporal
  if alfalfadesktop_huella_correcta "$destino"; then return 0; fi
  mkdir -p "$(dirname "$destino")"
  temporal="$(mktemp "$destino.XXXXXX")"
  trap 'rm -f "$temporal"' EXIT
  curl -q -fsSL --proto '=https' --proto-redir '=https' --tlsv1.2 --retry 2 -o "$temporal" \
    "https://github.com/0sw2ld0/AlfalfaDesktop/releases/download/v$ALFALFADESKTOP_VERSION/$ALFALFADESKTOP_ASSET"
  alfalfadesktop_huella_correcta "$temporal" \
    || { echo "✗ AlfalfaDesktop $ALFALFADESKTOP_VERSION no coincide con su huella SHA-256 fijada" >&2; return 1; }
  mv -f "$temporal" "$destino"
)

alfalfadesktop_comprobar_app() {
  local app="$1" casa="$2" plist tipo version ejecutable salida
  plist="$app/Contents/Info.plist"
  tipo="$(plutil -type AlfalfaDesktopVersion "$plist" 2>/dev/null)" || return 0
  version="$(plutil -extract AlfalfaDesktopVersion raw -o - "$plist")"
  if [ "$tipo" != string ] || ! [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "✗ AlfalfaDesktopVersion no es una versión válida" >&2; return 1
  fi
  ejecutable="$(cd "$(dirname "$app")" && pwd)/$(basename "$app")/Contents/Helpers/alfalfa-desktop"
  if [ ! -f "$ejecutable" ] || [ ! -x "$ejecutable" ] || [ -L "$ejecutable" ]; then
    echo "✗ La app declara AlfalfaDesktop $version y no trae su ejecutable" >&2; return 1
  fi
  codesign --verify --strict "$ejecutable" || return 1
  if ! salida="$(cd "$casa" && env -i HOME="$PWD" PATH=/usr/bin:/bin "$ejecutable" --version)" \
    || [ "$salida" != "AlfalfaDesktop $version" ]; then
    echo "✗ El ejecutable no declara AlfalfaDesktop $version de la app" >&2; return 1
  fi
}
