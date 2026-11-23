# Cómo se trabaja en AlfalfaCode

Reglas para cualquier agente (AlfalfaCode, Codex, Claude Code…) y persona que cambie este
repositorio. Si algo de aquí choca con lo que te pidieron, pregunta antes de hacerlo.

## Nunca directo en `main`

- **No hagas commits en `main` ni subas nada a `main`.** Todo cambio entra por una rama y un PR.
- **No trabajes en el clon local del dueño** (`~/Documents/cuycoders/workspace/AlfalfaCode`). Se queda
  en una versión vieja **a propósito**: su `main` local no es el de GitHub. No lo actualices, no cambies
  de rama ahí y no dejes archivos sin guardar en él.
- Empieza siempre en un **worktree nuevo desde `origin/main`**, con una rama propia:

  ```bash
  git fetch origin
  git worktree add -b <agente>/<tema> ../wt-<tema> origin/main
  ```

  Ejemplos de rama: `alfalfa/imagenes-openai` o `fable/menu-carpeta`. Un PR por tema.

## Commits

- En español, con un título corto que diga qué cambia para quien usa AlfalfaCode.
- Autor: `Oswaldo Leon <3227113+0sw2ld0@users.noreply.github.com>`. Nunca un correo personal.
- La línea de coautor de quien hizo el trabajo. Si lo hizo una sesión de AlfalfaCode, añade
  `Co-Authored-By: AlfalfaCode <alfalfacode@cuycoders.dev>`, también cuando otro agente lo mueve o lo
  termina, y nómbralo en el PR.
- Nunca escribas claves, tokens ni códigos CUY en el código, los commits o los PR.

## Antes de abrir el PR

- Motor (Python): `ruff check .` y `python -m pytest -q -p no:cacheprovider`.
- App de Mac: `cd alfalfa-mac && swift build && swift test`. **El CI no compila la app**: si tocas
  `alfalfa-mac/`, pásalas tú.
- Una herramienta, un campo o un valor nuevo del protocolo tiene que entrar en sus tablas:
  - `alfalfa/hooks/nombres.py` (`EQUIVALENCIAS` o `SIN_EQUIVALENTE`);
  - `alfalfa/pacai/nombres.py`;
  - después, `python scripts/nombres_pacai_swift.py` para regenerar `PacaiNames.swift`;
  - y `docs/USO.md`.

  Las pruebas `test_alfalfa_hooks`, `test_pacai_valores` y `test_pacai_nombres_swift` fallan si falta
  alguno.

## El PR

- Contra `main`, con qué cambia, por qué y cómo se probó, incluido lo que **no** se pudo probar.
- Espera a que el CI (`suite`) pase en verde. Si falla, arréglalo en la misma rama.
- **No lo fusiones tú** salvo que el dueño te lo pida.

## Fusionar (sólo con permiso del dueño)

`main` exige historial lineal: nada de commits de fusión.

- **No uses `gh pr merge`** ni el botón de GitHub: firman con el correo personal del dueño.
- Si la rama sale del `main` actual, se sube tal cual: `git push origin <SHA completo>:main`.
- Si `main` avanzó, rebasa la rama (o haz cherry-pick) sobre `origin/main`, vuelve a pasar las pruebas,
  sube la rama y luego `git push origin <SHA>:main`.
- GitHub marca el PR como fusionado solo. Si avisa de «Bypassed rule violations», algo salió mal: avisa.

## Publicar una versión (sólo con permiso del dueño)

Los instaladores generados para probar un commit son artefactos locales. La app instalada busca
la versión de la última release pública y sólo ofrece una actualización si su número es mayor.
Si una release ya está publicada y hay cambios nuevos para distribuir, sube el número de versión,
recompila ambos DMG y publica una release nueva. Conserva la etiqueta y los archivos de la release
anterior. Comprueba que la release nueva sea la última y que sus dos manifiestos y el tap de Homebrew
apunten a ella. Un IDE que usa la CLI recibe la versión nueva al actualizar la instalación de
`alfalfa` que invoca.

Publicar firma con el Llavero del dueño y lo ve todo el mundo: **nunca** lo hagas por tu cuenta.

1. Commit «AlfalfaCode X.Y.Z» en `main` con `__version__` (`alfalfa/__init__.py`) y
   `alfalfa-mac/NOTAS-VERSION.txt` (una nota por línea, para quien usa la app).
2. Con el Mac desbloqueado, `ARQ=arm64` y luego `ARQ=x86_64` con `PERFIL=CuyCleaner ./publicar.sh`
   en `alfalfa-mac/`.
3. Un solo `gh release create vX.Y.Z … --latest --target <SHA completo>` con los 8 archivos: los dos
   DMG, sus copias fijas, `version.json`, `version-intel.json` y los dos bottles. El tap de Homebrew se
   actualiza solo.
4. Si cambió `alfalfa/instalar.sh`, hay que copiarlo también a la web (repo `alfalfacode-web`).
