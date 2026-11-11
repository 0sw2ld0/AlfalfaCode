# Código de terceros en la app de Mac

## CuyTerm / SwiftTerm

La terminal integrada usa [CuyTerm](https://github.com/0sw2ld0/CuyTerm), copia de
[SwiftTerm](https://github.com/migueldeicaza/SwiftTerm) con estos cambios: sin dependencias,
y los programas no pueden leer el portapapeles ni escribirlo sin permiso (OSC 52). Su licencia:

```
Copyright (c) 2019-2026 Miguel de Icaza (https://github.com/migueldeicaza)
Copyright (c) 2017-2019, The xterm.js authors (https://github.com/xtermjs/xterm.js)
Copyright (c) 2014-2016, SourceLair Private Company (https://www.sourcelair.com)
Copyright (c) 2012-2013, Christopher Jeffrey (https://github.com/chjj/)
Copyright (c) 2016 Ryan L McIntyre (Powerline Extra Symbols flame silhouette, https://github.com/ryanoasis/powerline-extra-symbols)

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

## Dentro de AlfalfaCode.app (build-app.sh)

La app empaquetada lleva además programas de terceros sin cambios, cada uno con su licencia:

- **CPython** 3.13 de [python-build-standalone](https://github.com/astral-sh/python-build-standalone)
  (licencia PSF, en `motor/python/lib/python3.13/LICENSE.txt`).
- Las bibliotecas de Python de `motor-requisitos.txt` (httpx, mcp, jsonschema, pydantic,
  cryptography…), cada una con su licencia en su `*.dist-info`.
- **ripgrep** 15.2.0, binario oficial de [BurntSushi/ripgrep](https://github.com/BurntSushi/ripgrep)
  (MIT o Unlicense, en `motor/licencias/ripgrep/`).
- **CuyBrowser** 0.6.0, binario oficial para Apple Silicon. Las licencias de
  CuyBrowser, Playwright, Bun y sus componentes van en `motor/licencias/cuybrowser/`.
  [Fuentes de CuyBrowser](https://github.com/0sw2ld0/cuybrowser/tree/v0.6.0)
  [Fuentes de Bun](https://github.com/oven-sh/bun/tree/bun-v1.4.4)
