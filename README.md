# AlfalfaCode

Arnés de agentes de programación para modelos de Ollama (local o Ollama Cloud),
de OpenAI, de Anthropic (Claude), OpenRouter, OpenCode Zen u otro proveedor
compatible con OpenAI con tu propia clave, o de un servicio Alfalfa.
Recibe un encargo, trabaja en la carpeta actual y pregunta antes de ejecutar lo
que no sea una lectura. La orden es `alfalfa`.

Se usa desde la terminal, con `--stdio` (JSONL) para integrarlo en otra
aplicación o con la app de Mac de `alfalfa-mac/`
(`cd alfalfa-mac && swift run AlfalfaCode`).

La app de Mac también se puede instalar sin el repositorio: `alfalfa-mac/build-app.sh`
la empaqueta con su propio Python y el motor dentro, y `alfalfa-mac/publicar.sh` hace
el DMG firmado y notarizado. Detalles en
[alfalfa-mac/EMPAQUETADO.md](alfalfa-mac/EMPAQUETADO.md).

## Instalar

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
- **`instalar.sh`** ([alfalfa/instalar.sh](alfalfa/instalar.sh)) hace esto:
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

### Desde el código

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Después `alfalfa` queda en `.venv/bin/`. También se puede lanzar con
`scripts/alfalfa`, que admite un enlace simbólico desde cualquier carpeta del `PATH`.

### La primera vez, sin claves

Puede que todavía no haya ningún proveedor: ni claves, ni un proveedor elegido,
ni Ollama en el Mac. Entonces `alfalfa` y la app no dan un error de conexión.
Enseñan los modelos de **Gratis ahora** (ver abajo), con su aviso de datos.

OpenRouter se conecta con un clic, sin pegar ninguna clave:

```bash
alfalfa --conectar openrouter
```

En la app, el botón está en Nuevo encargo («Empieza gratis») y en la hoja que pide
la clave de OpenRouter. Esto es lo que pasa:

1. Se abre OpenRouter en el navegador, donde entras o creas tu cuenta gratis y
   autorizas AlfalfaCode (OAuth con PKCE).
2. La vuelta llega a un servidor efímero en `localhost`. Sólo acepta la que trae
   el `state` correcto, y cualquier otra no corta la espera.
3. La clave pasa al Llavero sin que la veas.
4. Si no había ningún proveedor, queda como modelo por defecto un gratis probado
   con herramientas: Nemotron 3.5 Super VL. Lo eliges tú, después de ver el aviso
   de datos.

Si ya tenías un proveedor, `--conectar` sólo guarda la clave. Lo mismo pasa con
`--solo-clave`, que es lo que usa la hoja de la clave de la app: ni el proveedor
ni el modelo por defecto cambian. Un modelo gratis nunca se elige por su cuenta:
con OpenRouter por defecto y sin modelo elegido, `alfalfa "encargo"` pide
`--modelo`.

En una terminal, `alfalfa "encargo"` lo ofrece directamente y sigue con el
encargo. La pregunta lleva el aviso de datos, y hay que contestar «s»: Enter es
que no. Los demás proveedores con
modelos gratis dicen dónde se saca su clave gratis
(`alfalfa --guardar-clave nvidia`). `alfalfa --primera-vez [--json]` da el
mismo resumen. Para verlo en un Mac que ya tiene claves, usa
`ALFALFA_FORZAR_PRIMERA_VEZ=1`.

Las sesiones, reglas, complementos y hooks del usuario viven en `$ALFALFA_HOME`
(por defecto `~/.alfalfa`).

## Proveedores

El modelo dice con quién se habla:

| Modelo | Proveedor |
|---|---|
| `deepseek-v4.1-flash:cloud`, `ollama/…` | Ollama, local o Ollama Cloud |
| `openai/gpt-6-luna`, `openai/<modelo>` | OpenAI, con tu clave de la API (Responses API) |
| `anthropic/claude-sonnet-5-5`, `anthropic/<modelo>` | Anthropic (Claude), con tu clave de la API (Messages API) |
| `openrouter/…`, `opencode/…`, `<proveedor>/…` | un proveedor compatible con OpenAI del catálogo, con tu clave |
| `alfalfa/…` | un servicio Alfalfa, con un código de cortesía |

```bash
alfalfa --proveedores                         # estado, sin secretos
alfalfa --guardar-clave openai                # pide la clave sin enseñarla
alfalfa --guardar-clave anthropic             # la de Claude
alfalfa --fijar-url ollama https://ollama.com
alfalfa --proveedor-por-defecto openai
```

Las claves van al Llavero de macOS (servicio `AlfalfaCode`), nunca a un archivo.
También valen las variables estándar `OLLAMA_API_KEY`, `OPENAI_API_KEY` y
`ANTHROPIC_API_KEY`, y
`ALFALFA_OLLAMA_URL` (por defecto `http://127.0.0.1:11434`). Lo que no es
secreto (URL, proveedor por defecto) queda en `$ALFALFA_HOME/proveedores.json`.
La app de Mac lo configura todo en AlfalfaCode ▸ Ajustes… (⌘,) ▸ Proveedores, que
también abre «Proveedores…» en el selector de modelo.

### Claude (Anthropic)

