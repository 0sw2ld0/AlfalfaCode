# PACAI UI Lang

PACAI UI Lang es la forma corta en que un modelo escribe las pantallas del perfil `ui` de PACAI. Va una línea por
pieza, sin las llaves ni las comillas del JSON. El motor de AlfalfaCode la traduce a nodos `pacai.ui/1` antes de
mandarla, así que **las apps no cambian**: la de Mac, la extensión de VS Code y la demo web reciben y dibujan lo
mismo que con el JSON.

Esta página es para quien no la conoce: qué es, cómo se escribe, qué arregla el motor solo y cuánto rinde frente a
OpenUI Lang 1.0 y al JSON de PACAI. La especificación completa del perfil está en
[`perfil-ui.md`](perfil-ui.md) y el código en [`alfalfa/pacai/ui_lang.py`](../../alfalfa/pacai/ui_lang.py).

- [1. Qué es y dónde vive](#1-qué-es-y-dónde-vive)
- [2. Sintaxis](#2-sintaxis)
- [3. Cambios sobre una pantalla](#3-cambios-sobre-una-pantalla)
- [4. Lo que el motor arregla solo y lo que rechaza](#4-lo-que-el-motor-arregla-solo-y-lo-que-rechaza)
- [5. Streaming](#5-streaming)
- [6. Benchmark](#6-benchmark)
- [7. Cómo repetirlo](#7-cómo-repetirlo)
- [8. Lo que no se ha probado](#8-lo-que-no-se-ha-probado)

## 1. Qué es y dónde vive

```text
 modelo ──UI Lang 3 o JSON──▶ motor alfalfa ──PACAI · pacai.ui/1──▶ app (Mac, VS Code, web)
                              traduce, valida,                      dibuja
                              repara lo seguro,
                              pone la revisión
```

- **PACAI** es el protocolo entre el motor y las apps.
- **`pacai.ui/1`** es el formato de las pantallas de su perfil `ui`: una lista de nodos de un catálogo (12
  componentes base más los que declare la app).
- **PACAI UI Lang 3** es otra forma de escribir esos nodos. Sólo existe entre el modelo y el motor: la app nunca
  la ve.

**Cuándo se usa.** El motor la enseña sólo a los modelos con los que se midió y cumple el criterio de adopción
(`MODELOS_MEDIDOS` en `ui_lang.py`): hoy **GPT-6 Luna** y **Claude Haiku 5.5**. Los demás reciben el manual en
JSON con ejemplos. `ALFALFA_PACAI_UI_LANG=1` la fuerza con cualquier modelo y `=0` la apaga con todos. El modelo no
elige el formato: lo decide el motor en cada inferencia.

**Por qué.** Un modelo cobra por cada token que lee y escribe, y el JSON gasta mucho en puntuación. El tablero de
CuyMercado ocupa 146 tokens en UI Lang 3, 193 en OpenUI Lang 1.0 y 308 en JSON. El manual del prompt de sistema
ocupa 1276, 1843 y 2037 tokens.

## 2. Sintaxis

Una pantalla va en un bloque con valla `pacai-ui-lang` y el nombre de la vista:

````text
```pacai-ui-lang view=palta
root Card "Palta Hass" meta="hoy · fruta" > nota,ver
nota Text "Sube un 3 % esta semana."
ver Button "Ver historial" variant=primary @press={"send":"ver_historial"}
```
````

Cada línea es un nodo, en orden de arriba abajo desde `root`:

```text
id Componente ["posicional"] clave=valor … @evento={acción} > hijo,hijo
```

| Pieza | Qué es | Ejemplo |
|---|---|---|
| `id` | En minúsculas, `a-z`, `0-9` y `_`. El primero es `root`. | `c_subieron` |
| `Componente` | Del catálogo (base o de la app). | `Stat` |
| `"posicional"` | Una cadena JSON justo detrás del componente: la prop que declara el catálogo (`Text.text`, `Card.title`, `Button.label`, `Panel.title`…). | `Text "Hola"` |
| `clave=valor` | Valor JSON (`"texto"`, números, `true`, listas, objetos), un enum como palabra suelta, o `$clave` para leer el estado. | `columns=4`, `style=caption`, `query={"tipo":"totales","campo":"subieron"}`, `name=$moneda` |
| `@evento=` | Las acciones del nodo: `send`, `set` u `open_url`. | `@press={"send":"ver_historial"}` |
| `> a,b` | Los hijos, en orden. | `> filtros,cifras` |

**El tablero de CuyMercado**, el que usan las pruebas (`tests/test_pacai_ui_lang.py`):

```text
root Stack direction=vertical gap=medium > filtros,cifras,p_bajo,fuente
filtros Filters
cifras Grid columns=4 > c_subieron,c_bajaron
c_subieron Stat "Subieron" icon=subio query={"tipo":"totales","campo":"subieron"}
c_bajaron Stat "Bajaron" icon=bajo query={"tipo":"totales","campo":"bajaron"}
p_bajo Panel "Lo que más bajó" > bajo_top
bajo_top PriceBars query={"tipo":"productos","orden":"baja","limite":6}
fuente Text "Fuente: MIDAGRI · Gran Mercado Mayorista de Lima." style=caption
```

El motor lo traduce **exacto** al JSON de `pacai.ui/1`, y vuelve
(`test_el_tablero_se_traduce_exacto_al_json_de_hoy_y_vuelve`). Por ejemplo, la línea `c_subieron` queda así:

```json
{"id":"c_subieron","component":"Stat","props":{"label":"Subieron","icon":"subio",
 "period":{"$":"periodo"},"family":{"$":"familia"},"query":{"tipo":"totales","campo":"subieron"}}}
```

**Herencia.** El catálogo puede declarar `inherit`: `Stat`, `Filters`, `PriceBars` y `PriceLines` leen
`period={"$":"periodo"}` y `family={"$":"familia"}` sin que el modelo lo escriba. Un valor explícito gana
(`test_un_valor_explicito_gana_a_la_herencia`).

**Campos y estado.** `$clave` es un enlace al estado de la pantalla
(`test_valores_json_enums_y_enlaces`):

```text
n Input "Cantidad" name=cantidad kind=number min=1 max=500 step=1 required=true placeholder="p. ej. 20"
t Text $moneda style=caption
```

## 3. Cambios sobre una pantalla

Para cambiar una pantalla, el modelo manda otro bloque con el mismo `view=`. Si la vista ya existe, es un cambio
contra la última revisión que el motor le enseñó. **El modelo nunca escribe `rev`**: la pone el motor.

| Línea | Efecto |
|---|---|
| `id Comp …` | Si el id es nuevo, lo añade; si existe, lo **sustituye entero**, con sus hijos tras `>`. |
| `-id` (o `- id`) | Lo borra, lo quita de su padre y se lleva lo que sólo colgaba de él. |

Un hijo que la línea del padre deja fuera también se va, y una línea idéntica a lo que ya hay no cuenta. El cambio
se aplica entero o nada.

```text
p_bajo Panel "Bajas e historial" > bajo_top,bajo_hist
bajo_hist PriceLines query={"tipo":"historial","productos":["palta","limon"]}
```

Esto cambia el título del panel y le añade un gráfico al final
(`test_el_patch_del_tablero_cambia_titulo_y_anade_al_final`). Reordenar y borrar se hace en la misma línea:

```text
root Stack direction=vertical gap=medium > fuente,cifras,filtros,p_bajo
-p_bajo
```

## 4. Lo que el motor arregla solo y lo que rechaza

El motor no adivina. Una línea que no cumple el catálogo se rechaza y se le pide al modelo que la corrija, una sola
vez y con todos los errores juntos. Sólo arregla lo que nunca cambia lo pedido, y lo deja anotado en las
reparaciones:

| Reparación | Ejemplo | Prueba |
|---|---|---|
| **La raíz con otro nombre.** Si la primera línea es un contenedor que no cuelga de nadie, pasa a llamarse `root`. | `raiz Stack … > foto,nota` | `test_una_raiz_con_otro_nombre_se_llama_root` |
| **Un alta sin raíz.** Un `Stack` vertical nuevo envuelve los nodos sueltos, en el orden en que llegaron. | `titulo Text "Vacaciones"` + un `Form` | `test_un_alta_sin_root_se_envuelve_en_un_stack_y_luego_se_puede_cambiar` |
| **Gana la última línea de un id**, también en los cambios. | `-fuente` y luego `fuente Text "otra"` | `test_en_un_cambio_gana_la_ultima_linea_de_un_id` |
| **Borrar un id que no está no hace nada**, salvo dejar un aviso. | `-c_filtros` | `test_una_baja_con_espacio_vale_y_la_de_un_id_que_no_existe_no_hace_nada` |
| **Sin valla.** Una respuesta que son sólo líneas del formato se lee como un bloque (`view=pantalla`). La prosa sigue siendo texto. | el tablero sin ```` ``` ```` | `test_una_pantalla_sin_valla_se_lee_si_todo_son_lineas_del_formato` |
| **Vallas de tildes o de dos puntos** (`~~~`, `:::`). | `~~~pacai-ui-lang view=resumen` | `test_un_bloque_con_valla_de_tildes_o_de_dos_puntos_tambien_vale` |
| **Un `rev` del modelo** se ignora. | `view=v rev=…` | `test_la_revision_la_pone_el_motor_y_un_rev_del_modelo_se_ignora` |
| **Un `>` sin nada detrás** es un nodo sin hijos. Una lista empezada (`> a,`) sigue siendo un error. | `ahora Card "Ahora" >` | `test_un_mayor_que_sin_hijos_es_como_no_ponerlo` |
| **Ids con tildes, eñes o mayúsculas** se leen sin tildes y en minúsculas, en la línea, en los hijos y en las bajas. Dos ids que sólo cambian en eso (también contra los de la pantalla en un cambio) son un error. | `previsión_panel` → `prevision_panel` | `test_ids_con_tildes_o_mayusculas_se_leen_en_ascii_si_son_coherentes` y `test_dos_ids_que_solo_cambian_en_tildes_no_se_juntan` |

Las dos últimas reglas llegaron después del benchmark (véase [perfil-ui.md §10.4](perfil-ui.md)). Al revalidar las
1868 respuestas guardadas de UI Lang 3 recuperan 5 pantallas, todas exactas (Luna pasa de 73 a 77 de 92 en los
briefs y GLM 5.3 Flash de 49 a 50 de 52 en las tareas), y no cambian ninguna de las que ya valían. Las tablas de §6
son las del motor 0.99.55, sin ellas.

Lo que **se rechaza**, entre otras cosas (`test_una_linea_mala_se_descarta_sin_corregirla` y
`test_cambios_que_se_rechazan`):

```text
t Texto "a"                       Texto no está en el catálogo
t Text style=caption "tarde"      el posicional va justo después del componente
t Text "a" > b                    Text no admite hijos
t Text "a" query={"tipo":         JSON mal cerrado
nuevo Text "suelto"               en un cambio: no cuelga de ningún nodo
-root                             root no se borra
```

## 5. Streaming

El motor publica cada nodo en cuanto llegan él y sus ancestros hasta `root`. Así, cada publicación es un árbol
válido que sólo crece. La línea que aún está llegando se publica cerrada (`cerrar_cola` termina el texto y los
corchetes) y se sustituye al completarse
(`test_la_linea_que_aun_llega_se_publica_cerrada_y_luego_se_sustituye`).

## 6. Benchmark

Sigue el modelo del [benchmark de OpenUI](https://www.openui.com/benchmarks)
([`thesysdev/generative-ui-bench`](https://github.com/thesysdev/generative-ui-bench)): validez estructural con
intervalos de Wilson, validez por complejidad, pantallas que se dibujan, tokens, latencia y coste. Le añade lo que
importa en AlfalfaCode: los **cambios** sobre una pantalla y si la pantalla hace **exactamente** lo pedido.

La página con los gráficos es un artifact de Claude, privado hasta que se comparta:
https://claude.ai/artifact/XgVaYRPDWiFdHZzNQLAbBV

### 6.1 Metodología

- **Tres formatos con el mismo catálogo** (12 componentes base y 6 de CuyMercado), el mismo ejemplo por componente
  traducido a cada sintaxis y el mismo validador: todo se convierte a `pacai.ui/1` y pasa el catálogo y el árbol.
  - **PACAI UI Lang 3**, con el prompt que ven hoy Luna y Haiku.
  - **OpenUI Lang 1.0**, según su especificación (`spec/language.md` y `spec/prompt.md`). El lector es nuestro,
    [`scripts/openui_lang.py`](../../scripts/openui_lang.py), con sus códigos de error.
  - **PACAI JSON con ejemplos**, el camino por defecto de los modelos sin UI Lang.
- **Dos conjuntos:**
  - **52 tareas propias:** 32 altas y 20 cambios sobre el tablero de CuyMercado, cada una con su comprobación de
    «exacta».
  - **Los 46 briefs de OpenUI**, sin cambiar el texto y sólo altas. Se leen de un clon de su repositorio y no se
    copian aquí, porque no tienen licencia publicada.
- **Válida** es que el motor la acepte sin errores. En los briefs, además, tiene que tener al menos tantos
  componentes alcanzables desde `root` como requisitos numerados tenga el brief: el «suelo de cobertura» de OpenUI.
- **Condiciones:** temperatura 0, esfuerzo bajo, sin herramientas, en streaming y con una corrección si la primera
  respuesta falla. Las cifras son del primer intento.
- **Coste:** tokens de entrada y salida por el precio del modelo. No se cuenta la caché de prompt, que el servicio no
  informa.
- **Datos:** todas las respuestas están en [`docs/pacai/datos/`](datos/) y las tablas en
  [`datos/formatos-ui/`](datos/formatos-ui/). Las cifras de aquí son esas respuestas **revalidadas con el motor
  0.99.55** (con las reglas de [§10.3 de perfil-ui](perfil-ui.md#103-reglas-para-perder-menos-pantallas-26-de-noviembre-de-2026)).
  Las tandas son del 26-11-2026 y costaron 1,83 US$.

### 6.2 Validez en las 52 tareas

GPT-6 Luna y Claude Haiku 5.5, 4 repeticiones (416 pantallas por formato):

| | PACAI UI Lang 3 | OpenUI Lang 1.0 | PACAI JSON con ejemplos |
|---|---|---|---|
| Válidas al primer intento | 412/416 (99,0 %) | **415/415 (100 %)** | 407/416 (97,8 %) |
| Altas válidas | 256/256 | 256/256 | 256/256 |
| Cambios válidos | 156/160 | **159/159** | 151/160 |
| Exactas (hacen lo pedido) | **410** | 407 | 400 |
| Coste por pantalla exacta | **0,24 $/1000** | 0,31 $/1000 | 0,38 $/1000 |
| Válidas tras una corrección | 416/416 | 415/415 | 416/416 |

Por modelo (UI Lang 3, intervalo de confianza del 95 % de Wilson):

| Modelo | Válidas | IC95 % | Salida (mediana) | Total (mediana) | Coste por exacta |
|---|---|---|---:|---:|---:|
| GPT-6 Luna | 207/208 (99,5 %) | 97,3–99,9 % | 74 | 1,34 s | 0,19 $/1000 |
| Claude Haiku 5.5 | 205/208 (98,6 %) | 95,8–99,5 % | 118 | 1,14 s | 0,29 $/1000 |
| DeepSeek v4.1 Flash | 52/52 (100 %) | 93,1–100 % | 84 | 0,98 s | 0,56 $/1000 |
| GPT-6.2 Sol | 52/52 (100 %) | 93,1–100 % | 65 | 1,96 s | 3,49 $/1000 |
| GLM 5.3 Flash | 49/52 (94,2 %) | 84,4–98,0 % | 72 | 1,37 s | 0,27 $/1000 |

Lo que queda sin valer con Luna y Haiku son 4 pantallas: 3 de Haiku, que crea un panel y no lo cuelga de `root`
(también es un error en el benchmark de OpenUI), y 1 de Luna, que escribe `***` en vez de la valla.

### 6.3 Validez por complejidad (briefs de OpenUI)

Luna y Haiku juntos, 2 repeticiones. Las bandas son las del benchmark de OpenUI, por número de requisitos del brief:

| Banda | Requisitos | PACAI UI Lang 3 | OpenUI Lang 1.0 | PACAI JSON con ejemplos |
|---|---|---|---|---|
| b1 | 2–3 | 37/40 (92,5 %) | **38/40 (95,0 %)** | 37/40 (92,5 %) |
| b2 | 4–6 | 37/40 (92,5 %) | 35/40 (87,5 %) | **39/40 (97,5 %)** |
| b3 | 7–9 | 30/40 (75,0 %) | 31/40 (77,5 %) | **32/40 (80,0 %)** |
| b4 | 11–13 | **23/32 (71,9 %)** | 19/30 (63,3 %) | 17/32 (53,1 %) |
| b5 | 16–18 | 17/32 (53,1 %) | **21/32 (65,6 %)** | **21/32 (65,6 %)** |
| **Total** | | 144/184 (78,3 %) | 144/182 (79,1 %) | **146/184 (79,3 %)** |
| Coste por válida | | **0,74 $/1000** | 0,93 $/1000 | 1,21 $/1000 |

- Los tres formatos empatan en validez (78–79 %) y bajan con la complejidad. UI Lang 3 es el más barato por
  pantalla válida: un 20 % menos que OpenUI y un 39 % menos que el JSON.
- Depende del modelo: con Luna, OpenUI llega al 94,4 % (UI Lang 3 79,3 %); con Haiku, UI Lang 3 saca 77,2 % y
  OpenUI 64,1 %.
- Con 16–18 requisitos, OpenUI publica un 90,6 % con sus modelos, su catálogo de 70 componentes y temperatura 0,7.
  No es comparable con estas cifras.

### 6.4 Pantallas que se dibujan

Con [`scripts/renderizar_ui.py`](../../scripts/renderizar_ui.py), cada respuesta guardada se dibuja con el
renderizador real ([`docs/demos/pacai-ui/ui.js`](../demos/pacai-ui/ui.js), el que copia la extensión de VS Code)
en Chrome sin ventana. Cuenta como dibujada si no lanza ninguna excepción y lo que sale tiene texto o controles
visibles.

- Con UI Lang 3 y el JSON, la app sólo recibe pantallas que el motor acepta. Si las rechaza, pide una corrección.
- Con OpenUI se dibuja lo que lea su lector aunque haya errores, como hace su renderizador.

| Formato (Luna y Haiku, 52 tareas) | Dibujadas al primer intento | En blanco o con excepción | Tras la corrección |
|---|---|---|---|
| PACAI UI Lang 3 | 412/416 (99,0 %) | 0 | **416/416** |
| OpenUI Lang 1.0 | **415/415 (100 %)** | 0 | 415/415 |
| PACAI JSON con ejemplos | 407/416 (97,8 %) | 0 | 416/416 |

| Formato (Luna y Haiku, 46 briefs) | Dibujadas al primer intento | En blanco o con excepción | Tras la corrección |
|---|---|---|---|
| PACAI UI Lang 3 | 144/184 (78,3 %) | 0 | 180/184 (97,8 %) |
| OpenUI Lang 1.0 | **182/182 (100 %)** | 0 | 182/182 |
| PACAI JSON con ejemplos | 146/184 (79,3 %) | 0 | 183/184 (99,5 %) |

- **Todas las pantallas válidas se dibujan**, con los 15 modelos y los tres formatos. Ninguna sale en blanco ni
  rompe el renderizador, y todos sus nodos llegan al DOM. El validador del motor y el renderizador están de acuerdo.
- OpenUI se dibuja al 100 % porque su renderizador tolera errores: dibuja lo que entiende aunque falten
  componentes. Es su «99,9 % que se dibuja» frente a su 96,9 % de validez. PACAI elige no enseñar una pantalla a
  medias y pedir la corrección. Tras ella, UI Lang 3 se dibuja en el 100 % de las tareas y el 97,8 % de los briefs.

### 6.5 Clasificación de modelos

**Qué se mide:** la validez al primer intento con UI Lang 3, frente al coste medio por tarea del primer intento.

- **Validez:** media de las 52 tareas y los 46 briefs (pesan igual).
- **Coste:** también la media de los dos conjuntos.
- **Frontera de Pareto (✓):** ningún otro modelo es a la vez igual o más válido e igual o más barato.

**Tanda del 27-11-2026:** 1 repetición por modelo, temperatura 0, el mismo prompt y el mismo validador. Luna y Haiku
traen las respuestas de §6.2 y §6.3.

| Modelo (empresa, vía) | Validez | 52 tareas | 46 briefs | Coste por tarea | Total (mediana) | Frontera |
|---|---|---|---|---:|---:|:---:|
| Gemini 3.8 Flash, esfuerzo bajo (Google, `agy`) | 100 % | 51/51 | 44/44 | ~6,15 $/1000 | 24,7 s | ✓ |
| GPT-6.2 Sol (OpenAI, Alfalfa) | 100 % | 52/52 | 46/46 | 6,28 $/1000 | 6,0 s | |
| Gemini 4 Argon, esfuerzo bajo (Google, `agy`) | 100 % | 50/50 | 45/45 | ~9,53 $/1000 | 35,9 s | |
| Gemma 4 31B (Google, Ollama Cloud) | 90,2 % | 52/52 | 37/46 | ~0,32 $/1000 | 2,4 s | ✓ |
| Kimi K3.1 Pro (Moonshot, Ollama Cloud) | 90,2 % | 52/52 | 37/46 | ~8,64 $/1000 | 4,9 s | |
| GPT-6 Luna (OpenAI, Alfalfa) | 89,4 % | 207/208 | 73/92 | 0,31 $/1000 | 2,4 s | ✓ |
| DeepSeek v4.1 Flash (DeepSeek, Alfalfa) | 89,1 % | 52/52 | 36/46 | 0,97 $/1000 | 3,0 s | |
| Claude Haiku 5.5 (Anthropic, Alfalfa) | 87,9 % | 205/208 | 71/92 | 0,50 $/1000 | 2,4 s | |
| Beam 1.0 (Ollama Cloud) | 84,8 % | 52/52 | 32/46 | sin precio | 6,7 s | |
| GLM 5.3 (Z.ai, Ollama Cloud) | 84,8 % | 52/52 | 32/46 | ~3,69 $/1000 | 3,1 s | |
| MiMo V2.6 Flash (Xiaomi, Ollama Cloud) | 82,9 % | 50/52 | 32/46 | ~0,31 $/1000 | 3,2 s | ✓ |
| MiMo V2.6 Pro (Xiaomi, Ollama Cloud) | 80,6 % | 51/52 | 29/46 | ~1,09 $/1000 | 3,4 s | |
| GLM 5.3 Flash (Z.ai, Alfalfa) | 73,2 % | 49/52 | 24/46 | 0,39 $/1000 | 3,8 s | |
| MiniMax M3.1 Flash (MiniMax, Ollama Cloud) | 66,3 % | 52/52 | 15/46 | ~0,65 $/1000 | 5,9 s | |
| Gemma 4 E4B, en este Mac (Google, Ollama local) | 30,8 % | 32/52 | 0/46 | 0 | 21,3 s | ✓ |

`~` es un precio de referencia del catálogo: esos modelos se pagan con un plan (Ollama, Gemini), no por token.

**Lectura:**

- **Las 52 tareas propias son fáciles para casi todos:** 14 de los 15 modelos superan el 94 %. Los briefs largos de
  OpenUI son los que separan.
- **Sólo Gemini 3.8 Flash, Gemini 4 Argon y GPT-6.2 Sol sacan válidos todos los briefs,** también en la banda de
  16–18 requisitos. Cuestan unas 20 veces más por tarea que Luna.
- **En la zona barata, a unos 0,31 $/1000 por tarea,** Gemma 4 31B (90,2 %), GPT-6 Luna (89,4 %) y MiMo V2.6
  Flash (82,9 %) están en la frontera.
  - Gemma 4 31B y Kimi K3.1 Pro empatan en validez, pero Kimi cuesta 27 veces más.
  - Gemma 4 31B es la candidata más clara para estudiar si se enciende UI Lang 3 en más modelos. Antes habría que
    medirla con más repeticiones.
- **Dónde fallan:**
  - en los briefs, sobre todo por enums fuera del catálogo y por hijos que no existen;
  - MiniMax M3.1 Flash pierde casi todos los briefs de más de 6 requisitos.
- **Gemma 4 E4B, el modelo pequeño que corre en este Mac,** no aprende la sintaxis. Sangra los hijos en líneas
  aparte en vez de ponerlos tras `>`, y no saca ningún brief. Está en la frontera sólo porque cuesta 0.

**Cómo leerla:**

- **Gemini va por el CLI de Antigravity (`agy -p`),** que es un agente con su propio prompt de sistema, de unos
  19 000 tokens.
  - El prompt de UI Lang va al principio del mensaje y se le pide que no use herramientas.
  - Por eso tarda 20–40 s.
  - Su coste cuenta sólo nuestro prompt, al precio de referencia.
  - 6 de sus 196 peticiones fallaron cuatro veces en el servicio («empty conversation id») y no cuentan; por eso
    su n es menor.
- **Qwen 3.5 ya no está en Ollama:** el servicio responde que se retiró el 25-09-2026.
- **1 repetición por modelo:** con 46 briefs, el intervalo de Wilson de un 80 % va de 66 a 89 %. Las diferencias
  de pocos puntos no son significativas.
- **Coste de la tanda:**
  - 0,54 US$ reales por Alfalfa (los briefs de GLM 5.3 Flash, DeepSeek y Sol);
  - Ollama Cloud y Gemini, contra la cuota de sus planes (1,73 y 1,43 US$ al precio de referencia).

### 6.6 Tokens y velocidad

| Pantalla escrita a mano (`o200k_base`) | PACAI UI Lang 3 | OpenUI Lang 1.0 | PACAI JSON |
|---|---:|---:|---:|
| Tablero de CuyMercado | **146** | 193 | 308 |
| Formulario de contacto | **66** | 73 | 120 |
| Tabla de tickets | **96** | 106 | 108 |
| Tres tarjetas con enlace | **134** | 162 | 222 |
| Cambio: «Iguales» en lugar de «Bajaron» | **40** | 52 | 80 |
| Prompt de sistema | **1276** | 1843 | 2037 |

- **Salida mediana en los briefs:** Luna 450 tokens en UI Lang 3, 562 en OpenUI y 819 en JSON; Haiku 848, 981 y
  1330.
- **Latencia.** El servicio entrega cada respuesta de una vez, así que el tiempo medido hasta la primera pantalla
  es el total. El streaming se estima como en OpenUI: con lo que el motor publicaría a cada 5 % del texto llegado
  y a 50 tokens/s.
  - UI Lang 3 se calcula con el `Flujo` del motor (§5): la línea que aún llega se publica cerrada y, por
    contenedor, sólo el prefijo de hijos que ya llegaron.
  - Hay dos criterios:
    - **el de OpenUI,** `root` con al menos un hijo;
    - **el primer contenido,** el primer nodo que enseña algo. Un `Stack` o un `Grid` vacíos no cuentan.
  - El de OpenUI favorece a un formato que siempre envuelve la pantalla en un `Stack`, como OpenUI. En UI Lang,
    `root` puede ser la tabla o el formulario: la pantalla ya se ve, pero «root con un hijo» no llega nunca.

| Primera pantalla estimada (mediana) | PACAI UI Lang 3 | OpenUI Lang 1.0 | PACAI JSON |
|---|---:|---:|---:|
| 52 tareas · Luna · primer contenido | 0,68 s | **0,55 s** | 1,41 s |
| 52 tareas · Haiku · primer contenido | 1,06 s | **0,98 s** | 2,04 s |
| 46 briefs · Luna · primer contenido | 1,17 s | **1,16 s** | 1,89 s |
| 46 briefs · Haiku · primer contenido | 1,73 s | **1,58 s** | 3,54 s |
| 52 tareas · Luna · criterio de OpenUI | 0,77 s (antes 0,85) | **0,49 s** | 1,50 s |
| 52 tareas · Haiku · criterio de OpenUI | 1,27 s (antes 1,53) | **0,94 s** | 2,19 s |
| 46 briefs · Luna · criterio de OpenUI | **1,05 s** (antes 1,40) | 1,10 s | 1,89 s |
| 46 briefs · Haiku · criterio de OpenUI | 1,73 s (antes 2,12) | **1,58 s** | 3,47 s |

«Antes» es la estimación de §10.2 de perfil-ui, cuando UI Lang 3 sólo publicaba líneas enteras.

- **La línea pendiente cerrada acerca a UI Lang 3 a OpenUI:** recorta 0,1–0,4 s.
- **Con el primer contenido, la diferencia es de 0,01–0,15 s.**
  - En los briefs con Luna hay empate.
  - En las 52 tareas, lo que queda es casi todo la valla: `pacai-ui-lang view=tickets` ocupa 9 tokens y
    `openui-lang` 5, unos 0,08 s a 50 tokens/s. Es el precio de nombrar la vista, que PACAI necesita.
- **El JSON tarda el doble,** porque sólo se lee con la línea entera.
- **Con la respuesta cortada,** al 25 % del texto OpenUI enseña el 32–38 % de los nodos y UI Lang 3 el 0–29 %.
  Al 50 %, 55–63 % frente a 40–53 %. En las tareas, el primer nodo de UI Lang suele ser `root` con su lista de
  hijos, y un hijo sólo se ve cuando llegan los anteriores.

### 6.7 Conclusiones

- **En nuestras tareas, UI Lang 3 hace lo pedido más veces y cuesta menos.** Saca 410 exactas, frente a 407 de
  OpenUI y 400 del JSON. Cuesta un 23 % menos que OpenUI y un 37 % menos que el JSON por pantalla exacta. En
  validez a la primera, OpenUI gana por 3 pantallas de 416.
- **En los briefs de OpenUI, empate en validez** (78–79 %) y UI Lang 3 es el más barato por pantalla válida.
- **Todo lo que valida se dibuja.** El renderizador no encontró pantallas en blanco ni excepciones en 3377
  respuestas de 15 modelos.
- **Dónde gana OpenUI:** en tolerancia, porque dibuja aunque haya errores, y en los briefs largos con Luna. En el
  streaming va por delante por poco: el primer contenido se ve 0,01–0,15 s antes, y casi todo es la valla más corta.

## 7. Cómo repetirlo

Sin gastar, con las respuestas guardadas:

```bash
python scripts/comparar_formatos_ui.py informe --revalidar docs/pacai/datos/formatos-ui-tareas.jsonl docs/pacai/datos/formatos-ui-briefs-openui.jsonl docs/pacai/datos/formatos-ui-otros-modelos.jsonl --csv docs/pacai/datos/formatos-ui
```

```bash
python scripts/renderizar_ui.py docs/pacai/datos/formatos-ui-tareas.jsonl docs/pacai/datos/formatos-ui-briefs-openui.jsonl docs/pacai/datos/formatos-ui-otros-modelos.jsonl --csv docs/pacai/datos/formatos-ui
```

`renderizar_ui.py` necesita Google Chrome (`--chrome RUTA` o la variable `CHROME` si no está en `/Applications`).

Con gasto (antes, pide permiso), un modelo nuevo con UI Lang 3:

```bash
python scripts/comparar_formatos_ui.py medir --proveedor ollama --modelos gemma4:31b-cloud --brazos lang --repeticiones 1 --salida datos.jsonl
```

```bash
python scripts/comparar_formatos_ui.py medir --proveedor ollama --modelos gemma4:31b-cloud --brazos lang --repeticiones 1 --conjunto openui --briefs ../generative-ui-bench/briefs/briefs.ts --salida briefs.jsonl
```

- `--proveedor alfalfa` (por defecto) usa el servicio Alfalfa.
- `--proveedor ollama` usa el Ollama de este equipo: modelos locales o de su nube.
- `--proveedor agy` usa Gemini con el CLI de Antigravity (`agy -p`) y la sesión que ya tenga iniciada. No hay
  claves en el código. Es un agente con su propio prompt de sistema: el nuestro va al principio del mensaje.
- Los modelos que se pagan con un plan se sitúan con un precio de referencia del catálogo, y la clasificación lo
  marca.

## 8. Lo que no se ha probado

- Los modelos exactos del benchmark de OpenUI, su catálogo de 70 componentes, su temperatura de 0,7 y su SDK. El
  lector de OpenUI Lang es nuestro.
- El streaming de verdad: el servicio no trocea, así que la primera pantalla es una estimación a 50 tokens/s sobre
  respuestas guardadas.
- Conversaciones con historial, el prompt real del motor y una sesión en la app: cada tarea va sola.
- La caché de prompt. Con ella la entrada pesa menos y las diferencias de coste se acortan.
- Los 6 componentes de CuyMercado en el renderizador: son de la app, así que en la prueba de dibujo llevan un dibujo
  mínimo. Se mide el árbol, no su aspecto.
- Una tanda nueva con el motor 0.99.55: las cifras son de respuestas guardadas y revalidadas. Otra sesión tiene
  preparada una tanda de confirmación con Luna y Haiku.
