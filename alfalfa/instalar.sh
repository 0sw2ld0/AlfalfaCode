#!/bin/sh
# Instala la CLI de AlfalfaCode (`alfalfa`) en este Mac, sin sudo:
#
#   curl -fsSL https://cuycoders.dev/alfalfa/instalar.sh | sh
#
# Qué hace:
#   1. Lee version.json de la última release de github.com/0sw2ld0/AlfalfaCode.
#   2. Baja el DMG de esa release y comprueba su huella SHA-256 con la de version.json.
#   3. Lo monta (sólo lectura) y comprueba la app de dentro: firma de Apple intacta
#      (codesign --verify --deep --strict), del equipo FZ8TW9AWLE, notarizada (spctl),
#      con el identificador com.cuycoders.alfalfacode y la misma versión.
#   4. Copia de la app el motor (un Python propio con alfalfa y sus dependencias) y sus
#      ayudantes a ~/.local/share/alfalfa, y deja ~/.local/bin/alfalfa. Si algo falla,
#      la copia que hubiera no se toca.
#   5. Si ~/.local/bin no está en el PATH, lo añade a ~/.zshrc (o ~/.bash_profile) entre
#      dos marcas (ALFALFA_SIN_PATH=1 para no tocarlos).
#
# No necesita Python, git, Xcode ni las herramientas de línea de órdenes: sólo lo que
# trae macOS (curl, shasum, hdiutil, codesign, spctl, plutil, ditto).
#
# Desinstalar (tus sesiones y ajustes de ~/.alfalfa se quedan):
#   curl -fsSL https://cuycoders.dev/alfalfa/instalar.sh | sh -s -- --desinstalar
# o a mano:
#   rm -rf ~/.local/share/alfalfa ~/.local/bin/alfalfa   (y las líneas marcadas de ~/.zshrc)
#
# Variables:
#   ALFALFA_PREFIJO   otra carpeta en vez de ~/.local (bin/ y share/alfalfa/ dentro)
#   ALFALFA_SIN_PATH  1: no tocar los archivos de arranque de la shell
#   ALFALFA_FEED      otro version.json; sólo file://, para probar con un DMG local
#                     (con él no se exige la notarización: un DMG de prueba no la tiene)
#
# Todo va dentro de main(), que se llama en la última línea: si la descarga de este guion
# se corta a medias, la shell no ejecuta nada.

