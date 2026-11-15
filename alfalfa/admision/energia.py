"""Destino de una tarea de Ollama: la nube o este Mac."""

# Un modelo de Ollama Cloud se reconoce por el sufijo de su etiqueta; sin modelo
# o con otro nombre el destino es ambiguo y no se afirma ni cuota ni su ausencia.
_CLOUD_SUFFIXES = (':cloud', '-cloud')


def ollama_destination(task):
    """'cloud', 'local' o None (ambiguo) para una tarea del proveedor ollama."""
    model = task.get('model')
    if not isinstance(model, str) or not model.strip():
        return None
    model = model.strip().lower().removeprefix('ollama/')
    return 'cloud' if model.endswith(_CLOUD_SUFFIXES) else 'local'
