# Instalar

En un Mac con Apple Silicon y macOS 14 o posterior, sin Python, git ni Xcode:

```bash
brew install --cask 0sw2ld0/tap/alfalfacode   # la app de Mac
brew install 0sw2ld0/tap/alfalfa              # la CLI
```

o, sólo la CLI y sin Homebrew ni sudo:

```bash
curl -fsSL https://alfalfacode.cuycoders.dev/instalar.sh | sh
```

También vale bajar el DMG de la
[última release](https://github.com/0sw2ld0/AlfalfaCode/releases/latest) y
arrastrar la app a Aplicaciones.

Las tres formas usan el mismo DMG de la release, firmado y notarizado:

- **El cask** instala la app. Se actualiza sola (`auto_updates`), así que
  `brew upgrade` no la toca.
- **La fórmula** instala la CLI desde un bottle, que es el motor de ese DMG tal
  cual. Homebrew no reescribe sus binarios, así que siguen con la firma Developer ID
  del equipo FZ8TW9AWLE.
  No compila nada.
- **`instalar.sh`** ([alfalfa/instalar.sh](../../alfalfa/instalar.sh)) hace esto:
  1. Lee `version.json` de la última release.
  2. Baja el DMG y comprueba su huella SHA-256.
  3. Monta el DMG en sólo lectura y comprueba la firma de la app: que sea del
     equipo FZ8TW9AWLE, con el identificador `com.cuycoders.alfalfacode`, la
     misma versión y notarizada (`spctl`).
  4. Copia el motor a `~/.local/share/alfalfa` y deja `~/.local/bin/alfalfa`.
  5. Si hace falta, añade `~/.local/bin` al `PATH` en `~/.zshrc`, entre marcas.
     Con `ALFALFA_SIN_PATH=1` no toca ese archivo.

  `ALFALFA_PREFIJO` cambia la carpeta de destino, que por defecto es `~/.local`.

Para desinstalar:

```bash
brew uninstall alfalfa && brew uninstall --cask alfalfacode
curl -fsSL https://alfalfacode.cuycoders.dev/instalar.sh | sh -s -- --desinstalar
```

Ninguna de las dos borra `~/.alfalfa`, donde viven tus sesiones y ajustes.

## Desde el código

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Después `alfalfa` queda en `.venv/bin/`. También se puede lanzar con
`scripts/alfalfa`, que admite un enlace simbólico desde cualquier carpeta del `PATH`.
