# Demo de «Qué hizo el agente»

La demo abre una página ficticia con Chromium propio mediante el CLI Node del
PR CB-5. Ejecuta navegar, leer y tomar una captura con el motor de AlfalfaCode y
comprueba que sólo la última acción tiene un PNG asociado. El servidor usa un
puerto efímero distinto de 8765. La prueba no abre formularios ni acepta diálogos
de CuyBrowser. No usa un navegador personal ni el binario instalado globalmente.

Construye primero CuyBrowser (`npm run build` en el worktree de CB-5) y AlfalfaKit
(`swift test` en `alfalfa-mac`). Desde el worktree de AlfalfaCode, ejecuta:

```bash
.venv/bin/python docs/demos/actividad-a10/demo.py \
  --cuy /ruta/al/worktree-cb5 --node /ruta/a/node22 \
  --swift-bin /ruta/a/alfalfa-mac/.build/arm64-apple-macosx/debug \
  --destino /ruta/nueva/de/evidencia
```

El lanzador compila un helper de depuración con la vista y el lector reales.
La app no inicia el motor ni proveedores, lee únicamente el hogar ficticio que
le pasa la demo y guarda `panel.png` a partir de su propia vista. Deja también
los informes `informe-sin-capturas.html` e `informe-con-captura.html`: el primero
contiene sólo metadatos; el segundo contiene los píxeles de la captura ficticia.
`cierre.json` confirma que el servidor y la app de ensayo terminaron.
