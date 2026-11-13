# Actualizar

Cada versión es una etiqueta `vX.Y.Z` de este repositorio.

```bash
alfalfa --version
alfalfa --buscar-actualizacion
alfalfa --actualizar
```

En un clon, `--actualizar` avanza la rama hasta la etiqueta nueva (sólo si no hay
cambios sin guardar ni commits propios) y reinstala el paquete; instalado con pip,
lo reinstala desde GitHub en esa etiqueta. Así llegan también las dependencias
nuevas. Dentro de AlfalfaCode.app no instala nada (el bundle está firmado): dice
dónde descargar el DMG nuevo. Con la CLI de `instalar.sh` vuelve a pasar el
`instalar.sh` que viaja con el paquete, que baja la última release y comprueba su
huella y su firma. Con la de Homebrew dice `brew upgrade alfalfa`: cambiarla por
debajo dejaría a Homebrew creyendo que tiene otra versión. Si actualizas un clon con `git pull` y falta la
librería de búsqueda web, la app de Mac la instala sola al abrirse. En la
terminal, usa `alfalfa --instalar-buscador` o `pip install -e .`. Los `.venv`
creados con `uv`, que no traen pip, se instalan con `uv pip`. La app de Mac tiene
el mismo botón en
la barra lateral (flecha hacia abajo, o el menú del nombre), en Ajustes ▸
Actualizaciones y en el menú AlfalfaCode → «Buscar actualización…». Si la versión nueva cambia `alfalfa-mac/`, hay que volver a
compilarla con `swift build`.

Al publicar una versión, sube `__version__` en `alfalfa/__init__.py` y crea la
etiqueta con el mismo número. Una aplicación que lo integra debería fijar la
versión en sus dependencias.
