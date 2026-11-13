# Catálogo, coste y presupuesto

## Catálogo de modelos (models.dev)

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
[alfalfa/catalogo/PROCEDENCIA.md](../../alfalfa/catalogo/PROCEDENCIA.md).

## Gratis ahora

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

## Coste estimado

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

## Caché de prompts

Anthropic y OpenAI sirven de su caché la parte del prompt que ya vieron, a un
precio menor. Por cada sesión, AlfalfaCode suma los tokens leídos de la caché (y los
escritos, en Anthropic) y compara lo que habrían costado esas peticiones sin caché
con lo que costaron.

- La app lo enseña en el panel de la sesión («Caché: ahorró 0,42 $», con el detalle
  al pasar el ratón) y en `/estado`; la terminal, al acabar cada turno.
- En Anthropic escribir en la caché cuesta 1,25× la entrada: al principio de una
  sesión puede costar más de lo que ahorra, y se dice así («aún cuesta … más»).
- Con Ollama no aplica.

## Presupuesto

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
