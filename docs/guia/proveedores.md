# Proveedores

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

## Claude (Anthropic)

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
  antes de llegar ahí (ver [Modelos que cobran más por contexto largo](contexto.md)).

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

## Todos los modelos con tu clave (OpenAI y Anthropic)

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

## Proveedores compatibles con OpenAI

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

El catálogo de modelos (models.dev), la lista de **Gratis ahora** y la estimación
de coste, con la caché de prompts y el presupuesto, están en
[Catálogo, coste y presupuesto](catalogo.md).
