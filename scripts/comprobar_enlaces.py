"""Comprueba que los enlaces relativos de README.md y docs/ apuntan a archivos que existen.

Uso: python3 scripts/comprobar_enlaces.py [rutas ...]

Sin argumentos revisa README.md y todos los .md de docs/. Los enlaces externos
(http, https, mailto) y los anclas internas (#) se ignoran. Sale con 1 si hay rotos.
"""
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

ENLACE = re.compile(r"\]\(([^()\s]+)\)")

def rutas_a_revisar():
    rutas = []
    if (RAIZ / "README.md").exists():
        rutas.append(RAIZ / "README.md")
    for ruta in sorted((RAIZ / "docs").rglob("*.md")):
        rutas.append(ruta)
    return rutas

def comprobar(ruta):
    rotos = []
    por_linea = ruta.read_text(encoding="utf-8").split("\n")
    en_codigo = False
    for numero, linea in enumerate(por_linea, 1):
        if linea.lstrip().startswith("```"):
            en_codigo = not en_codigo
            continue
        if en_codigo:
            continue
        # El código inline (entre backticks) es ejemplo, no un enlace.
        sin_codigo = re.sub(r"`[^`]*`", "``", linea)
        for destino in ENLACE.findall(sin_codigo):
            if destino.startswith(("http://", "https://", "mailto:", "#")):
                continue
            destino = destino.split("#", 1)[0].split("?", 1)[0]
            if not destino:
                continue
            if destino.startswith("/"):
                destino_abs = RAIZ / destino.lstrip("/")
            else:
                destino_abs = (ruta.parent / destino)
            destino_abs = destino_abs.resolve()
            try:
                destino_abs.relative_to(RAIZ)
            except ValueError:
                # Permitido si la ruta cae fuera al seguir un ../  más de lo normal.
                rotos.append((numero, destino, "sale del repositorio"))
                continue
            if not destino_abs.exists():
                rotos.append((numero, destino, "no existe"))
    return rotos

def main():
    rutas = [Path(x) for x in (sys.argv[1:] or rutas_a_revisar())]
    total_rotos = 0
    revisados = 0
    for ruta in rutas:
        if not ruta.exists():
            continue
        revisados += 1
        rotos = comprobar(ruta)
        total_rotos += len(rotos)
        if rotos:
            print(f"{ruta.relative_to(RAIZ)}:")
            for numero, destino, motivo in rotos:
                print(f"  línea {numero}: {destino} ({motivo})")
    if total_rotos:
        print(f"\n{total_rotos} enlaces rotos.")
        return 1
    print(f"{revisados} archivos revisados, todos los enlaces relativos existen.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
