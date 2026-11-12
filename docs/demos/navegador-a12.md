# Ensayo A12: WebKit y Ollama local

El ensayo compara dos ejecuciones del mismo flujo en un documento ficticio nuevo.
Un helper de depuración muestra un `BrowserEngine` real en su ventana. El harness
usa `Navegador` y su puente JSONL; no abre conversaciones ni proveedores del arnés.
Sólo navega a su servidor HTTP en un puerto efímero propio de `127.0.0.1`.

1. El harness abre el catálogo ficticio y solicita un árbol completo.
2. `gemma4:e4b-u` lee ese resultado y propone la referencia del único botón
   «Crear pedido». Sólo si coincide, el harness envía `browser_click` real.
3. El modelo recibe otra lectura real y confirma el número de pedido y el total.

El caso **full** usa `tree` y un presupuesto de 8000 en ambas lecturas. **diff**
usa `tree` y luego `diff`, con presupuesto 2000. La primera lectura debe llegar
completa en ambos casos. No hay reintentos para forzar éxito: una respuesta
incorrecta queda registrada. La navegación fija la hace el harness, no el modelo.

## Repetir en un Mac de ensayo

Requiere el modelo de chat **local** `gemma4:e4b-u` ya instalado. El harness
comprueba `/api/tags`, rechaza una etiqueta remota y nunca descarga modelos.
Usa un hogar nuevo para compilar y el helper crea otro para su ventana. No firmes
ni notarices el bundle de depuración.

Desde la raíz del worktree, elige una carpeta de ensayo nueva:

```sh
ensayo=/private/tmp/alfalfa-a12-ensayo-nuevo
mkdir -p "$ensayo/build-home/Library/Caches" "$ensayo/build-home/Library/Application Support" "$ensayo/build-tmp"
env HOME="$ensayo/build-home" CFFIXED_USER_HOME="$ensayo/build-home" TMPDIR="$ensayo/build-tmp" \
  .venv/bin/python docs/demos/navegador-a12/compilar.py "$ensayo/LecturasA12Demo.app"
env HOME="$ensayo/build-home" CFFIXED_USER_HOME="$ensayo/build-home" TMPDIR="$ensayo/build-tmp" \
  .venv/bin/python docs/demos/navegador-a12/benchmark.py \
  --puente "$ensayo/LecturasA12Demo.app/Contents/MacOS/LecturasA12Demo" \
  --salida "$ensayo/evidencia"
```

`resultado.json` conserva lecturas, acción y confirmación, `prompt_eval_count`
y `eval_count` de Ollama, tamaños y duraciones. Los mensajes de cada caso parten
del mismo contexto vacío de conversación y usan las mismas opciones. No se vacía
la caché de Ollama ni se cambia su configuración; no se compara rendimiento en
tiempo como si ambos modelos empezaran en frío. Las capturas son del WebKit
ficticio y de la franja de estado del helper. AppKit no dibuja la capa de WebKit
en la captura de ventana; la captura `*-pagina.png` sí muestra el pedido.
El helper y el servidor se cierran al final.

## Resultado de esta rama

Ambos casos eligieron `e1`, pulsaron el botón real y confirmaron el pedido 1
por 20 soles. Ollama reportó los siguientes contadores en cuatro inferencias:

| Medida | Árbol completo | Diff |
| --- | ---: | ---: |
| Entrada, elección del botón (`prompt_eval_count`) | 1879 | 1882 |
| Entrada, confirmación (`prompt_eval_count`) | 3755 | 2262 |
| Entrada total del flujo | 5634 | 4144 |
| Salida total del flujo (`eval_count`) | 445 | 491 |
| Cuerpo de la lectura final (tokens estimados por Alfalfa) | 1615 | 72 |

La confirmación usó un 39,8 % menos de tokens de entrada y el flujo completo
un 26,4 % menos. El presupuesto de 2000 no recortó el árbol inicial, así que
el ahorro observado viene del diff. Son dos ejecuciones de un único modelo y
una página ficticia; el resultado no predice el ahorro en otras páginas ni la
latencia con cachés distintas.
