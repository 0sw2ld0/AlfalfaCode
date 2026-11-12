# Ensayo A11: del clic al archivo `.spec.ts`

El helper de depuración compila `BrowserEngine`, la tarjeta de guardado y la
paleta reales. Abre su servidor ficticio de loopback en un puerto efímero,
rellena la cantidad 2, pulsa «Crear pedido ficticio» y espera «Pedido ficticio
1 · total 20 soles» en WebKit. El ensayo comprueba que la oferta por sí sola
no crea un archivo. Después envía el clic al botón nativo de su propia ventana,
sin mover el puntero del sistema, y entrega el comando al transporte del motor.

Requiere Node y un binario Chromium o Chrome ya disponible. Desde el worktree,
usa una carpeta nueva y un hogar aislado. Instala el runner en esa carpeta,
sin descargar un navegador ni instalar nada global:

```sh
ensayo=/private/tmp/alfalfa-a11-ensayo-nuevo
mkdir -p "$ensayo/home/Library/Caches" "$ensayo/home/Library/Application Support" "$ensayo/tmp" "$ensayo/deps"
printf '%s\n' '{"name":"alfalfa-a11-fixture","version":"0.0.0","private":true}' > "$ensayo/deps/package.json"
env HOME="$ensayo/home" CFFIXED_USER_HOME="$ensayo/home" TMPDIR="$ensayo/tmp" \
  npm_config_cache="$ensayo/npm-cache" PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
  npm install --prefix "$ensayo/deps" --ignore-scripts --no-audit --no-fund --save-exact @playwright/test@1.64.1
env HOME="$ensayo/home" CFFIXED_USER_HOME="$ensayo/home" TMPDIR="$ensayo/tmp" \
  ALFALFA_HOME="$ensayo/home/.alfalfa" .venv/bin/python docs/demos/navegador-a11/ensayo.py \
  --deps "$ensayo/deps" --salida "$ensayo/resultado" --npx "$(command -v npx)" \
  --chrome '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
```

El ensayo ejecuta `npx --no-install playwright test` sobre el archivo guardado,
en su proyecto temporal y con un contexto Chromium nuevo. Las variables
ficticias apuntan sólo a su servidor. `resultado.json` y `playwright.log`
registran el resultado. La captura de la tarjeta y la de WebKit son de la app
de depuración; Playwright captura únicamente su página ficticia. La app y el
servidor se cierran al finalizar. No hace falta firmar ni notarizar el helper.

En la ejecución de esta rama, WebKit mostró el pedido 1 por 20 soles. El
archivo no existía al mostrar la oferta y sólo se creó al pulsar el botón.
El spec guardado pasó 1/1 en Chromium y comprobó el mismo texto visible.