main() {
  set -eu
  REPO="0sw2ld0/AlfalfaCode"
  EQUIPO="FZ8TW9AWLE"
  IDENTIFICADOR="com.cuycoders.alfalfacode"
  PREFIJO="${ALFALFA_PREFIJO:-$HOME/.local}"
  RAIZ="$PREFIJO/share/alfalfa"
  BIN="$PREFIJO/bin"
  LANZADOR="$BIN/alfalfa"
  MARCA_LANZADOR="# AlfalfaCode: lo dejó instalar.sh"
  MARCA_PATH="# AlfalfaCode (instalar.sh)"
  FEED="${ALFALFA_FEED:-https://github.com/$REPO/releases/latest/download/version.json}"

  case "${1:-}" in
    --desinstalar) desinstalar; return 0 ;;
    --ayuda|-h|--help) sed -n '2,30p' "$0" 2>/dev/null || echo "Ver https://cuycoders.dev/alfalfa"; return 0 ;;
    "") ;;
    *) falla "opción desconocida: $1 (vale --desinstalar)" ;;
  esac

  comprobar_mac
  if [ -e "$LANZADOR" ] && ! grep -q "$MARCA_LANZADOR" "$LANZADOR" 2>/dev/null; then
    falla "ya hay otro $LANZADOR que no instaló este guion; quítalo o usa ALFALFA_PREFIJO=otra/carpeta."
  fi

  TMP="$(mktemp -d "${TMPDIR:-/tmp}/alfalfa-instalar.XXXXXX")"
  VOLUMEN=""
  trap limpiar EXIT INT TERM

  # --- 1. version.json ------------------------------------------------------------------
  case "$FEED" in
    https://*) PRUEBA="" ;;
    file://*) PRUEBA=1; aviso "modo de prueba: feed local $FEED" ;;
    *) falla "ALFALFA_FEED tiene que ser https:// o file://" ;;
  esac
  bajar "$FEED" "$TMP/version.json"
  VERSION="$(campo version)"
  URL="$(campo url)"
  HUELLA="$(campo sha256 | tr 'A-F' 'a-f')"
  case "$VERSION" in
    [0-9]*.[0-9]*.[0-9]*) ;;
    *) falla "version.json trae una versión rara: $VERSION" ;;
  esac
  printf '%s' "$HUELLA" | grep -Eq '^[0-9a-f]{64}$' || falla "version.json no trae una huella SHA-256 válida"
  if [ -n "$PRUEBA" ]; then
    case "$URL" in file:///*) ;; *) falla "con un feed file:// sólo vale un DMG file://" ;; esac
  else
    case "$URL" in
      "https://github.com/$REPO/releases/download/"*.dmg) ;;
      *) falla "version.json apunta fuera de las releases de $REPO: $URL" ;;
    esac
  fi

  if [ -f "$RAIZ/instalacion" ] && [ "$(sed -n 2p "$RAIZ/instalacion")" = "$VERSION" ]; then
    paso "AlfalfaCode $VERSION ya está instalado en $RAIZ; se vuelve a instalar igual."
  fi

  # --- 2. DMG y su huella ---------------------------------------------------------------------
  paso "Bajando AlfalfaCode $VERSION…"
  bajar "$URL" "$TMP/AlfalfaCode.dmg"
  CALCULADA="$(shasum -a 256 "$TMP/AlfalfaCode.dmg" | cut -d' ' -f1)"
  [ "$CALCULADA" = "$HUELLA" ] || falla "la huella del DMG no coincide con la publicada (esperada $HUELLA, bajada $CALCULADA)"
  paso "Huella SHA-256 correcta."

  # --- 3. Montar y comprobar la app -----------------------------------------------------------
  VOLUMEN="$TMP/volumen"
  mkdir "$VOLUMEN"
  hdiutil attach -nobrowse -readonly -noautoopen -quiet -mountpoint "$VOLUMEN" "$TMP/AlfalfaCode.dmg" \
    || { VOLUMEN=""; falla "no se pudo montar el DMG"; }
  APP="$VOLUMEN/AlfalfaCode.app"
  [ -d "$APP" ] || falla "el DMG no trae AlfalfaCode.app"
  codesign --verify --deep --strict "$APP" 2>/dev/null || falla "la firma de la app no es válida"
  EQUIPO_APP="$(codesign -dv --verbose=2 "$APP" 2>&1 | sed -n 's/^TeamIdentifier=//p')"
  [ "$EQUIPO_APP" = "$EQUIPO" ] || falla "la app no está firmada por el equipo $EQUIPO (es «$EQUIPO_APP»)"
  [ "$(plutil -extract CFBundleIdentifier raw -o - "$APP/Contents/Info.plist")" = "$IDENTIFICADOR" ] \
    || falla "la app no es $IDENTIFICADOR"
  [ "$(plutil -extract CFBundleShortVersionString raw -o - "$APP/Contents/Info.plist")" = "$VERSION" ] \
    || falla "la app del DMG no es la versión $VERSION"
  if [ -z "$PRUEBA" ]; then
    spctl --assess --type execute "$APP" 2>/dev/null || falla "Apple no la tiene notarizada (spctl la rechaza)"
  fi
  MOTOR="$APP/Contents/Resources/motor"
  [ -x "$MOTOR/alfalfa" ] && [ -x "$MOTOR/python/bin/python3" ] || falla "la app no trae el motor"
  paso "Firma de Apple correcta (equipo $EQUIPO)."

  # --- 4. Copiar el motor ---------------------------------------------------------------------
  mkdir -p "$PREFIJO/share" "$BIN"
  NUEVA="$PREFIJO/share/.alfalfa-nueva.$$"
  rm -rf "$NUEVA"
  mkdir -p "$NUEVA/Resources"
  ditto "$MOTOR" "$NUEVA/Resources/motor"
  ditto "$APP/Contents/Helpers" "$NUEVA/Helpers"
  printf 'curl\n%s\n' "$VERSION" > "$NUEVA/instalacion"
  "$NUEVA/Resources/motor/alfalfa" --version >/dev/null 2>&1 || { rm -rf "$NUEVA"; falla "el motor copiado no arranca"; }
  hdiutil detach -quiet "$VOLUMEN" || true
  VOLUMEN=""

  VIEJA=""
  if [ -e "$RAIZ" ]; then
    VIEJA="$PREFIJO/share/.alfalfa-vieja.$$"
    mv "$RAIZ" "$VIEJA"
  fi
  if ! mv "$NUEVA" "$RAIZ"; then
    [ -n "$VIEJA" ] && mv "$VIEJA" "$RAIZ"
    falla "no se pudo colocar la versión nueva en $RAIZ"
  fi
  [ -n "$VIEJA" ] && rm -rf "$VIEJA"

  {
    echo "#!/bin/sh"
    echo "$MARCA_LANZADOR (https://cuycoders.dev/alfalfa)"
    echo "exec $(citar "$RAIZ/Resources/motor/alfalfa") \"\$@\""
  } > "$LANZADOR.nuevo.$$"
  chmod 755 "$LANZADOR.nuevo.$$"
  mv "$LANZADOR.nuevo.$$" "$LANZADOR"

  # --- 5. PATH -----------------------------------------------------------------------------------
  poner_en_path

  echo
  paso "AlfalfaCode $VERSION instalado: $LANZADOR"
  echo "  Empezar:       alfalfa \"tu encargo\"   (la primera vez te ofrece modelos gratis)"
  echo "  Actualizar:    alfalfa --actualizar"
  echo "  Desinstalar:   curl -fsSL https://cuycoders.dev/alfalfa/instalar.sh | sh -s -- --desinstalar"
  echo "                 (tus sesiones y ajustes de ~/.alfalfa se quedan)"
  echo "  La app de Mac: https://cuycoders.dev/alfalfa  ·  brew install --cask 0sw2ld0/tap/alfalfacode"
}

