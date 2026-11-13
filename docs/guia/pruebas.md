# Pruebas

```bash
.venv/bin/python -m pytest -q
cd alfalfa-mac && swift test
```

El estilo se comprueba con `ruff check .` en el motor y con `swiftlint` dentro de
`alfalfa-mac/`. Las reglas, la deuda que queda y cómo partir los archivos grandes están
en [docs/ESTILO.md](../ESTILO.md).
