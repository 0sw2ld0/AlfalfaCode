"""PACAI 0.2 (Protocol for Agent Communication And Interfaces) en AlfalfaCode: `alfalfa --pacai`.

AlfalfaCode es el agente; el cliente es la app, un widget o un terminal. Este paquete tiene lo mínimo del protocolo
(el sobre y la numeración `seq`, los ids, el esquema y las reglas de sesión) y dos piezas propias:

- `traductor.Traductor` pasa los eventos de `--stdio` v2 a mensajes PACAI (anexo A de la especificación);
- `transporte.TransportePacai` es el `Transport` de v2 hablando PACAI por stdio: saludo, validación de lo que llega,
  cola de entradas, aprobaciones con caducidad y cierre ordenado de cada ejecución.

Lo que no tiene equivalente en el núcleo va como extensión `alfalfa.*`. Material derivado del SDK de PACAI
(Apache-2.0): ver NOTICE y LICENSE en esta carpeta. No depende de ningún repositorio privado.
"""
from .tipos import VERSION

__all__ = ['VERSION']
