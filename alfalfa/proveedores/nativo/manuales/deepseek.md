## Ajustes para DeepSeek

- Incluye siempre `path` en `read_file`, `write_file` y `edit_file`; una llamada
  sin `path` no hace nada.
- Haz una sola edición por llamada y vuelve a leer el archivo entre ediciones
  del mismo archivo para usar el `sha256` nuevo.
- Antes de entregar, pasa las pruebas con `run_checks` y resume su resultado real.