comprobar_mac() {
  [ "$(uname -s)" = "Darwin" ] || falla "AlfalfaCode sólo funciona en macOS."
  # Bajo Rosetta, uname -m dice x86_64 aunque el Mac sea Apple Silicon.
  [ "$(uname -m)" = "arm64" ] || [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1" ] \
    || falla "AlfalfaCode necesita un Mac con Apple Silicon (M1 o posterior)."
  MAYOR="$(sw_vers -productVersion | cut -d. -f1)"
  [ "$MAYOR" -ge 14 ] 2>/dev/null || falla "AlfalfaCode necesita macOS 14 (Sonoma) o posterior."
}

bajar() {   # bajar <url> <destino>
  case "$1" in
    file://*) cp "${1#file://}" "$2" || falla "no se pudo leer $1" ;;
    *) curl -fsSL --proto '=https' --tlsv1.2 --retry 2 -o "$2" "$1" || falla "no se pudo bajar $1" ;;
  esac
}

campo() {   # un campo de texto de version.json (plutil lee JSON)
  plutil -extract "$1" raw -o - "$TMP/version.json" 2>/dev/null || falla "version.json no trae «$1»"
}

citar() {   # una ruta entre comillas simples para la shell
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

archivo_de_arranque() {
  case "$(basename "${SHELL:-/bin/zsh}")" in
    zsh) echo "${ZDOTDIR:-$HOME}/.zshrc" ;;
    bash) echo "$HOME/.bash_profile" ;;
    *) echo "" ;;
  esac
}

poner_en_path() {
  case ":${PATH:-}:" in *":$BIN:"*) return 0 ;; esac
  ARRANQUE="$(archivo_de_arranque)"
  if [ -n "${ALFALFA_SIN_PATH:-}" ] || [ -z "$ARRANQUE" ]; then
    aviso "$BIN no está en tu PATH. Añádelo a tu shell:  export PATH=\"$BIN:\$PATH\""
    return 0
  fi
  if ! grep -q "$MARCA_PATH" "$ARRANQUE" 2>/dev/null; then
    {
      echo ""
      echo "$MARCA_PATH"
      case "$BIN" in
        "$HOME"/*) echo "export PATH=\"\$HOME/${BIN#"$HOME"/}:\$PATH\"" ;;
        *) echo "export PATH=$(citar "$BIN"):\"\$PATH\"" ;;
      esac
    } >> "$ARRANQUE"
    paso "Añadido $BIN al PATH en $ARRANQUE (abre otra terminal o: source $ARRANQUE)"
  fi
}

desinstalar() {
  QUITADO=""
  if [ -f "$RAIZ/instalacion" ] && [ "$(sed -n 1p "$RAIZ/instalacion")" = "curl" ]; then
    rm -rf "$RAIZ"
    QUITADO=1
  elif [ -e "$RAIZ" ]; then
    falla "$RAIZ no parece una instalación de este guion; no se toca."
  fi
  if [ -f "$LANZADOR" ] && grep -q "$MARCA_LANZADOR" "$LANZADOR"; then
    rm -f "$LANZADOR"
    QUITADO=1
  fi
  for ARRANQUE in "${ZDOTDIR:-$HOME}/.zshrc" "$HOME/.bash_profile"; do
    if [ -f "$ARRANQUE" ] && grep -q "$MARCA_PATH" "$ARRANQUE"; then
      # Quita la marca y la línea siguiente (el export), y la línea en blanco de antes si la hay.
      awk -v marca="$MARCA_PATH" '
        saltar { saltar = 0; next }
        $0 == marca { if (hay && previa == "") hay = 0; saltar = 1; next }
        { if (hay) print previa; previa = $0; hay = 1 }
        END { if (hay) print previa }' "$ARRANQUE" > "$ARRANQUE.alfalfa.$$" \
        && cat "$ARRANQUE.alfalfa.$$" > "$ARRANQUE" && rm -f "$ARRANQUE.alfalfa.$$"
      paso "Quitado $BIN del PATH en $ARRANQUE"
    fi
  done
  if [ -n "$QUITADO" ]; then
    paso "AlfalfaCode desinstalado. Tus sesiones y ajustes siguen en ~/.alfalfa (bórralo a mano si quieres)."
  else
    paso "No había ninguna instalación de este guion en $PREFIJO."
  fi
}

limpiar() {
  if [ -n "${VOLUMEN:-}" ]; then hdiutil detach -quiet -force "$VOLUMEN" 2>/dev/null || true; fi
  [ -n "${TMP:-}" ] && rm -rf "$TMP"
  [ -n "${NUEVA:-}" ] && rm -rf "$NUEVA"
  return 0
}

paso() { printf '✓ %s\n' "$1"; }
aviso() { printf '! %s\n' "$1" >&2; }
falla() { printf '✗ AlfalfaCode: %s\n' "$1" >&2; exit 1; }

main "$@"
