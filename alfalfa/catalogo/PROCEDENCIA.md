# Procedencia del catálogo de modelos

Los datos de los modelos (ventana de contexto, salida máxima, razonamiento,
herramientas, visión y precios) y de los proveedores (URL de la API, nombre de la
variable de la clave y página de documentación) vienen de
[models.dev](https://models.dev), repositorio
[sst/models.dev](https://github.com/sst/models.dev), de los creadores de OpenCode.

- AlfalfaCode baja `https://models.dev/api.json`, lo valida y guarda una versión
  reducida en `~/.alfalfa/catalogo/models.dev.json`.
- `minimo.json` es una copia reducida que viaja con el paquete para el primer
  arranque sin internet. Se rehace con `python scripts/catalogo_minimo.py`.

Los datos se usan con su licencia:

```
MIT License

Copyright (c) 2025 models.dev

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Los precios y los modelos gratis cambian a menudo: el catálogo es orientativo y
el coste que enseña AlfalfaCode es una estimación, no la factura del proveedor.
