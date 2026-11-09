# Tap de Homebrew de CuyCoders

```bash
brew install --cask 0sw2ld0/tap/alfalfacode   # la app de Mac de AlfalfaCode
brew install 0sw2ld0/tap/alfalfa              # la CLI `alfalfa`
```

Sólo Apple Silicon y macOS 14 (Sonoma) o posterior.

- **alfalfacode** (cask): el DMG firmado y notarizado de la última release de
  [AlfalfaCode](https://github.com/0sw2ld0/AlfalfaCode). La app se actualiza sola
  (`auto_updates`), así que `brew upgrade` no la toca salvo con `--greedy`.
- **alfalfa** (fórmula): la CLI, con el mismo motor que lleva la app (un Python propio con
  todas sus dependencias). Se instala desde un bottle hecho con ese motor tal cual, firmado
  por Apple; no compila nada. Se actualiza con `brew upgrade alfalfa`.

Tus sesiones y ajustes viven en `~/.alfalfa`; ni `brew uninstall` ni `--zap` los borran.

Estos archivos se generan en el repositorio de AlfalfaCode (`distribucion/homebrew`) y los
actualiza `distribucion/actualizar-tap.sh` con cada release; no los edites aquí.
