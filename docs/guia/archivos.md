# Archivos: PDF, texto, código, Word y Excel

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
marcado como datos y no instrucciones, con los secretos del proyecto tapados, y desde
entonces lo arriesgado pide una aprobación extra (ver «Contenido externo» en
[docs/USO.md](../USO.md)), como tras un `read_web`. Si el texto
de un PDF contiene un secreto, no se manda como documento (no se podría tapar dentro):
va su texto con el secreto tapado. Lo que pesa cuenta para el presupuesto y la
compactación (unos 2 000 tokens por página de PDF como documento); con más de 50 000
tokens o 100 páginas se avisa, y el texto de un archivo se recorta a 400 000 caracteres.