Con tu clave de la API de Anthropic (se consigue en
https://platform.claude.com/settings/keys), el uso se cobra en tu cuenta.

- El selector recomienda Claude Haiku 5.5, Claude Sonnet 5.5 (el de por defecto si
  Anthropic es el proveedor por defecto) y Claude Opus 5.5, y ofrece debajo los demás
  modelos de tu cuenta (ver «Todos los modelos con tu clave»).
- El esfuerzo de la sesión es el de Claude (`low` a `max`), con razonamiento
  adaptativo, ajustado a lo que admite cada modelo: Opus 4.6 no tiene `xhigh` y se
  queda en `high`. El razonamiento firmado vuelve al modelo con los resultados de las
  herramientas, sólo en memoria.
- El prompt del sistema y las herramientas van en la caché de prompts, y la
  conversación usa la caché automática. El coste cuenta aparte lo leído y lo
  escrito en la caché.
- Haiku 5.5 tiene una ventana de 1M, pero cobra cinco veces más una petición cuyo
  prompt pase de 100K tokens: en modo Ahorro (el de por defecto) la sesión compacta
  antes de llegar ahí (ver «Modelos que cobran más por contexto largo»).

Con un **código de cortesía** de un servicio Alfalfa, que trae la dirección del
servicio detrás de una arroba (`CUY-XXXX-XXXX-XXXX@servicio.example.com`):

```bash
alfalfa --activar-codigo     # pide el CUY-…@servicio y lo ata a este Mac
alfalfa --cuota              # % usado este mes
```

El repositorio no trae la dirección de ningún servicio: llega con el código. Al
servicio sólo se le manda la parte `CUY-…`, y la dirección queda guardada en
`proveedores.json` cuando el servicio acepta el código. Sin esquema se usa HTTPS
(HTTP sólo en este mismo equipo, como `@127.0.0.1:8090`). Un código suelto, sin
arroba, vale si la dirección ya está guardada o se fija a mano con
`alfalfa --fijar-url alfalfa https://…`.

El código queda atado a este Mac con una clave del Secure Enclave: copiado a otro
ordenador no funciona. Los modelos salen como «GPT-6 Luna - Alfalfa» y la app
enseña una barra con el % de la cuota del mes.

### Todos los modelos con tu clave (OpenAI y Anthropic)

Con tu propia clave de OpenAI o de Anthropic pagas tú el uso, así que el selector
ofrece todos los modelos de tu cuenta que sirven para programar, no sólo los
recomendados. (Un código de cortesía de Alfalfa Cloud sigue con los modelos que el
servicio da a ese código.)

- Con la clave se pide la lista real a `GET /v1/models` de cada proveedor, con un
  tiempo de espera corto (5 s). Se guarda en `$ALFALFA_HOME/catalogo/modelos-openai.json`
  y `modelos-anthropic.json` con su fecha y una huella de la cuenta (nunca la clave) y
  se renueva cada 6 horas al listar los modelos; tras un fallo no se reintenta hasta
  pasados 10 minutos. El motor nunca espera a la red: en cada turno lee la copia guardada.
- Sin clave, sin red o si la lista nunca llegó, salen los modelos de ese proveedor en
  el catálogo de models.dev.
- Se cruza con models.dev para la ventana, el precio, los tramos de contexto largo, la
  visión, el razonamiento y los esfuerzos. De Anthropic además vale lo que la propia API
  dice de cada modelo (`capabilities`: esfuerzos, visión, razonamiento adaptativo;
  `max_input_tokens`, `max_tokens` y `lifecycle`).
- Sólo salen modelos de chat que llaman herramientas y no están retirados. En OpenAI
  quedan fuera los embeddings, la voz (TTS, transcripción, audio y realtime, que no
  hablan la Responses API), la imagen y el vídeo, la moderación, la búsqueda y los de
  completions antiguos; en Anthropic, los `deprecated` y `retired`. Las instantáneas con
  fecha de un modelo que también está (`gpt-4o-2024-08-06` junto a `gpt-4o`) no se repiten.
- Un modelo que la API devuelve y models.dev no describe sale igual, con «no se sabe»:
  sin precio y con la visión sin saber (se dejan adjuntar imágenes).
- Arriba van los recomendados (GPT-6 Luna; Claude Haiku, Sonnet y Opus 5.5), que
  conservan sus datos propios (la ventana de Luna, el tramo largo de Haiku), y los que
  añadas a mano en `proveedores.json` (`"openai": {"modelos": ["gpt-6-sol"]}`); debajo,
  el resto, los más nuevos primero. En la app, dentro del grupo del proveedor, el resto
  va en el submenú «Más modelos».
- El esfuerzo de la sesión se ajusta a cada modelo: el más cercano por debajo de entre
  los que admite (GPT-5 no tiene `xhigh` y pide `high`; GPT-5 Pro sólo admite `high`).
  A los que no razonan (GPT-4o, GPT-4.1) no se les manda razonamiento.

### Catálogo de modelos (models.dev)

AlfalfaCode sabe la ventana de contexto, la salida máxima, si razona y con qué
esfuerzos, si llama herramientas, si ve imágenes y el precio de cada modelo por el
catálogo público de [models.dev](https://models.dev) (MIT, de los creadores de
OpenCode). Funciona así:

- Se baja `https://models.dev/api.json` sin clave, se valida (tamaño máximo,
  identificadores, URL sólo HTTPS, números finitos) y se guarda reducido en
  `$ALFALFA_HOME/catalogo/models.dev.json` con su fecha.
- Se refresca cada 24 horas, al listar los modelos. Sin red vale la última copia;
  tras un fallo no se reintenta hasta pasada una hora.
- La primera vez sin internet vale la copia mínima que viaja con el paquete
  (`alfalfa/catalogo/minimo.json`: OpenAI, Anthropic, Ollama Cloud, OpenRouter,
  OpenCode Zen y NVIDIA).
- El motor nunca espera a la red: en cada turno lee la copia que haya.
- Lo que responde el propio proveedor manda. Por ejemplo, la ventana de
  `/api/show` de Ollama. El catálogo sólo rellena lo que falta: la ventana que usa
  la compactación y el tope de salida que se pide.

```bash
alfalfa --catalogo           # fecha, origen y cuántos modelos gratis hay ahora
```

Los datos de models.dev y su licencia están en
[alfalfa/catalogo/PROCEDENCIA.md](alfalfa/catalogo/PROCEDENCIA.md).

### Gratis ahora

El selector de modelos tiene una sección **Gratis ahora**. Entran los modelos con
coste de entrada y de salida 0 que cumplen todo esto:

- son de un proveedor de nube que habla el formato de OpenAI por HTTPS;
- llaman herramientas;
- no están retirados.

Se cuida qué significa ese 0:

- Se excluyen los proveedores locales (LM Studio y similares), donde 0 es «corre
  en tu máquina».
- Se excluyen los planes de suscripción (`*-coding-plan`, `*-token-plan`,
  OpenCode Go), donde 0 es «incluido en el plan».
- Se excluyen los enrutadores `auto`.

La lista se actualiza sola con el catálogo.

**Gratis no quiere decir privado.** Junto a cada modelo gratis sale el aviso
«Gratis: puede usar tus datos para entrenar». Muchos proveedores pagan los modelos
gratis guardando lo que les mandas para entrenar o mejorar sus modelos. No mandes
nada confidencial a un modelo gratis sin leer antes las condiciones de su
proveedor.

AlfalfaCode no revende nada: cada petición va desde tu Mac al proveedor, con tu
clave.

### Proveedores compatibles con OpenAI

OpenRouter, OpenCode Zen y cualquier otro proveedor del catálogo que hable el
formato de OpenAI funcionan con tu clave y la URL del catálogo.

- Se usa Chat Completions.
- Se usa la Responses API en los modelos que el catálogo marca así (los GPT de
  Zen, por ejemplo).
- Para añadir una clave:

  ```bash
  alfalfa --guardar-clave openrouter     # pide la clave sin enseñarla
  alfalfa --guardar-clave opencode       # OpenCode Zen
  alfalfa --guardar-clave nvidia         # cualquier otro id del catálogo
  ```

  En la app, en Ajustes ▸ Proveedores. Si eliges en el selector un modelo cuyo
  proveedor no tiene clave, AlfalfaCode te dice cuál falta (la variable del
  catálogo, como `OPENROUTER_API_KEY`) y dónde conseguirla, y la guarda ahí mismo.
- La clave va al Llavero y queda atada a la URL que había al guardarla. Si el
  catálogo cambiara luego esa URL, la clave no la sigue.
- Sólo OpenRouter y OpenCode Zen leen también su variable estándar
  (`OPENROUTER_API_KEY`, `OPENCODE_API_KEY`). De los demás no se lee ninguna
  variable, porque su nombre viene de un dato externo.
- Además de los gratis, en el selector salen los modelos de pago que añadas en
  `proveedores.json`:

  ```json
  {"openrouter": {"modelos": ["anthropic/claude-sonnet-5"]}}
  ```

**OpenCode Zen y otras herramientas.** Según su documentación, los modelos de pago
de Zen se pueden usar desde cualquier agente, pero sus modelos gratis
«require OpenCode». Por eso los gratis de Zen no salen en «Gratis ahora». Sus
condiciones de uso también limitan el uso a tu propio uso interno.

### Coste estimado

Con los precios del catálogo, AlfalfaCode estima lo que cuesta cada turno y la
sesión entera. Usa los tokens de entrada, de salida y de caché que devuelve el
proveedor.

- La app lo enseña donde ya enseña los tokens: en el panel de la sesión, en
  `/estado` y en la lista de sesiones.
- Sólo se estima para quien cobra por token: OpenAI, Anthropic y los compatibles. Ollama local
  no cuesta nada; Ollama Cloud y el servicio Alfalfa se pagan con su plan o su
  cuota.
- Es una estimación, no la factura del proveedor.
- Lo que gastan los subagentes cuenta para la sesión que los lanzó.

### Caché de prompts

Anthropic y OpenAI sirven de su caché la parte del prompt que ya vieron, a un
precio menor. Por cada sesión, AlfalfaCode suma los tokens leídos de la caché (y los
escritos, en Anthropic) y compara lo que habrían costado esas peticiones sin caché
con lo que costaron.

- La app lo enseña en el panel de la sesión («Caché: ahorró 0,42 $», con el detalle
  al pasar el ratón) y en `/estado`; la terminal, al acabar cada turno.
- En Anthropic escribir en la caché cuesta 1,25× la entrada: al principio de una
  sesión puede costar más de lo que ahorra, y se dice así («aún cuesta … más»).
- Con Ollama no aplica.

### Presupuesto

Dos topes en dólares, los dos opcionales:

- **por sesión**: lo que lleva la sesión, con sus subagentes;
- **al mes**: lo gastado este mes natural por todas las sesiones de este Mac.

Al 80 % de un tope AlfalfaCode avisa una vez: el de la sesión, una vez por sesión; el
del mes, una vez al mes en este Mac. Antes de cada petición al modelo (respuestas del
turno, compactar, mirar una imagen, recuperar una respuesta cortada) suma lo gastado,
lo que costará la entrada de esa petición y lo apartado por las que ya están en marcha,
como las de los subagentes en paralelo. Si pasa del tope, no la hace: el turno se
detiene limpio, nunca a mitad de una herramienta, y dice cuánto se gastó y cómo subir
el tope. La sesión queda lista para seguir.

- **Margen**: la entrada se estima por el tamaño del contexto, contando como caché lo
  que leyó la petición anterior. Lo que no se puede saber antes es la salida, así que
  un tope puede pasarse, como mucho, lo que cueste la salida de la última respuesta
  (con Opus 5.5 y 32.000 tokens de salida, unos 0,64 $). Otra sesión abierta a la vez
  sólo cuenta para el tope del mes cuando su petición ya se apuntó.
- Con un tope alcanzado, un mensaje nuevo no entra en el historial del modelo ni
  dispara los hooks; «Compactar» dice que no se compactó.
- Sólo cuenta lo que tiene precio: un modelo local, de plan o del servicio Alfalfa
  con cortesía no gasta, y puede seguir aunque el tope se haya alcanzado. Un modelo
  de pago que el catálogo no tiene con precio tampoco cuenta, y se avisa una vez.
- Bifurcar una sesión o seguir en una nueva empieza su gasto de cero; el del mes sigue
  sumando.
- Se configura en la app, en **Ajustes ▸ General ▸ Presupuesto**, y vale ya para
  las sesiones abiertas. En la terminal, `alfalfa --presupuesto 5 --presupuesto-mes 40 "…"`
  o `ALFALFA_PRESUPUESTO=5 ALFALFA_PRESUPUESTO_MES=40`. Con `0` se quita el tope.
- El gasto del mes vive en `~/.alfalfa/gasto/AAAA-MM.jsonl`, una línea por
  petición con coste. Ese archivo sólo crece y se lee por el final, así que
  sobrevive a los reinicios sin engordar las sesiones.

## Usar

```bash
alfalfa --modelo deepseek-v4.1-flash:cloud "corrige la prueba que falla"
alfalfa --sesiones
alfalfa --reanudar ID "sigue"
alfalfa --bifurcar ID --turno T "prueba otra solución"
alfalfa --volver ID --turno T          # conversación y código a antes de ese mensaje
alfalfa --rehacer ID                   # devuelve el código que quitó el último deshacer
alfalfa --comprobar-orden git push
```

## Imágenes

```bash
alfalfa --modelo deepseek-v4.1-flash:cloud --imagen captura.png "¿qué falla aquí?"
```

Como en Codex, cada imagen va al texto con `<image name=[Image #N] path="...">`, se
copia a la sesión reducida a 2048 px como mucho y viaja a Ollama en el campo
`images` de `/api/chat`. Hace falta un modelo con visión. En `--stdio` el mensaje
lleva `"imagenes": ["/ruta/absoluta.png"]`. En la app de Mac se adjuntan con el
botón «+», pegándolas con ⌘V o soltándolas sobre el cuadro de texto, y salen como
miniatura, como en Codex: si el campo de texto escribe la ruta al soltarla, la app la
quita y la adjunta. Una captura arrastrada desde su miniatura flotante se copia a
los adjuntos, porque macOS borra enseguida su archivo temporal.

Cada modelo de `--modelos --json` dice si ve imágenes (`vision`: true, false o null si
no se sabe), de su `/api/show` en Ollama, de lo que dice cada proveedor o el servicio
Alfalfa, o del catálogo de models.dev. Un mensaje con imágenes para un modelo que no las
ve se rechaza antes de empezar el turno, con un error que nombra modelos con visión que
tienes de verdad; si quien integra AlfalfaCode configuró una visión delegada, las imágenes
se describen con ella en vez de fallar. Las imágenes que ya estaban en la conversación (o
que devuelve una herramienta) no rompen el turno de un modelo sin visión: se describen
con la visión delegada o el mensaje dice que no se envían. Si no se sabe si las ve, se le
mandan y el proveedor dirá. En la app, con un modelo sin visión el botón de adjuntar queda
desactivado («Este modelo no ve imágenes»), pegar o soltar una imagen lo avisa, las
miniaturas ya adjuntas se marcan y al enviar se ofrece cambiar a un modelo con visión o
quitarlas.

Si el modelo tiene visión, el agente también puede mirar por su cuenta una imagen
del proyecto con `inspect_image`, como `view_image` en Codex: el mismo modelo de
la sesión recibe la imagen y responde a la pregunta. Sin visión, la herramienta
no se ofrece. Una aplicación que integra AlfalfaCode puede delegarlo en otra
sesión con `alfalfa.ajustes.vision_delegada`.

Al revés, el agente también puede enseñarte imágenes y vídeos, como en la app de Codex.
Si responde con una línea `![Tolgrenn — caminar](/ruta/lamina.png)`, la app de Mac
pinta ahí la imagen con el texto como pie de foto, y varias seguidas forman una
cuadrícula. Valen PNG, JPG, HEIC, WebP, GIF (animado) y, en un reproductor sin sonido
y en bucle si es corto, MP4 y MOV. Sólo archivos locales que existan, con ruta
absoluta, `~/` o relativa a la carpeta de la sesión: una URL web se queda como enlace y
nunca se descarga nada. Un clic la abre en Vista rápida y el menú contextual la muestra
en Finder o la copia. Copiar o exportar la respuesta sigue dando el Markdown original, y
en la terminal se ve la ruta.

## Archivos: PDF, texto, código, Word y Excel

```bash
alfalfa --archivo informe.pdf --archivo datos.xlsx "resume el informe y cuadra las cifras"
```

Además de imágenes se adjuntan PDF, texto y código (txt, md, csv, json, yaml, py, swift,
ts, go…), Word (.docx) y Excel (.xlsx): hasta 8 por mensaje, con un tope de 20 MB por
PDF, Word o Excel y de 2 MB por archivo de texto. El texto lleva una etiqueta por
archivo, `<file name="informe.pdf" path="/ruta/original">`, que la app enseña como un
chip en la burbuja y que se conserva al reanudar. En `--stdio` el mensaje lleva
`"archivos": ["/ruta/absoluta.pdf"]`. En la app se adjuntan igual que las imágenes (botón
«+», ⌘V con un archivo copiado en Finder o soltándolos), y con cualquier modelo.

El motor copia cada archivo a la carpeta privada de la sesión (`archivos/`, 0600) sin
seguir enlaces simbólicos y extrae su texto una vez: el PDF con PDFKit de macOS, por
páginas; Word y Excel leyendo su XML. Lo que llega al modelo depende de lo que admita:

- **Claude API y OpenAI** (modelos con visión, capacidad `pdf` de `/api/show`): el PDF va
  entero como documento (`document` en `/v1/messages`, `input_file` con `file_data` en la
  Responses API), sin subirlo a ninguna Files API. Así el modelo ve también tablas e
  imágenes de cada página. Con más de 100 páginas va su texto.
- **Ollama, Alfalfa Cloud y los compatibles con OpenAI**: el texto extraído, dentro del
  mensaje. Un PDF escaneado sin texto se dice tal cual («no tiene texto que se pueda
  extraer») y el agente no debe inventar su contenido.

Codex no manda archivos al modelo: su TUI sólo pone la ruta en el mensaje para que el
agente lo lea con sus herramientas. Aquí se hace eso cuando no se puede leer: el aviso
dice la ruta original. El contenido de un adjunto cuenta como contenido externo: va
marcado como datos y no instrucciones, con los secretos del proyecto tapados, y cada
acción posterior con efectos pide su aprobación, como tras un `read_file`. Si el texto
de un PDF contiene un secreto, no se manda como documento (no se podría tapar dentro):
va su texto con el secreto tapado. Lo que pesa cuenta para el presupuesto y la
compactación (unos 2 000 tokens por página de PDF como documento); con más de 50 000
tokens o 100 páginas se avisa, y el texto de un archivo se recorta a 400 000 caracteres.

## Cierre de cada turno

Como la caja «siguiente paso» de Claude Code: cuando un turno cambió archivos o
ejecutó órdenes, el agente llama a `wrap_up` antes de su respuesta. Da un resumen
corto de cómo queda el trabajo y, si lo siguiente lo decide el usuario, una
pregunta con una o dos respuestas. En `--stdio` llega como `cierre` dentro de
`turno_terminado` (`resumen`, `pregunta`, `opciones`). La app de Mac lo pinta como
una tarjeta, y cada botón manda su texto como el siguiente mensaje; el primero
también responde con ⌘↩. En la terminal sale como «Siguiente paso». Sólo lo
ofrece el catálogo de AlfalfaCode; los agentes del integrador no lo ven.

Como las tarjetas «Nueva sesión» de Claude Code, el agente puede proponer una
tarea aparte con `propose_session` (`title` y `prompt`, el encargo completo,
porque la sesión nueva no ve la conversación). Sirve para algo fuera del
encargo o para el siguiente paso que recomienda y no va a hacer ahora. Puede
proponer hasta tres por turno, y no ejecuta nada: viaja en el mismo `cierre`, como
`sesiones: [{titulo, encargo}]`, y se guarda con el turno. La app enseña una
tarjeta por propuesta. «Nueva sesión» empieza el encargo aparte, en la misma
carpeta y con el mismo modelo, esfuerzo y modo, sin cambiar de pantalla: la
sesión sale en la barra lateral. «Hacerlo aquí» se lo pide a la sesión actual. En
la terminal se imprime el encargo para abrir otra sesión a mano.

La sesión que nace así es hija de la que la propuso: se crea con `--padre ID`, que
queda guardado en ella y sale como `padre` en `--sesiones --json`, y con `--titulo`
y el título de la propuesta, que la barra lateral enseña en vez del principio del
encargo. Si el padre vuelve a abrirse, su tarjeta ya no ofrece «Nueva sesión». En la barra
lateral va debajo de su padre, con sangría. Cada vez que la hija termina un turno, la
app le manda al padre una nota con los primeros 400 caracteres de su informe: el
resumen de `wrap_up` o, si no lo hay, su última respuesta. Si el padre está
trabajando, la nota entra al acabar su turno. Si está dormido o cerrado, se despierta
sin cambiarte de pantalla. Un turno que corta el usuario no manda nota.

## Sesiones desde la app

`--marcar ID titulo|fijada|archivada VALOR`, `--eliminar-sesion ID` (a la Papelera
de macOS) y `--solo-bifurcar ID` responden una línea JSON. Son lo que usa el menú de
clic derecho de la barra lateral. En ese menú, «Mostrar en Finder» abre la carpeta del
proyecto y «Mostrar historial en Finder», la de la sesión en `$ALFALFA_HOME/sesiones`.
El botón de carpeta de la cabecera de una sesión también abre la del proyecto.

La ventana de Ajustes (⌘,) guarda el nombre del pie de la barra lateral, la carpeta,
el modelo, el esfuerzo y el modo de permisos de los encargos nuevos, los proveedores,
si se mantiene el Mac despierto mientras trabaja y la búsqueda de actualizaciones. Para revisarla sin clics, `ALFALFA_ABRIR_AJUSTES=modelo`
la abre en esa parte al arrancar (`general`, `proveedores`, `modelo` o
`actualizaciones`).

Al abrir una sesión guardada, la app carga su conversación (`alfalfa --historial ID
--json`) y la deja lista para seguir, como un hilo de Codex: el siguiente mensaje
abre un turno nuevo con `--reanudar ID "mensaje"`, también si la sesión ya estaba
completada. Las interrumpidas o bloqueadas tienen además «Reanudar» para seguir
sin mensaje.

## Buscar en sesiones anteriores

El agente tiene `buscar_sesiones`: busca en las conversaciones guardadas de otras
sesiones para recordar qué se decidió, qué se probó o qué pediste. Devuelve
fragmentos reales (nunca un resumen hecho por un modelo) con la sesión, el turno, la
fecha y quién lo dijo, y cada resultado trae una `ref` para pedir los mensajes de
alrededor (`alrededor: "a1b2c3#42"`). Sólo lee, así que está en todos los modos,
también en Plan. La sesión en curso no entra: ya está en su contexto. La idea es la
de `session_search` de Hermes Agent.

```bash
alfalfa --buscar-sesiones "compactación tarifa"         # sesiones de esta carpeta
alfalfa --buscar-sesiones "compactación tarifa" --todas # de todas las carpetas
alfalfa --ver-resultado a1b2c3d4e5f6a7b8#42             # los mensajes de alrededor
alfalfa --reindexar                                     # rehacer el índice desde cero
alfalfa --borrar-indice                                 # borrarlo (se rehace al buscar)
```

Con `--json` responden una línea `busqueda_sesiones`, `sesiones_alrededor` o
`indice_sesiones`. `--tiempo S` (más de 0) limita lo que la búsqueda espera a que el
índice se ponga al día, y una consulta que empieza por «-» va pegada a la opción
(`--buscar-sesiones=-x`). En la app, «Buscar» de la barra lateral busca a la vez en
los títulos y, con tres letras o más, dentro de las conversaciones (la misma orden,
con `--todas`, `--incluir-externas` y `--tiempo 3`; al escribir otra letra, la
búsqueda anterior se termina); al pulsar un resultado se abre su sesión en ese
mensaje, y lo que viene de fuera lleva «de fuera».

- **Dónde busca el agente.** Por defecto, sólo en las sesiones de la misma carpeta de
  trabajo. Ampliarlo a todas lo decides tú: `alfalfa --alcance-busqueda todas` (se
  guarda en `proveedores.json`; `carpeta` lo devuelve) o `ALFALFA_BUSCAR_SESIONES=todas`.
- **Índice.** `$ALFALFA_HOME/indice/sesiones.sqlite3`, SQLite con FTS5 (sin tildes ni
  mayúsculas; si las palabras enteras no dan nada, se prueban como prefijos). Si el Python
  no trae FTS5, se busca con LIKE en la misma tabla. Se pone al día solo y por partes:
  de cada `messages.jsonl` lee sólo lo que hay detrás de lo ya indexado, empezando por
  las sesiones más recientes y con un tope de tiempo, así que una búsqueda del agente
  nunca espera a indexarlo todo. Las sesiones viejas (versiones 1 a 4) también entran;
  una línea a medias (un guardado cortado) se lee cuando esté entera.
- **Secretos.** Se tapan al guardar en el índice (también el título de la sesión) y
  otra vez al devolver: las claves conocidas de los proveedores y de los servidores
  MCP; los campos con nombre sensible (token, key, password, auth, cookie…), también
  si son listas y también como `"clave": "valor"` dentro de un texto (una salida larga
  guardada cortada, un archivo leído; con comillas simples y con las barras que sean),
  y el `value` de un par `{"name": "X-Api-Key", "value": …}`; las asignaciones
  `ALGO_TOKEN=…`, `DB_PASS=…`, `MYSQL_PWD=…`, `…_PASSPHRASE=…` y `password: …`;
  `.dockerconfigjson`; el valor de cada cookie de `document.cookie`; las cabeceras `Authorization:`, `Cookie:` y `Set-Cookie:`; `Bearer …`;
  la clave de `https://usuario:clave@servidor`; las claves privadas PEM; las formas
  conocidas de claves de API y lo que tiene forma de clave secreta de AWS. La salida
  de una herramienta que leyó un archivo con secretos (`.env`, `.ssh`, `*.pem`,
  `credentials.json`, `.netrc`…) o volcó el entorno o un almacén de claves (`env`,
  `printenv`, `security`, `op`, `gh auth token`, `aws configure get`, `kubectl get
  secret`…, en cualquier lugar de la orden: `sh -c "…"`, `find … -exec env ;`,
  `xargs printenv`, `timeout 5 env`) o que trae código que lee el entorno
  (`os.environ`, `process.env`, `getenv`…) no se guarda. Si entra una clave nueva en el Llavero, la siguiente
  búsqueda la tapa también en lo ya indexado (se guarda sólo una huella HMAC, con una
  sal propia del índice, del conjunto de claves). Es una red, no una garantía: una contraseña sin forma reconocible que
  imprima un programa cualquiera puede pasar. El razonamiento (`thinking`,
  `alfalfa_bloques`) nunca se lee.
- **Salidas externas.** Lo que devolvieron `read_web`, `web_search`, el navegador
  (`browser_*`) y los servidores MCP (`mcp__*`) sale con `origen: "externo"` y un aviso
  de que puede traer instrucciones de terceros; lo que dijo el agente después en ese
  tramo (hasta el siguiente mensaje tuyo) también, porque puede repetirlas. El título
  de una sesión propuesta por el agente (`propose_session`) sale con
  `titulo_origen: "agente"`. Buscando en todas las carpetas, de las otras sólo salen
  tus mensajes y los del agente no marcados: ninguna salida de herramienta (un README
  malicioso leído con `read_file` también es de fuera). `--incluir-externas` las
  devuelve, marcadas, para lo que lee una persona (la app); el agente nunca lo usa.
- **Tamaño y borrado.** Cada mensaje se guarda recortado (8.000 caracteres; 4.000 la
  salida de una herramienta), una sesión que ya no está se quita del índice
  (`--eliminar-sesion` lo hace en el acto) y, pasado el tope (`ALFALFA_INDICE_MAX_MB`,
  256 MB), se sueltan las sesiones más viejas hasta que el archivo queda por debajo.
  Lo quitado no queda legible en el archivo: borrado seguro de SQLite y de FTS5, y
  después se optimiza, se devuelven las páginas libres y se vacía el WAL (si otra sesión
  está leyendo no se la espera: queda apuntado y lo hace la siguiente búsqueda). Sin FTS5, la
  búsqueda con LIKE sólo recorre los 20.000 mensajes más recientes. El índice es
  desechable: borrarlo no pierde nada, porque las sesiones no se tocan; si se rompe,
  se rehace solo (ocupado por otra sesión, no se toca). Crearlo y borrarlo van con un
  cerrojo de archivo que se espera como mucho 5 s.

## Modelo, esfuerzo y contexto

El modelo y el esfuerzo se pueden cambiar en una sesión ya empezada, como `/model`
en Codex: la app manda `{"tipo":"ajustes","modelo":…,"esfuerzo":…}` por `--stdio`
y el cambio vale desde el siguiente turno. Cada `contexto` trae, además del total
que mide Ollama, un `desglose` estimado (mensajes, instrucciones, herramientas,
MCP y, si los hay, archivos de memoria, instrucciones MCP y complementos), el uso
`estimado` con lo añadido desde la última medida, el `umbral` de la compactación
automática y la lista de archivos de `memoria` (`AGENTS.md`). Con servidores MCP
también trae las herramientas cargadas con su peso y cuántas siguen diferidas. La
última medición se guarda con la sesión, y el panel la enseña al abrirla, como el
de Claude Code: % usado, cada parte con sus tokens, lo añadido desde la última
medida, la reserva de compactación, el espacio libre y cuánto falta para compactar.

## Compactar

La compactación automática salta al 90 % de la ventana (o en `--compactar-en`).
Además se puede compactar a mano en cualquier momento, como `/compact` de Claude
Code: el botón **Compactar sesión** del panel o `/compactar`, con instrucciones
detrás si quieres centrar el resumen (`/compactar conserva las rutas y los
errores`). Entre turnos se compacta al momento sin abrir turno; con un turno en
marcha, antes de la siguiente respuesta del modelo. Por `--stdio` es
`{"tipo":"compactar","instrucciones":"…"}`. Una sesión guardada se abre sin turno
con `--reanudar ID --esperar` para compactarla.

La conversación enseña la marca «Conversación compactada · 120k → 9k» con el
resumen que ahora lee el modelo, plegado; el historial completo sigue guardado.
El evento es un `evento` de sistema con `compactacion`
(`estado` `en_curso`/`hecha`/`fallida`, `motivo`, `antes`, `despues`, `resumen`).
Cerca del umbral, el pie del cuadro avisa de cuánto falta («12 % hasta compactar»).
`compactacion` lleva también `composicion`: cuántos tokens había por tipo de
mensaje (`tool.content`, `assistant.tool_calls`…), sin su texto.

### Modelos que cobran más por contexto largo

Algunos modelos cobran más toda la petición pasado un umbral: GPT-6 Luna, por
encima de 272K tokens de entrada, 2× la entrada y la caché y 1,5× la salida (su
ventana es de 1.050.000); Claude Haiku 5.5, si el prompt pasa de 100.000 tokens
contando la caché, 5× (ventana de 1M). El umbral sale de los tramos de precio de
models.dev (`tiers`, o `context_over_200k` en el formato antiguo), de una tabla
propia comprobada en las páginas oficiales, o de lo que diga el proveedor
(Alfalfa Cloud lo manda en `/api/show` como `alfalfa_tarifa`). `--modelos --json`
lo da junto a la ventana (`barato_hasta`, y `tarifa_larga` con los multiplicadores),
y el selector lo enseña: «1M · barato hasta 100K».

Cada sesión elige cómo usarlo, y se guarda con ella:

- **Ahorro** (por defecto): la compactación automática salta también al 90 % del
  umbral. Al 80 % la conversación avisa («A partir de 100K tokens, Claude Haiku 5.5
  cobra 5× toda la petición. Compactaré en unos 10K») con tres botones: **Compactar
  ahora**, **Seguir con contexto completo** (no vuelve a preguntar en esa sesión) y
  **Seguir en una sesión nueva**, que compacta y abre otra que empieza sólo con el
  resumen (con un turno en marcha, espera a que termine; hereda los cambios del
  agente pero no las incidencias, que señalan una conversación que ya no ve). Si una
  salida de herramienta enorme llevaría una sola petición por encima del umbral, se
  recorta antes de enviarla (por el medio, como las salidas viejas), empezando por
  las que aún no están en la caché del proveedor, de la más nueva a la más vieja;
  se guarda sólo hasta cuántos tokens se recortó, para que las peticiones siguientes
  sean idénticas y la caché siga valiendo, y el historial completo no cambia. Si tras
  compactar lo fijo (instrucciones, herramientas y resumen) sigue por encima del
  umbral, lo dice una vez y no vuelve a compactar sola hasta que la conversación crezca.
  Los agentes que usan el motor sin la compactación de Codex también compactan antes
  del umbral, y los subagentes siguen el modo de la sesión que los lanza.
- **Contexto completo**: usa la ventana entera y avisa una vez al pasar el umbral.

Se cambia con `/contexto ahorro` o `/contexto completo`, en el panel de contexto,
con `--contexto ahorro|completo` en la terminal, o con
`{"tipo":"contexto","modo":"completo"}` por stdio (vale ya, también a mitad de
turno). El evento `sesion` dice el modo (`contexto`), el `contexto` lleva `tarifa`
(`desde`, `entrada`, `salida`, `modo`) y el aviso es un `evento` de sistema con
`tarifa` (también en `--historial`, donde la app lo pinta ya contestado). El coste
estimado cobra el tramo aunque models.dev no lo traiga (GPT-6 Luna). «Seguir en una sesión nueva» es `{"tipo":"continuar_en_nueva"}`: el
motor responde `sesion_cambiada` con `continuada_de` y se cierra. Con un código de
Alfalfa Cloud el servicio anuncia como ventana el umbral barato, salvo a los
códigos con contexto completo, que reciben la ventana real y el umbral.

Lo que lee el modelo tras compactar junta Codex y Claude Code, para que no rehaga
lo que ya hizo ni tome por suyos cambios ajenos:

- El resumen va por apartados, como en Claude Code: encargo actual, restricciones,
  lo hecho, errores, pedidos anteriores (atendidos o no), lo pendiente, en qué
  estaba y un siguiente paso que sólo continúa el encargo actual.
- Tus mensajes anteriores van juntos y marcados como historia ya atendida; sólo
  el último va como mensaje tuyo. Al compactar entre turnos también el último es
  historia.
- Antes de tu último mensaje, como el contexto que Codex vuelve a dar, va la lista
  de archivos que cambió el propio agente: el resto no lo mete en un commit ni lo
  revierte sin preguntar.

Al empezar cada turno el motor da al modelo un informe breve de estado (archivos
que cambió, cambiados por fuera, últimos fallos), con tope, y sólo cuenta el
último: antes iba entero en cada turno y llenaba el contexto.

Las instrucciones base piden, como Codex y Claude Code, que un commit añada por
nombre sólo los archivos del agente (nunca `git add -A` con otros cambios) y que
pregunte si encuentra cambios inesperados.

## Plan del agente

Como en Codex y Claude Code, el agente lleva una lista de tareas con `update_plan`
(pasos `pending`, `in_progress` o `completed`, y una `explanation` opcional). La
app la enseña bajo la conversación mientras trabaja o le quedan pasos. Si
escribes mientras tanto, se queda como «Plan de antes de tu último mensaje» hasta
que el agente mande otro. `--stdio`: evento `plan`
(`{"pasos":[{"paso":"…","estado":"in_progress"}],"explicacion":"…"}`), también al
reanudar una sesión.

## Opciones de cada mensaje

Bajo cada mensaje tuyo la app muestra hace cuánto lo enviaste y tres botones, como
en Claude: copiarlo; **volver** a él; y **bifurcar**, que abre una sesión nueva con
la conversación hasta ese mensaje y su respuesta (`--solo-bifurcar ID --turno T`; los
archivos no cambian).

## Volver a un mensaje y deshacer el código

Antes y después de cada turno, AlfalfaCode guarda una instantánea del contenido de la
carpeta de trabajo en un git aparte, fuera del proyecto:
`$ALFALFA_HOME/instantaneas/<huella de la carpeta>` (`--git-dir` ahí, `--work-tree`
en la carpeta). El `.git` del proyecto no se toca y funciona igual en una carpeta que
no es repositorio. Como guarda la carpeta entera, cubre también lo que cambian las
órdenes (`run_command`), no sólo las ediciones. Guarda los bytes tal cual (sin los
`text`, `eol` ni `working-tree-encoding` de los `.gitattributes`). Respeta los
`.gitignore`, el `.git/info/exclude` del proyecto y el ignore global de git; salta las
cachés que ya conocen los permisos (`__pycache__`, `.pytest_cache`…), las carpetas
`.tmp-…/`, `node_modules/` y los entornos de Python, los repositorios anidados, los
archivos que no se pueden leer y los de más de 5 MB, y apunta todo lo que deja fuera.
Las instantáneas de más de 30 días se borran, y también las más viejas si el almacén
pasa de 2 GB (nunca las de los dos últimos días ni las de deshacer y rehacer).
`ALFALFA_INSTANTANEAS=0` las apaga; un fallo al guardar una nunca para el turno.

En un repositorio git no copia lo que el proyecto ya tiene: el almacén lee sus objetos
(`objects/info/alternates` al almacén común, también desde un worktree) y la primera
instantánea parte del árbol de HEAD, así que sólo escribe lo cambiado y lo nuevo. Se
toma mientras el modelo ya responde: sólo la esperan las herramientas que pueden
cambiar archivos (editar, órdenes, parches, subagentes, MCP…). Si tarda, la app y la
terminal dicen «Preparando deshacer…» con el tamaño. Si no termina en 90 s, avisa una
vez de que deshacer no está disponible en esa carpeta y no lo reintenta hasta el día
siguiente. Si el proyecto reescribe su historia y un `git gc` borra objetos que una
instantánea tomaba prestados, esos archivos no se restauran (salen como «perdido»).

Al pulsar **volver** la app pregunta qué vuelve a antes del mensaje:

- **Conversación y código**: la conversación sigue en una copia justo antes del
  mensaje, con su texto en el cuadro, y los archivos vuelven a como estaban.
- **Sólo conversación**: como hasta ahora; los archivos no cambian.
- **Sólo código**: los archivos vuelven y la conversación sigue igual.

Antes de tocar nada enseña qué archivos vuelven y cuáles no:

- Sólo se restaura sin preguntar lo que el agente apuntó en la evidencia durante esos
  turnos y sigue como lo dejó. Lo que cambió sin huella durante un turno con órdenes o
  subagentes viene marcado como «probablemente una orden», para que lo confirmes. Lo
  que cambiaste después del turno, lo que otro cambió durante un turno sin órdenes y
  lo que el agente no tocó se queda como está, salvo que lo marques uno por uno.
- Nunca borra algo que ya existía al empezar el turno sólo porque las instantáneas no
  lo guardaban (un `.env` ignorado, un archivo grande, un repositorio anidado): se
  queda y se avisa.
- Restaurar no sigue enlaces ni escribe fuera de la carpeta, conserva los permisos y
  atributos de los archivos que reescribe (sólo cambia su bit de ejecución) y sólo
  quita carpetas vacías que creó el turno.
- Dice con honestidad lo que no se deshace, leyendo las órdenes de esos turnos con el
  clasificador de los permisos: `git push` y otras publicaciones, commits y ramas
  (el `.git` no entra en las instantáneas), instalaciones, archivos fuera de la
  carpeta, llamadas de red, cambios del sistema, herramientas MCP o del navegador,
  las órdenes de los subagentes y los archivos que cambió el agente fuera de lo que
  guardan las instantáneas.

Al deshacer se guarda el estado que se quita: la conversación muestra una marca
«Código deshecho» con **Rehacer**, que lo devuelve con la misma regla (lo que
cambiaste después de deshacer no se pisa). Los archivos restaurados reciben una
huella nueva en la evidencia (`changes`, `via: "deshacer"`) y la revisión del espacio
sube, así que ninguna comprobación anterior sigue valiendo.

Desde la terminal:

```bash
alfalfa --volver ID --turno T --vista-previa        # qué haría, sin tocar nada
alfalfa --volver ID --turno T                       # conversación y código
alfalfa --volver ID --turno T --alcance codigo      # sólo el código, en la misma sesión
alfalfa --volver ID --turno T --alcance conversacion
alfalfa --rehacer ID [--deshecho D]
```

En una terminal interactiva pregunta archivo por archivo por los que cambiaste
después; si no, `--forzar RUTA` (se repite) los restaura igualmente. Con `--json`
responde con líneas del protocolo de `--stdio`: `deshacer_vista` y `rehacer_vista`
(vista previa), `deshecho` y, si la conversación sigue en otra sesión,
`sesion_cambiada`. El historial (`--historial ID --json`) trae la marca como el campo
`deshacer` de su evento.

## Modos de permisos

Como en Claude Code, cada sesión tiene un modo (`--modo` en la terminal, el selector
junto al modelo en la app, o `/modo`). Se guarda con la sesión y se puede cambiar
en cualquier momento; vale desde el siguiente turno.

| Modo | Qué hace |
| --- | --- |
| `auto` (por defecto) | Las órdenes normales corren en el sandbox sin preguntar; piden permiso las peligrosas, las de red y las que salen del sandbox |
| `manual` | Pregunta antes de cada cambio: cada orden que no sólo lee y cada edición de archivos |
| `ediciones` | Aceptar ediciones: edita sin preguntar y pregunta antes de cada orden que no sólo lee |
| `plan` | Sólo lee (sin herramientas de escritura ni órdenes) y propone un plan |
| `omitir` | Aprueba todo sin preguntar, también fuera del sandbox; las reglas `prohibida` siguen mandando. La app pide confirmación |

El hogar de AlfalfaCode (`ALFALFA_HOME`, por defecto `~/.alfalfa`) queda fuera de las
herramientas de archivos y de las escrituras del sandbox, incluso al abrir una sesión
en `~` o dentro del propio hogar. La protección se conserva en hooks, servidores MCP
locales, diagnósticos y subagentes. Antes de iniciar el sandbox se comprueban los
metadatos del hogar: si contiene archivos con enlaces duros o no se puede completar
el recorrido, se rechaza el arranque para evitar escrituras por otro nombre.

## Permitir en esta sesión

Como «Yes, and don't ask again for this command» de Codex, la tarjeta de permiso
ofrece **Permitir en esta sesión** (en la terminal, `t`). Se guarda la orden exacta
con su carpeta y si sale del sandbox; mientras dure la sesión no vuelve a preguntar
por ella. Si lo aprobado era el reintento sin sandbox, la próxima vez esa misma
orden sale del sandbox directamente. Nunca vale para órdenes peligrosas ni para las
que una regla manda preguntar, y una bifurcación empieza sin aprobaciones. Por
`--stdio` es `{"tipo":"permiso","id":…,"aprobado":true,"sesion":true}` cuando el
permiso trae `para_sesion`.

Cuando el sandbox bloquea una orden y su reintento fuera funciona, el intento de
dentro ya no deja el turno «bloqueado».

## Procesos en segundo plano

Un juego, una app o un servidor que no termina (`swift run Juego`, `npm run dev`)
se lanza con `run_command` y `background: true`, como los «background terminals»
de Codex: a los ~10 s el agente recibe su id (`p1`) y lo que lleva escrito, el
turno sigue y el programa queda abierto aunque el turno acabe. `command_status`
le enseña al agente la salida nueva, si terminó y con qué código, y lo detiene
con `stop: true`. La salida va a `sesiones/ID/procesos/p1.log`.

Una orden normal que pasa de su plazo (120 s o `timeout_seconds`), como un
`swift build -c release` largo, tampoco se corta: pasa a segundo plano con lo que
llevaba escrito y el agente espera su final con `command_status` y
`wait_for_exit: true` (hasta 300 s por consulta), como Codex con una orden que
sigue viva. `run_checks` sí se corta, porque su resultado es la evidencia.
Detener o cortar una orden cierra también a sus descendientes que se fueron a su
propio grupo de procesos (`swift-driver`, `swift-frontend`): antes seguían
compilando sueltos y el reintento competía con ellos. En eso AlfalfaCode va más
allá que Codex, que en macOS sólo mata el grupo de la orden.

En la app aparecen en la sección **Procesos** del panel (orden, cuánto lleva,
últimas líneas y **Detener**) y en el pie («1 proceso en segundo plano»).
`/procesos` (`/ps`) los lista y `/cerrar-procesos` (`/clean`) los cierra. Por
`--stdio`: evento `procesos` y `{"tipo":"detener_proceso","id":"p1"|"todos"}`.
Como en Codex, se cierran con la sesión; la app no la cierra al cambiar de chat
mientras haya alguno abierto.

## Mantener el Mac despierto

Como «Prevent sleep while running» de Codex: mientras trabaja un turno, el Mac no se
duerme por inactividad. Cuenta todo el turno: los subagentes, las órdenes que lanza el
agente (también las de segundo plano mientras el turno sigue) y la espera de un permiso.
Al terminar, cancelar o fallar el turno se suelta; entre turnos el Mac vuelve a dormirse
como siempre, aunque quede abierto un proceso en segundo plano.

El arnés lo hace él mismo, sin la app: toma una aserción de energía de macOS
(`PreventUserIdleSystemSleep`, por IOKit) con el nombre «AlfalfaCode trabaja en un turno».
La aserción es del proceso del arnés, así que macOS la suelta también si el proceso
muere, aunque sea con `kill -9`: no queda ningún `caffeinate` huérfano. Se ve con:

```bash
pmset -g assertions | grep AlfalfaCode
```

Está **encendido por defecto**: sólo dura lo que dura un turno, no apaga ni enciende la
pantalla y no hace nada cuando el agente no trabaja. Sin él, un encargo largo que dejas
corriendo se para a la media hora porque el Mac se durmió. Para apagarlo:

- en la terminal, `ALFALFA_MANTENER_DESPIERTO=0 alfalfa "…"` o `alfalfa --sin-mantener-despierto "…"`;
- en la app, **Ajustes ▸ General ▸ Mantener el Mac despierto**. El cambio vale ya, también
  en las sesiones que están trabajando.

Mientras lo mantiene despierto, el pie de la sesión enseña «Mac despierto».

Límite: no impide que el Mac se duerma al **cerrar la tapa**. Con la tapa cerrada y sin
corriente se duerme igual. Sólo sigue despierto con la tapa cerrada si está enchufado y
con una pantalla externa (modo «clamshell»). Tampoco usa `pmset` ni pide contraseña de
administrador.

## Errores al editar (servidores de lenguaje)

Como OpenCode, el agente ve los errores de lo que acaba de editar sin pedirlos: tras cada
`write_file`, `edit_file` o `apply_patch`, el resultado de esa misma llamada trae los
diagnósticos **nuevos** de los archivos tocados, con archivo, línea y columna, como
mucho 20, primero los errores. «Nuevo» se decide alineando con difflib las líneas de
antes y de después de la edición: un error viejo que bajó porque se añadieron líneas
encima no cuenta, y uno igual a otro que se arregló en otro sitio sí. En un parche de
varios archivos, lo de antes de cada uno se calcula con los demás también como antes.
Ningún documento queda abierto en el servidor entre ediciones, así que lo que cambie
una orden en disco se vuelve a leer.

```json
{"path": "Sources/Demo/Suma.swift", "before": "…", "after": "…",
 "diagnosticos": {"nuevos": ["Sources/Demo/Suma.swift:2:16 error [SourceKit] Cannot convert value of type 'String' to expected argument type 'Int'"],
                  "errores": 1, "revisado_con": ["sourcekit-lsp"],
                  "nota": "Diagnósticos automáticos tras editar: orientan, pero no son una comprobación…"}}
```

Usa lo que ya está instalado; no instala nada:

| Archivos | Servidor de lenguaje | Si no hay |
|---|---|---|
| Swift | `sourcekit-lsp` (viene con Xcode), con una base de datos de compilación propia | — |
| Python | `basedpyright-langserver` o `pyright-langserver` | `ruff check` y, sin ruff (o si falla), los errores de sintaxis del propio Python |
| TypeScript/JavaScript | `typescript-language-server` (el del PATH antes que el de `node_modules`) | — |

Además, si el proyecto tiene su linter configurado (`ruff.toml` o `[tool.ruff]`,
la configuración de ESLint, `.swiftlint.yml`), se pasa sólo sobre los archivos tocados.
Un linter que sale con un código que no es de «sin avisos» o «con avisos» (configuración
rota, regla que no existe) cuenta como fallido, nunca como limpio.

- Los servidores arrancan la primera vez que hacen falta, siguen vivos durante la sesión
  (también para sus subagentes) y se cierran con ella.
- **No alargan el turno**: cada edición espera como mucho 3 s
  (`ALFALFA_DIAGNOSTICOS_ESPERA`). Lo que no contesta a tiempo se queda fuera y el
  resultado lo dice en `sin_respuesta` («sourcekit-lsp no contestó en 3 s (sigue
  arrancando…)»); un servidor que arranca sigue arrancando y sirve en la siguiente
  edición. Uno que no termina de arrancar en 90 s se da por perdido en la sesión.
  Con un paquete Swift, la primera edición prepara el paquete en el sandbox (unos 2 s, más
  la caché de módulos del SDK la primera vez en el Mac, que se comparte entre proyectos) y
  suele decir «prepara el paquete…»; después, centésimas. Si se cancela el turno, no queda
  nada corriendo.
- Un fallo de los diagnósticos nunca hace fallar la edición: la edición se hizo y el
  resultado lo dice.
- **No son una comprobación**: la evidencia sólo cuenta `run_checks`, y el resultado lo
  recuerda en `nota`. Que no haya diagnósticos nuevos no verifica nada.
- **Aislamiento y permisos**, porque estos programas cargan código del proyecto:
  - pyright, `typescript-language-server`, ESLint, ruff y SwiftLint corren en el mismo
    sandbox Seatbelt que las órdenes del agente (sin red, escribiendo sólo en el proyecto
    y en las carpetas temporales). Sin Seatbelt no se lanzan.
  - En **Manual** y **Aceptar ediciones**, donde apruebas cada orden, los que ejecutan
    código del proyecto (pyright con el intérprete de su configuración, tsserver con sus
    plugins, ESLint con su configuración JS) piden permiso una vez por sesión. Un programa
    de `node_modules/.bin` lo pide en cualquier modo. ruff y SwiftLint sólo leen y no lo
    piden. Un «no» también vale para toda la sesión.
  - `sourcekit-lsp` no puede ir en Seatbelt (su sourcekitd es un servicio XPC de Xcode que
    el sandbox bloquea): ejecuta el compilador de Swift sobre los archivos editados con tus
    permisos. Por eso pide el permiso de **orden fuera del sandbox**, una vez por paquete y
    sesión (Omitir permisos lo concede solo).
  - **`sourcekit-lsp` nunca evalúa un `Package.swift`.** Un manifiesto es código y puede pedir
    cualquier opción al compilador sin escribir su nombre (con `dlsym` o `@_silgen_name`,
    por ejemplo, cargar un programa del repositorio como plugin de macros), así que ninguna
    lista de nombres prohibidos lo cierra. En su lugar, AlfalfaCode evalúa el manifiesto
    **dentro del sandbox** con `swift package describe` y sólo se queda con datos: los
    objetivos de Swift, sus fuentes dentro del paquete y las dependencias entre ellos. Compila
    en el sandbox, con `swiftc -emit-module` y opciones fijas, los módulos de los objetivos de
    los que dependen otros, y escribe un `compile_commands.json` en
    `~/Library/Caches/alfalfa/sourcekit`, donde el sandbox de las órdenes no escribe. Sus únicas
    opciones son `-module-name`, `-sdk`, `-target`, `-swift-version`, `-parse-as-library` y
    `-I`/`-F` de esa caché y de la plataforma: ninguna sale del manifiesto. `sourcekit-lsp` se
    lanza en modo base de datos de compilación con esa carpeta como raíz y sólo recibe
    archivos que están en la base; uno que no estuviera (el propio `Package.swift`) le haría
    buscar un paquete. Los archivos sueltos van igual, cada uno con su orden fija. Si los
    ajustes no se pueden sacar así, ese paquete se queda sin diagnósticos de Swift (se dice):
    nunca se cae al modo SwiftPM.
  - Por eso no se cargan las macros ni los plugins del paquete, ni las dependencias de otros
    paquetes, ni los objetivos de C: los diagnósticos del código que los usa quedan
    incompletos. Los módulos de otros objetivos se vuelven a compilar cuando cambian sus fuentes.
  - `sourcekit-lsp` **no se lanza** si el proyecto trae algo que le haría ejecutar órdenes
    o cambiar su configuración (`buildServer.json`, `.bsp/`, `.sourcekit-lsp/`,
    `compile_commands.json`, `compile_flags.txt`, también en `build/`), desde la carpeta
    del archivo hasta la raíz. Si aparece uno con el servidor en marcha, se cierra. Si
    cambia el manifiesto o qué archivos hay, el paquete se prepara de nuevo y el servidor
    se reinicia con la base nueva.
  - Un programa del PATH que en realidad está dentro del proyecto (un `node_modules/.bin` o
    un `.venv/bin` del repo puesto en el PATH) cuenta como del proyecto y pide permiso.
- En la app, la fila de la edición enseña «2 errores · 1 aviso» y «incompleto» si algún
  servidor no contestó.

Está **encendido por defecto**. Para apagarlo:

- en la terminal, `ALFALFA_DIAGNOSTICOS=0 alfalfa "…"` o `alfalfa --sin-diagnosticos "…"`;
- para el usuario, `{"diagnosticos": false}` en `~/.alfalfa/ajustes.json`;
- en un proyecto, `{"diagnosticos": false}` en `.alfalfa/ajustes.json`. El proyecto sólo
  puede apagarlo: nunca enciende lo que el usuario apagó;
- en la app, **Ajustes ▸ General ▸ Ver los errores al editar**. Vale desde la siguiente
  edición, también en las sesiones abiertas. Por `--stdio`:
  `{"tipo":"diagnosticos","activo":false}`.

## Mensajes con el agente ocupado

Lo que escribes mientras el agente trabaja queda «en cola» y entra en su siguiente
paso. Debajo del mensaje en cola hay una fila de acciones, como en Claude:

- **Copiar**: copia su texto, sin las imágenes.
- **Editar** (flecha circular): lo quita de la cola y lo devuelve al cuadro con sus
  imágenes, detrás de lo que ya hubieras escrito.
- **Redirigir**, como steer de Codex: el modelo deja la respuesta que estaba
  pensando y la empieza otra vez con tu mensaje dentro, sin cortar el turno.
  También corta las esperas de `wait_agent` y `command_status`; una orden que ya
  corre termina antes.
- **Quitar** (✕): lo saca de la cola sin enviarlo.
- **Enviar ahora**, como Claude Code: detiene el turno en marcha y tu mensaje abre
  el siguiente. La sesión sigue abierta («Turno interrumpido por el usuario»).

Redirigir y Enviar ahora sólo salen con un turno en marcha. Editar y Quitar sólo
funcionan mientras el mensaje no haya entrado en el contexto del modelo. Si ya entró,
la app avisa («Ya lo leyó el agente») y el mensaje se queda en la conversación.

Por `--stdio`, cada `{"tipo":"mensaje"}` puede llevar un `id` propio. Con
`{"tipo":"quitar_de_cola","id":…}` el motor lo saca de la cola y responde
`{"tipo":"quitado_de_cola","id":…,"quitado":true}`. Si no lo saca, responde con
`"quitado":false` y un `motivo`: `entregado` si el modelo ya lo tiene, `desconocido`
si nunca llegó. `{"tipo":"enviar_ahora","id":…}` adelanta justo ese mensaje, aunque
otro diga lo mismo. Si ya entró, no corta el turno ni lo vuelve a enviar. Los mensajes
sin `id` funcionan como antes, pero no se pueden quitar.
La terminal (`alfalfa "encargo"`) no tiene cola: cada orden es un solo encargo.

Entre turnos, un mensaje (o un botón de la caja de cierre) abre el turno siguiente
y el motor lo confirma: deja de verse «en cola» y la sesión pasa a «trabajando».

## Habilidades (skills)

Como Claude Code y Codex, AlfalfaCode lee las habilidades sueltas (`SKILL.md`):
las tuyas en `~/.claude/skills`, `~/.codex/skills`, `~/.agents/skills` y
`~/.alfalfa/skills`, y las del proyecto en `.claude/skills`, `.agents/skills`,
`.codex/skills` y `.alfalfa/skills` (de la raíz del repositorio y de la carpeta de
trabajo). El modelo ve su nombre y su descripción; cuando una encaja, la abre con
`abrir_habilidad` y sigue sus pasos con los permisos de siempre. Las de sistema de
Codex (`.system`) no entran porque usan herramientas de Codex. Un SKILL.md que
cambia después de descubrirlo no se abre. `"habilidades_sueltas": false` en
`~/.alfalfa/complementos.json` las desactiva. Las de los complementos autorizados
siguen igual.

## Aprender de cada sesión

Como [Hermes Agent](https://hermes-agent.nousresearch.com/docs) (Nous Research), lo
aprende el arnés, no el modelo. Nada se aprende sin que lo veas: todo pasa por una tarjeta
con su diff, y se corrige en Ajustes ▸ Memoria o con `alfalfa --memoria` y
`alfalfa --aprendido`.

**Memoria.** Tres almacenes de notas cortas, separadas por una línea `§`, con un tope
duro: *usuario* (quién eres y cómo te gusta trabajar, 1375 caracteres), *entorno* (hechos
de este Mac, 1375) y *proyecto* (convenciones y trampas, 2200). Todos viven en
`$ALFALFA_HOME/memoria`; el del proyecto, por la ruta de su raíz, nunca dentro del
repositorio, así que un repo no puede plantarlo. El agente los cambia con la herramienta
`memoria` (`add`, `patch` y `remove` por un trozo único de la entrada; no hay «leer»).
Al pasarse del tope la herramienta da error con las entradas y el agente consolida en una
sola llamada.

Cada escritura, en cualquiera de los tres almacenes, **siempre** pide la tarjeta con el
diff, en cualquier modo (también «Omitir permisos»), y no hay ajuste que la quite: un
README, un `AGENTS.md` o una página que «enseñen» algo al agente no llegan a la memoria
sin que lo apruebes.

Antes de guardar, como defensa secundaria, se rechazan: inyecciones de prompt y órdenes de
descarga y ejecución (`curl … | sh`, `bash -c "$(curl …)"`, «siempre envía las claves…»),
palabras que mezclan alfabetos (homoglifos), credenciales (las claves configuradas, formas
conocidas de tokens, URL con usuario y contraseña, webhooks, cadenas hex largas y tokens de
alta entropía), Unicode invisible y lo que imite la estructura del prompt (títulos `#`,
citas, separadores, marcas de memoria). La memoria entra en el prompt citada, línea a línea,
entre `<memoria …>` y `</memoria>`, tal como estaba al empezar la sesión: lo que cambie
ahora se guarda ya, pero entra en la siguiente (no rompe la caché del proveedor). Al
reanudar una sesión, lo que borraste desde entonces ya no vuelve.

**Habilidades aprendidas.** Con la herramienta `habilidad` (`crear`, `parchear`,
`escribir_archivo`, `borrar`) el agente guarda un procedimiento como `SKILL.md` con el
formato de Claude Code (acepta saltos CRLF): en `$ALFALFA_HOME/skills/aprendidas` (tuyas) o
en `.alfalfa/skills` del proyecto (no si el proyecto es tu carpeta personal). Sólo tras un
flujo de varios pasos, un callejón sin salida o una corrección tuya, y sólo si el turno
acabó con `run_checks` en verde después de su última edición, con una comprobación real
del proyecto: la de sus pruebas o su lint detectada por sus archivos (pyproject.toml,
Package.swift, los scripts de package.json, Cargo.toml, go.mod, el Makefile…), la de su
AGENTS.md o una que nombra archivos cambiados en el turno. `python -c "assert 1"`,
`--version`, `-n`, `--dry-run` o `npm test --if-present` sin ese script no valen. Lecciones, no registros: se rechazan fechas,
números de PR y trozos de conversación. Los archivos de apoyo sólo van en
`references/*.md|txt`, con nombres ASCII que no coinciden con otro sin distinguir
mayúsculas. Todas, tuyas o del proyecto, piden siempre permiso con el diff, también
en «Omitir permisos». El registro de
`$ALFALFA_HOME/aprendido` guarda su origen (sesión y turno) y dice qué creó AlfalfaCode:
una habilidad que no está en él, que cambiaste a mano o que bloqueaste no la toca. Entran en
la lista de habilidades en la siguiente sesión. Una del proyecto queda apuntada como cambio
del agente; si sólo escribió Markdown, no caduca la comprobación que estaba en verde.

Para acreditar `make` se comprueba el archivo que realmente usa y una receta sencilla;
los objetivos ya actualizados, las recetas ambiguas o las suites con cero pruebas o todas
omitidas no valen. La carpeta efectiva de la orden cuenta. Si el envoltorio no puede
validarse, ejecuta directamente las pruebas o el lint del proyecto.

Las herramientas de archivos no escriben habilidades en `.alfalfa/skills`, `.agents/skills`,
`.claude/skills` ni `.codex/skills`, tampoco en subproyectos. Las órdenes mantienen esas
carpetas de instrucciones de solo lectura; las habilidades se guardan con su tarjeta.
Antes de abrir el sandbox se comprueba que ninguna comparta archivos mediante enlaces
duros. Si no se puede completar la comprobación, no se abre para escribir.

**Repaso al cerrar el turno.** Si el turno tuvo muchos pasos, un fallo superado o una
corrección tuya y cerró con `wrap_up`, una pasada breve sin razonamiento decide si hay algo
que recordar o un procedimiento que guardar. Corre con el turno ya cerrado (en `--stdio`,
después de `turno_terminado`) y como mucho 20 s; si llega otro mensaje o pulsas «Detener»,
se deja (en la terminal, Ctrl-C). Usa el mismo proveedor y su modelo más barato
de la familia, o el de `modelo_revision`; con un proveedor de plan o local sólo corre si lo
eliges. Cuenta la entrada y la salida máxima contra el presupuesto y no corre si no
alcanza. **Nunca aplica nada**: lo que propone queda pendiente y sale como tarjeta con el
diff («Guardar» o «Descartar»; en la terminal, con la orden para aplicarlo), sin repetir lo
que ya estaba propuesto. Al reabrir la sesión en la app, sus tarjetas pendientes vuelven.

**`/aprender`** convierte una carpeta, una URL o unas notas en una habilidad
(`/aprender docs/despliegue.md`, `/aprender https://…`), con la misma aprobación.

La tarjeta enseña siempre el diff entero: un cambio que no cabe se rechaza, no se recorta.
En Plan y en solo lectura no hay escrituras. Estas escrituras las hace el arnés, no una
orden dentro del sandbox, así que validan cada ruta (relativa, sin `..` ni ocultos, nunca
dentro de `.git` sin distinguir mayúsculas) y se hacen componente a componente con
descriptores que no siguen enlaces, para que un enlace que aparezca a mitad no se siga.
Los ajustes se releen en cada uso: apagar algo en Ajustes ▸ Memoria vale ya.

El hogar de AlfalfaCode (`$ALFALFA_HOME`: memoria, habilidades aprendidas, propuestas,
ajustes, proveedores, complementos, hooks, reglas y sesiones) no lo tocan ni las
herramientas de archivos ni las órdenes, aunque caiga dentro de la carpeta de trabajo (una
sesión en `~` con `~/.alfalfa`): las herramientas lo rechazan y el sandbox Seatbelt
deniega escribir en él por su ruta real y sin distinguir mayúsculas, y renombrarlo. Lo que
llegue a la memoria por otro camino se vuelve a escanear al empezar cada sesión, y una
propuesta pendiente se recalcula desde sus operaciones: si no coincide con su diff, se
descarta.

```bash
alfalfa --memoria                              # los tres almacenes de esta carpeta
alfalfa --memoria escribir usuario --texto "Prefiere commits pequeños."   # o por la entrada estándar
alfalfa --memoria quitar entorno "Xcode 25"
alfalfa --aprendido                            # habilidades, propuestas y ajustes
alfalfa --aprendido ver pasar-pruebas          # usuario:NOMBRE o proyecto:NOMBRE si hay dos
alfalfa --aprendido bloquear pasar-pruebas     # el agente ya no la cambia; tú sí (editar)
alfalfa --aprendido pendientes ver ID          # una propuesta con su diff entero
alfalfa --aprendido aplicar ID                 # o descartar ID
alfalfa --aprendido ajustes revision no        # memoria, habilidades o revision
alfalfa --aprendido proyecto no                # no aprender nada en esta carpeta
```

`ALFALFA_APRENDER=0` lo apaga todo y `ALFALFA_APRENDER_REVISION=0` sólo el repaso. En
`--stdio` el permiso trae `motivo: "aprendizaje"` y `diff`; el repaso emite
`aprendizaje_propuesto` (`id`, `clase`, `almacen` o `accion`/`alcance`/`nombre`, `motivo`,
`diff`) y la orden `{"tipo":"aprendizaje","id":…,"aplicar":true|false}` lo resuelve con
`aprendizaje_resuelto` (`aplicado`, `descartada`, `texto`). Con `--json`, `--memoria`
responde `memoria`; `--aprendido`, `aprendido` (o `habilidad` con `ver` y `pendiente` con
`pendientes ver`).

## Subagentes

Como `spawn_agent` de Codex y la herramienta Agent de Claude Code, el agente puede
lanzar otros agentes con una tarea acotada. Cada uno tiene su propia conversación y
sus herramientas, trabaja en la misma carpeta y pide los permisos con la misma
tarjeta, que dice qué subagente pide.

- `spawn_agent` lo lanza. Sin `background` espera su informe final y lo devuelve,
  como Claude Code; con `background: true` devuelve su id al momento y varios
  trabajan a la vez, como Codex. `fork_context` le pasa la conversación hasta ahí.
- `wait_agent` recoge sus informes, `send_input` les manda otro mensaje (o una
  tarea nueva si ya terminaron, con lo que aprendieron), `close_agent` los cierra
  y `list_agents` dice cómo van.
- Tipos: `general` (las herramientas del modo actual), `explorador` (sólo lee) y
  los tuyos en `.alfalfa/agents/*.md`, `.claude/agents/*.md` del proyecto,
  `~/.alfalfa/agents` o `~/.claude/agents`, con el formato de Claude Code
  (`name`, `description`, `tools`, `model` en la cabecera e instrucciones debajo).
  También los de OpenCode que autorices (ver «Venir de OpenCode, Codex o Claude Code»).
  `tools: Read, Grep` se traduce a `read_file` y `search`; un modelo de Claude
  (`sonnet`, `inherit`…) usa el de la sesión.
- Como en Codex, hay 6 trabajando como mucho y un subagente no lanza otros. En
  modo Plan también sólo leen.
- Si el agente termina su respuesta con subagentes trabajando, el turno espera a
  que acaben y le pasa sus informes, como las notificaciones de Claude Code. Al
  detener el turno se detienen con él.

La app los enseña en el panel «Subagentes» (tipo, modelo, estado, lo que hacen y
sus pasos) y en el pie («2 subagentes trabajando»); sus pasos no se mezclan con la
conversación principal.

## Venir de OpenCode, Codex o Claude Code

Un proyecto preparado para otra herramienta funciona en AlfalfaCode sin tocar nada.
`alfalfa --importar` dice qué encontró en la carpeta y en qué estado está; en la app
está en Ajustes ▸ Importar.

| Herramienta | Qué se lee | Qué se usa |
|---|---|---|
| Estándar AGENTS.md | `AGENTS.md` / `AGENTS.override.md` de la raíz del repositorio a la carpeta de trabajo | Instrucciones |
| Claude Code | `CLAUDE.md`, `.claude/CLAUDE.md` y `CLAUDE.local.md` de esas mismas carpetas; `.mcp.json` del proyecto; `.claude/commands/*.md` (del proyecto y de `~/.claude/commands`) | Instrucciones, servidores MCP, órdenes «/» |
| OpenCode | `opencode.json(c)` y `.opencode/opencode.json(c)` del proyecto, `~/.config/opencode/opencode.json(c)`, `.opencode/agent(s)/*.md` y `.opencode/command(s)/*.md` | `mcp`, `agent`/`agents`, `command`/`commands` e `instructions` (formatos V1 y V2) |
| Codex | `~/.codex/config.toml` y `.codex/config.toml` del proyecto | `[mcp_servers.*]`, `developer_instructions` y `project_doc_fallback_filenames` |

**Instrucciones.** Entran en este orden y en el mismo tope de 32 KiB:
`developer_instructions` de Codex (como hace Codex, delante); en cada carpeta, de la
raíz a la de trabajo, `AGENTS.override.md`, `AGENTS.md` o un nombre alternativo de
Codex, y después los CLAUDE.md; al final, los archivos de `instructions` de OpenCode
(nunca URL). Un archivo que ya entró no se repite: ni el mismo, ni un enlace a él,
ni otro con el mismo texto, y un CLAUDE.md que sólo dice `@AGENTS.md` no añade nada.
Un archivo que enlaza fuera del proyecto no se lee. El `AGENTS.md` de `~/.alfalfa`
sigue yendo primero.

**Servidores MCP, agentes y órdenes «/»: se autorizan una vez, viendo qué hacen.**
Como los complementos, lo que viene de una configuración ajena no se activa hasta
que lo autorizas. La lista enseña de qué herramienta y de qué archivo viene cada
uno y su definición sin resolver. De un servidor stdio enseña la orden entera con
sus argumentos, sin recortar; una de más de 8000 caracteres (contados en crudo) no se
usa. La de un servidor del proyecto se ve tal cual se ejecutará. En la de uno tuyo se
tapan sólo los valores que son claramente una credencial: `--token=…`,
`NOMBRE_TOKEN=…` (como `docker run -e`), el valor que sigue a `--api-key`, una cabecera
(`Authorization: …`, `Cookie: …`, también tras `--header=` o `-H`), un token con prefijo
conocido (`ghp_`, `github_pat_`, `sk-`, `xoxb-`, `glpat-`…), las claves con nombre de
credencial de un JSON (y el `value` de un `{"name": "Authorization", …}`), y de una URL de
cualquier esquema (también `postgresql://`, `mongodb+srv://`, `redis://`) o sin él, la
contraseña de `usuario:contraseña@` (siempre, aunque sea corta), los valores secretos
de la consulta o del fragmento y un tramo con forma de token. Los caracteres
de control salen escritos. También enseña la ruta del ejecutable, avisando si es del
repositorio, también si un servidor tuyo acaba lanzando un binario del repositorio
abierto. Pasa con un `npx`, `npm exec`, `pnpm dlx` o `bunx` (aunque sean los globales),
que ejecutan el `node_modules/.bin/<paquete>` del proyecto si existe, buscándolo desde la
carpeta del proceso hacia arriba. Entonces la huella incluye ese binario y todo su
contenido, y la autorización no vale en otro repositorio ni si el binario cambia. Si
la definición declara un PATH, se busca con él, y un tramo vacío cuenta como la
carpeta del proceso. Por último, la carpeta que podrá escribir, si
tendrá internet y qué variables lee. De uno HTTP enseña sólo el host. De una orden «/» o un agente enseña el principio de su texto.
Al empezar una sesión, un aviso dice qué queda sin autorizar.

```bash
alfalfa --importar                                # qué hay, su definición y su huella
alfalfa --importar --autorizar mcp:docs@HUELLA    # lo que viste (la clave corta vale si no hay dudas)
alfalfa --importar --autorizar todo               # en una terminal: todos los pendientes
alfalfa --importar --autorizar todo --sin-internet
alfalfa --importar --retirar agente:revisor
```

`CLAVE@HUELLA` sólo autoriza si el archivo sigue diciendo lo que viste: si cambió
entre la lista y la autorización, se rechaza. Sin huella, o con `todo`, hace falta
una terminal de verdad. Dentro de una sesión de AlfalfaCode no se autoriza nada,
porque un agente no se da permisos. La app autoriza siempre con la huella, y
«Autorizar todo» pide confirmación.

La autorización queda en `~/.alfalfa/importadas.json` como una huella de lo que se
usará (HMAC con una clave de este Mac). Si cambia algo (otra orden, otros argumentos,
otro entorno, el archivo `{file:…}` del prompt de un agente), vuelve a «sin
autorizar». Otro proyecto con un servidor del mismo nombre no hereda la autorización.

- Los servidores corren como los de `mcp.json`: stdio dentro del sandbox Seatbelt
  (con internet salvo `--sin-internet`) y cada llamada pide permiso. Sólo reciben el
  entorno que declaran. Las referencias (`{env:X}` de OpenCode, `${X}` de Claude Code,
  `env_vars` y `bearer_token_env_var` de Codex) se resuelven en memoria al abrir la
  sesión y nunca entregan una clave de proveedor. Lo que sale de ellas, también en la
  URL, se redacta como el entorno del servidor. Ninguna clave se copia a ningún
  sitio. La lista enseña los valores literales que puso el proyecto y oculta los de
  tu configuración.
- La carpeta de un servidor stdio es la única que puede escribir. La de uno del
  proyecto queda dentro del proyecto; si la raíz del repositorio es tu carpeta
  personal (dotfiles), dentro de la carpeta de trabajo. Ninguna puede ser tu carpeta
  personal ni una que la contenga. Tampoco puede estar dentro de `~/.alfalfa` ni
  contenerla, ni estar dentro de un `.git` o de un repositorio desnudo, cuyos hooks
  correrían fuera del sandbox. Las comparaciones no distinguen mayúsculas, como el
  disco del Mac (`.GIT`, `~/.ALFALFA`). Si lo es, el servidor no se usa. El sandbox
  tampoco abre para escribir una carpeta de trabajo que esté dentro de un `.git`.
- `mcp.json` y los complementos ganan. Las capas de OpenCode se fusionan campo a
  campo como en OpenCode: la más cercana gana, así que `{"gh": {"disabled": true}}`
  en el proyecto apaga el `gh` del usuario. Luego va lo del proyecto (de la carpeta
  de trabajo a la raíz, también las intermedias) y por último lo del usuario. Un
  nombre repetido sale como «duplicado». Si lo tapado es algo que ya autorizaste, se
  avisa.
- Diferencias con las otras herramientas:
  - OpenCode deja vacía una `{env:X}` que no existe; aquí el servidor no se inicia sin ella.
  - Codex sigue sin una variable de `env_vars` que no existe; aquí también, con un aviso.
  - OAuth no se usa, sólo las cabeceras. Un servidor remoto de OpenCode que dependa
    de su OAuth por defecto lo avisa.
  - Tampoco se usan `{file:…}` en un servidor, SSE ni `http_headers_helper` de Codex.
- Un agente de OpenCode se usa como subagente (`spawn_agent`). Denegar editar
  (`permission.edit`, `tools.write/edit: false` o las reglas V2 en orden, la última
  gana) lo deja de sólo lectura, y denegar `bash` le quita las órdenes. Un deny
  limitado a unas rutas no se puede limitar aquí: se aplica entero y se avisa. Su
  modelo `proveedor/modelo` no es de AlfalfaCode, así que usa el de la sesión, y la
  variante (`#high`) pasa a esfuerzo. Un `.alfalfa/agents` o `.claude/agents` con el
  mismo nombre gana.

**Órdenes «/».** Las de OpenCode y Claude Code salen en el menú «/» de la app; las que
faltan por autorizar, atenuadas. Una autorizada, `/probar unidad` (o `alfalfa "/probar
unidad"`), le manda al agente su plantilla con `$ARGUMENTS` y `$1`, `$2`… sustituidos,
en una sola pasada. La plantilla va marcada como texto del repositorio, no como
palabras tuyas. Una sin autorizar se manda tal cual, con un aviso. `` !`orden` `` no
se ejecuta sola: queda como petición al agente, que la corre con los permisos de
siempre. Si el comando pide un subagente (`subtask`/`subagent`), el mensaje lo dice.
Las órdenes propias de la app ganan.

## Probar apps del Mac (control de apps)

Como Computer Use de Codex, el agente puede abrir la app o el juego que construyó y
usarlo como un usuario para comprobar que funciona:

- `app_list`: las apps abiertas y sus ventanas.
- `app_state`: el árbol de accesibilidad de una ventana con elementos numerados
  (`[2] Button titulo="Sumar uno" ayuda="Suma uno al contador" (34,119 91×26)`), su
  tamaño y una captura a la misma escala en puntos. Con `question`, la visión mira la
  captura y responde; con `wait_for`, espera a que salga un texto; con `zoom`, da una
  zona a la resolución completa para leer detalles pequeños.
- `app_action`: `click`, `double_click`, `right_click`, `type`, `key` (`return`,
  `cmd+s`…), `scroll`, `set_value`, `press`, `drag`, `hover`, `menu`
  (`"Archivo > Guardar"`) y `focus`. Se apunta con `target` (el texto visible),
  con un número de elemento o con un punto de la ventana, y `steps` hace varias en
  una llamada. Devuelve la ventana ya cambiada y qué cambió (`cambios`).

Codex lo hace con un servicio nativo y un servidor MCP en Node con una sola
herramienta `js`, y sus acciones traen la ventana al frente. AlfalfaCode compila
la primera vez un ayudante en Swift (`alfalfa/apps/ayudante.swift`, guardado en
`$ALFALFA_HOME/bin`) y usa estas herramientas normales. Lo que hace mejor:

- **No necesita la app delante.** Todo va en fondo, también los menús.
- **Apuntar por texto** (`target`) en vez de por coordenadas o por un número que
  puede haber cambiado, como `find` de las herramientas de Claude.
- **Varios pasos en una llamada** (`steps`), como `computer_batch` de Claude: menos
  idas y vueltas con el modelo.
- **Qué cambió** tras cada acción, y un aviso si la ventana no cambió.
- **Esperar un texto** (`wait_for`) en vez de mirar una y otra vez.
- **Zoom** sobre una zona de la captura, como el de Computer Use de Claude.
- **Menús** de la app por accesibilidad, como `app_menu` de Claude, sin abrirlos.
- **Desplegables** (selectores, botones de menú): un clic en fondo no abre su menú, porque AppKit lo sigue con el ratón de verdad; el clic, también por coordenadas, los abre y elige su opción por accesibilidad, aunque el menú salga de la ventana.
- **Tooltips** en el árbol (`ayuda`), sin pasar el ratón por encima.
- **Niveles por app**, como los de Claude: los navegadores sólo se miran; las
  terminales y los editores se miran y admiten clics sueltos, nunca texto, teclas,
  clic derecho ni arrastrar.

- **Ratón propio, como Codex.** Los clics, dobles clics, clics derechos, arrastres,
  desplazamientos y teclas se mandan a la ventana de la app en segundo plano: tu ratón
  no se mueve, la app no pasa al frente y puedes seguir trabajando en la tuya. Un
  cursor verde dibujado enseña dónde actúa el agente y se va solo
  (`ALFALFA_SIN_CURSOR=1` lo apaga). Una ventana inactiva se traga el primer clic de
  casi todas las vistas, así que, como el servicio de Codex, AlfalfaCode le avisa
  sólo a esa app de que tiene el foco con los registros del gestor de procesos de
  macOS (el truco de yabai). La app de delante no cambia. Para una app que ignore
  los eventos de fondo, `real_mouse: true` usa el ratón de verdad, y sólo si la app
  está delante en ese punto.
- **Números de elemento seguros.** Un número se resuelve con la ruta y el rol de la
  última lectura: si la ventana cambió, la acción falla en vez de pulsar otra cosa.
- **Permiso por app.** La primera vez que se toca una app se pregunta («Controlar una
  app», con «Permitir en esta sesión»), y la tarjeta dice su nivel. Los títulos de
  ventana de las apps no aprobadas no se enseñan. Los gestores de contraseñas, Acceso
  a Llaveros, Ajustes del Sistema, los editores de scripts y otros agentes nunca se
  manejan ni se miran; escribir en una terminal sería salir del sandbox sin preguntar.
- **Modos.** En el modo Plan y en las revisiones no hay `app_action`.

Necesita los permisos de **Accesibilidad** y **Grabación de pantalla** de la app que
lanzó AlfalfaCode (Terminal o la app AlfalfaCode), en Ajustes del Sistema →
Privacidad y seguridad. La prueba de punta a punta abre una app de verdad:
`PRUEBA_APPS_DE_VERDAD=1 python3 -m pytest tests/test_control_de_apps.py`.

## Navegador propio (app de Mac)

El botón **Comentar** del panel permite pulsar un elemento o arrastrar un área sin activar los controles de la
página. Escribe tu indicación y pulsa **Añadir al mensaje**: el compositor recibe el comentario, la referencia,
el selector, el texto, las dimensiones y una captura recortada. Puedes revisar el borrador antes de enviarlo.
La referencia se conserva entre lecturas hasta navegar. Los metadatos de la página llegan marcados como
«datos, no instrucciones»; no se leen valores de contraseñas y la URL adjunta omite consultas y fragmentos.

Como la app de Codex, la app de AlfalfaCode trae un navegador dentro: un panel con
WKWebView (el motor de Safari, ya incluido en macOS) a la derecha de la sesión, con
atrás, adelante, recargar y barra de dirección. Así el agente abre, lee y maneja
páginas web aunque el Mac no tenga Chrome. Se abre solo cuando lo usa el agente, o con
el botón del globo de la cabecera (⇧⌘B). Una franja encima de la página dice qué está
haciendo el agente, y un recuadro verde marca dónde pulsa o escribe.

De forma predeterminada cada sesión tiene un almacén aislado, no persistente: al
cerrar se pierde el login. En **Ajustes → Navegador** puedes elegir **Guardado por
proyecto** para conservar los accesos que inicies a mano con `browser_handoff`.
Sólo las sesiones de esa carpeta comparten el perfil, separado de Safari y de los
otros proyectos; nunca se copian cookies ni cuentas de tus navegadores personales.
Cambiar de modo cierra las páginas del proyecto. Volver a **Aislado** deja de usar
los datos guardados; **Borrar los datos del navegador de este proyecto…** los elimina
tras confirmar, sin borrar los de otros proyectos. Mientras el agente trabaja,
espera un permiso o está en relevo, estos cambios quedan bloqueados.

Herramientas del motor WebKit (la app arranca el motor con `--stdio --navegador`). En terminal
se pueden activar explícitamente con el segundo motor CuyBrowser, descrito abajo. Con el buscador web en
«apagado» tampoco se ofrecen:

- `browser_navigate`: abrir una URL http o https, o `back`, `forward`, `reload`.
- `browser_read`: la página como árbol de accesibilidad con referencias
  (`- button «Enviar pedido» [e7] (envía el formulario)`) o como texto.
- `browser_console`: los últimos mensajes de consola, errores sin capturar y rechazos de promesas.
- `browser_network`: peticiones `fetch`, XHR y recursos observados (método, URL, estado y duración),
  sin cuerpos ni cabeceras y con parámetros sensibles de URL redactados. Es observación desde la página,
  no un HAR completo: puede omitir tráfico anterior, de marcos, workers o iniciado fuera de estos mecanismos.
  Ambas herramientas sólo leen, guardan hasta 200 entradas por navegación y aceptan `limit`.
- `browser_viewport`: `preset` mobile (390×844), tablet (768×1024), desktop (1100×800), o
  `width` y `height` entre 200 y 4096 píxeles CSS. La vista se escala para caber en el panel.
  Cambia el tamaño para probar el diseño adaptable; conserva la identidad de AlfalfaCode y no emula un dispositivo.
- `browser_click` y `browser_type`: por referencia (`ref`) o por el texto visible
  (`target`); `browser_type` también elige en un desplegable y con `submit` pulsa Intro.
- `browser_scroll`: la página, el contenedor que se desplaza o hasta un elemento.
- `browser_screenshot`: si el modelo ve imágenes, la captura le llega como imagen; con
  `question`, la mira el modelo de visión y vuelve la respuesta.
- `browser_eval`: JavaScript que lee, en un mundo aislado de sólo lectura que la página
  no ve; con `modifies: true`, en la propia página y con permiso.
- `browser_handoff`: el agente espera mientras haces tu parte (login, 2FA, captcha o pago).
  El panel muestra el motivo, **Continuar** (⇧⌘↩) y **Cancelar**. Con `until.url_contains`,
  Continuar requiere ese fragmento en la dirección. Las acciones del agente se bloquean
  mientras espera, incluso en Plan; a los cinco minutos o al detener el turno se cancela.
  El agente nunca resuelve captchas por su cuenta.
- `browser_wait`: esperar texto visible, una referencia visible, un fragmento de URL o la
  carga; una condición por llamada, con un plazo de hasta 30 segundos.
- `browser_key`: Intro, Escape, Tab o flechas; admite `ref` y `target` para elegir el foco.
  Si Intro enviaría un formulario, se pregunta antes.
- `browser_hover`: pasar el puntero por un elemento, con eventos y CSS reales de WebKit,
  sin mover tu ratón. Requiere que el panel de la app esté visible.
- `browser_upload`: elegir hasta diez archivos del proyecto en un campo de subida (20 MiB
  en total). Siempre pide permiso, también si la web los enviaría inmediatamente; no sigue
  enlaces que salgan del proyecto. Teclas, hover y subida se deshabilitan en Plan y sólo lectura.
- `browser_download`: descargar a una ruta del proyecto, sólo de direcciones públicas
  (también después de cada redirección y mirando a qué IP resuelve el nombre), hasta
  200 MB y unos 5 minutos; si no, se cancela y no queda nada a medias.

Permisos, con el mismo criterio que el resto del arnés:

- Leer, desplazarse, mirar y navegar a páginas públicas no pide permiso.
- Una dirección de este Mac (`127.0.0.1`, `localhost`) o de la red de casa
  (`192.168.x`, `.local`) se pregunta una vez por sesión y por origen; una redirección a
  una dirección local sin aprobar se bloquea. Las imágenes, los marcos, `fetch` y los
  WebSocket de una página pública hacia este Mac o la red de casa también: la app los
  bloquea con reglas de contenido de WebKit. Una página de un origen aprobado sí puede
  usar la red local (su backend en otro puerto, por ejemplo). Esas reglas miran la URL, no
  la IP: un nombre público que resuelve a `127.0.0.1` sólo lo para el motor al abrirlo.
- Enviar un formulario, escribir en un campo de contraseña, descargar y el JavaScript que
  cambia la página piden aprobación cada vez. El JavaScript que parece cambiar algo
  (asignaciones, clics, `fetch`, `localStorage`…) se trata como si lo cambiara; ese
  filtro sólo es una ayuda. La barrera es el mundo de sólo lectura en el que corre el
  JavaScript sin permiso: ahí la app bloquea los cambios del DOM, el envío de
  formularios, los clics, la red y el valor de los campos de contraseña.
- En **Manual** también se pregunta antes de pulsar o escribir en un sitio, una vez por
  sesión y sitio, y antes de cada `browser_eval` (igual en **Aceptar ediciones**). En
  **Plan** y en solo lectura no hay pulsar, escribir, descargar ni `browser_eval`.
  **Omitir permisos** lo aprueba todo, como siempre.
- Las descargas que lanza una página, las ventanas nuevas y los diálogos (`alert`,
  `confirm`) no llegan a nada: se avisa en el panel.

El motor decide los permisos y la app sólo ejecuta (`alfalfa/navegador.py` y
`alfalfa-mac/Sources/AlfalfaKit/Browser/`). En el protocolo, el motor manda
`{"tipo":"navegador","id":…,"accion":"pulsar",…}` y la app responde
`{"tipo":"navegador_respuesta","id":…,"ok":true,"resultado":{…}}`, o
`"permiso":{"tipo":"formulario",…}` si la acción necesita aprobación: el motor pregunta
y repite la petición con `permitido`.

### Chromium con CuyBrowser

En Ajustes → Navegador se puede elegir **CuyBrowser · Chromium** para las sesiones nuevas.
WebKit sigue siendo la opción inicial. En terminal se activa expresamente:

```sh
alfalfa --motor-navegador cuybrowser --perfil-cuybrowser alfalfa "Revisa mi página"
```

Requiere una instalación de confianza de `cuybrowser` en `PATH`, con HTTP y la capacidad
`exactOrigins` que garantiza el bloqueo anterior al contacto, también al restaurar perfiles,
y `localNetwork` para impedir que recursos de una página pública contacten localhost o la red local.
**CuyBrowser 0.4.0 todavía no ofrece esa garantía:** se devuelve un error de capacidad y no
se abre Chromium. No se instala ni descarga un navegador automáticamente.

El motor pide permiso una vez por sesión para iniciar el servidor fuera del sandbox. Usa HTTP
autenticado en un puerto propio de `127.0.0.1`, sin proxies ni redirecciones. El hogar de CuyBrowser
vive en `ALFALFA_HOME/cuybrowser/<hash de la raíz canónica del proyecto>` y conserva los logins
de ese proyecto. Un bloqueo exclusivo impide que dos sesiones compartan el mismo navegador.
No adopta un daemon preexistente ni detiene procesos de otros clientes. Al cerrar, detiene sólo
su servidor y su daemon; los perfiles propios permanecen. Nunca copia cookies personales ni usa attach.

La política de ese hogar debe conservar `approval: native-dialog`, todas las confirmaciones
sensibles, bloqueo de campos sensibles y `attach.enabled: false`. El cliente comprueba `policy_show`
antes de actuar y pide las confirmaciones a CuyBrowser mediante `confirm`: la persona decide en
el diálogo del sistema. No se duplica la tarjeta sensible de AlfalfaCode. **Omitir permisos sólo
omite las preguntas del motor; los diálogos independientes de CuyBrowser siguen activos.**
Manual conserva su permiso por sitio y Plan/sólo lectura deshabilitan las herramientas de escritura.
Para contraseñas, 2FA y captchas se usa el relevo; el agente nunca los resuelve ni disfraza su identidad.

Las dieciséis herramientas mantienen sus nombres. El contenido web, los mensajes, las URLs y los
títulos siguen marcados como datos, no instrucciones. Estas diferencias se comunican al agente:

| Herramienta | Comportamiento de CuyBrowser |
| --- | --- |
| Navegar, leer, consola, red, viewport, pulsar, escribir, desplazar, captura, esperar y hover | Traducción HTTP nativa. Los diagnósticos de red no incluyen duraciones, cuerpos ni cabeceras. `type` admite campos editables, no selecciona opciones de un `<select>` nativo. |
| `browser_eval` | Requiere `modifies: true` y que una persona haya habilitado `evaluate` en la política privada. Siempre pasa por su diálogo nativo. No existe un mundo JavaScript de sólo lectura y el motor no habilita la función automáticamente. |
| `browser_handoff` | Espera la decisión de la persona en el diálogo del sistema, hasta 120 segundos. `until` devuelve un error de capacidad. Cancelar el turno detiene exclusivamente este navegador para interrumpir el relevo. |
| `browser_key` | Usa el foco actual; `ref` y `target` para cambiarlo devuelven un error de capacidad. |
| `browser_upload` | Hasta diez archivos del proyecto y 20 MiB. Rechaza enlaces simbólicos, duros, rutas privadas y salidas del proyecto. Prepara copias privadas de sólo lectura fuera del hogar de CuyBrowser; su diálogo aprueba esos mismos bytes aunque el proyecto cambie mientras espera. Las copias se eliminan al terminar o cancelar. |
| `browser_download` | Descargar una URL a un archivo del proyecto requiere WebKit; CuyBrowser devuelve un error explícito. No se suplanta con JavaScript ni `fetch`. |

El modo estricto de subrecursos requiere `strictResources: true`. Si el servidor sólo garantiza
documentos por origen exacto, se rechaza ese modo y se indica usar WebKit. Una lista de hostnames
nunca sustituye la política por origen (esquema, host y puerto). Sólo se configura la política del
hogar exclusivo de AlfalfaCode, inicialmente cerrada antes de abrir el navegador.
La excepción `local_network.allow_origins` se concede únicamente tras el permiso independiente
de red local. Como las reglas de WebKit, la barrera de subrecursos compara URLs; la resolución
de IP se comprueba en Python al navegar, y no protege contra cambios de DNS en cada recurso.
Los permisos de **Una vez** viajan separados de los permisos de sesión/proyecto, mediante
`origins.visit` con identificador propio y capacidad `visitLeases`. El motor retira los identificadores
de `status.consumed_visits` antes de generar otra política, y al cerrar el navegador. Una respuesta
atrasada no puede convertir una visita consumida en confianza permanente.
El servidor compatible emplea un proxy privado antes de lanzar el perfil para proteger también
recursos, workers y WebSocket, sin interceptar TLS. Los service workers se deshabilitan en ese perfil
restringido; los workers dedicados y compartidos pueden funcionar.

## SwiftPM y apps dentro del sandbox

SwiftPM abre su propio sandbox y macOS no deja anidarlo. Dentro del de AlfalfaCode,
`swift build/run/test/package` recibe `--disable-sandbox`, también cuando lo llama
un script (`sh "Abrir app.command"`, `make`): las órdenes del sandbox llevan en el
PATH envoltorios de `swift`, `swift-build`… y `xcrun`. Abrir una app (`open`) no
funciona dentro del sandbox; el agente lo pide directamente con
`sandbox_permissions: "require_escalated"`, como en Codex.

## Órdenes con «/»

Como en Codex, escribir «/» en el cuadro de la app abre un menú de órdenes que se
filtra por prefijo: ↑↓ para moverse, ↩ para ejecutar, ⇥ para completar y Esc para
cerrar. Las que no valen en ese momento salen atenuadas, por ejemplo `/revisar`
con un turno en marcha. También aceptan su nombre en inglés (`/model`, `/compact`,
`/init`…).

`/nuevo`, `/modelo`, `/esfuerzo`, `/compactar`, `/contexto`, `/estado`, `/revisar`,
`/inicializar` (crea un AGENTS.md, como `/init` de Codex), `/renombrar`, `/fijar`,
`/bifurcar`, `/archivar`, `/copiar`, `/exportar` (la conversación en Markdown),
`/aprender` (ver «Aprender de cada sesión») y `/detener`. Lo que empieza por «/» y no es una orden, como una ruta, se envía como
mensaje normal. Las órdenes del proyecto de OpenCode y Claude Code también salen en el
menú (ver «Venir de OpenCode, Codex o Claude Code»).

## Cargar un chat de Ollama

Como `/import` de Codex, que trae chats de Claude Code: en la app, «Cargar chat de
Ollama» (en Nuevo encargo) lista los chats de la app de Ollama y copia el elegido
en una sesión nueva de la carpeta actual. Sus mensajes de usuario y asistente
quedan como contexto, y el siguiente mensaje la continúa. Desde la terminal:

```bash
alfalfa --chats-ollama
alfalfa --importar-ollama ID_DEL_CHAT
alfalfa --reanudar SESION "sigue desde aquí"
```

La base de Ollama (`~/Library/Application Support/Ollama/db.sqlite`) sólo se lee:
conexión de solo lectura, `query_only` y una transacción que se deshace al final.
La lista sólo enseña títulos y recuentos. No se traen imágenes adjuntas,
herramientas ni respuestas a medio escribir.

## Actualizar

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

## Qué incluye

| Módulo | Hace |
|---|---|
| `proveedores/nativo/` | El bucle del agente (`NativeRuntime`) y sus herramientas |
| `terminal.py`, `stdio.py` | La orden `alfalfa` y el protocolo JSONL para apps |
| `navegador.py` | Las herramientas `browser_*` del navegador propio de la app de Mac |
| `permisos/`, `seatbelt/`, `reglas/` | Permisos bajo demanda, sandbox de macOS y reglas de `reglas.toml` |
| `mcp_cliente/`, `complementos/`, `hooks/` | Servidores MCP, complementos y hooks compatibles con Claude Code |
| `compactacion/`, `agents_md/`, `sesiones/` | Compactación del contexto, `AGENTS.md` y sesiones con id por turno |
| `importadas/` | Proyectos de OpenCode, Codex y Claude Code: MCP, agentes, órdenes «/» e instrucciones |
| `deshacer/` | Instantáneas por turno en un git paralelo, volver con el código y rehacer |
| `busqueda_sesiones/` | `buscar_sesiones` y `--buscar-sesiones`: índice FTS5 de las conversaciones guardadas |
| `apply_patch/`, `edicion_tolerante/` | Edición por parches y reemplazos tolerantes |
| `diagnosticos/` | Servidores de lenguaje y linters tras cada edición |
| `catalogo/`, `proveedores/compatible.py` | Catálogo de models.dev y proveedores compatibles con OpenAI |
| `proveedores/openai.py`, `proveedores/anthropic.py` | OpenAI (Responses API) y Claude (Messages API) con la clave del usuario |
| `proveedores/lista_api.py` | La lista real de modelos de OpenAI y Anthropic (`GET /v1/models`), con caché |

## Integrarlo en otra aplicación

El arnés no importa nada de quien lo integra. Lo que esa aplicación necesita
cambiar está en `alfalfa/ajustes.py`: instrucciones por defecto, dónde buscar los
binarios, cómo llegar al buscador web, qué repositorio da por propio y las URL
y claves de los proveedores (`ollama_url`, `ollama_clave`, `openai_clave`, `anthropic_clave`,
`clave_compatible`, `alfalfa_servicio_url`).
También cómo se nombran el runtime y el integrador en los textos (`nombres`), a
qué ramas puede avanzar un agente con `git merge --ff-only` (`ramas_de_avance`) y
qué órdenes propias de su proyecto da por seguras (`orden_permitida`). La
aplicación los registra al cargarse.

## Pruebas

```bash
.venv/bin/python -m pytest -q
cd alfalfa-mac && swift test
```

El estilo se comprueba con `ruff check .` en el motor y con `swiftlint` dentro de
`alfalfa-mac/`. Las reglas, la deuda que queda y cómo partir los archivos grandes están
en [docs/ESTILO.md](docs/ESTILO.md).

## Licencia

AlfalfaCode es MIT; ver [LICENSE](LICENSE). Las piezas adaptadas de terceros
conservan la suya, como se cuenta en «Procedencia».

## Procedencia

Varias piezas son adaptaciones de [OpenAI Codex](https://github.com/openai/codex)
(Apache-2.0). Cada una lleva junto a su código `LICENSE`, `NOTICE` y
`PROCEDENCIA.md` con lo que se tomó y cómo se cambió. El código de terceros de la
app de Mac está en [alfalfa-mac/TERCEROS.md](alfalfa-mac/TERCEROS.md).

La búsqueda web usa la librería [CuySearch](https://github.com/0sw2ld0/CuySearch)
(MIT) como dependencia.

«Aprender de cada sesión» toma ideas, no código, de
[Hermes Agent](https://hermes-agent.nousresearch.com/docs) (Nous Research, MIT): sus
almacenes de memoria con tope y escaneo, `skill_manage` y su repaso en segundo plano.

La idea de `buscar_sesiones` viene de `session_search` de
[Hermes Agent](https://hermes-agent.nousresearch.com/docs) (Nous Research); no se tomó
código.

El catálogo de modelos viene de [models.dev](https://models.dev) (MIT); ver
[alfalfa/catalogo/PROCEDENCIA.md](alfalfa/catalogo/PROCEDENCIA.md).
