#!/bin/bash
# Demo de la alfalfa que crece con el contexto. Desde la raíz del repositorio, después de
# `(cd alfalfa-mac && swift build)`:
#   docs/demos/alfalfa-crece/demo.sh
# Compila Demo.swift con AlfalfaKit y las vistas de la planta de este worktree y lo ejecuta con
# HOME, CFFIXED_USER_HOME y ALFALFA_HOME en una carpeta temporal: no lee sesiones ni ajustes reales.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
DEMO="$ROOT/docs/demos/alfalfa-crece"
BUILD="$ROOT/alfalfa-mac/.build/arm64-apple-macosx/debug"
SOURCES="$ROOT/alfalfa-mac/Sources/AlfalfaCode"
TEMP="$(mktemp -d)"
trap 'rm -rf "$TEMP"' EXIT
mkdir -p "$TEMP/hogar" "$TEMP/alfalfa"
swiftc -parse-as-library -I "$BUILD/Modules" "$DEMO/Demo.swift" "$SOURCES/Theme.swift" \
    "$SOURCES/Session/ActivityIndicator.swift" "$SOURCES/Session/AlfalfaPlantDrawing.swift" \
    "$BUILD"/AlfalfaKit.build/*.swift.o -o "$TEMP/AlfalfaCrece"
env -i PATH="/usr/bin:/bin" HOME="$TEMP/hogar" CFFIXED_USER_HOME="$TEMP/hogar" ALFALFA_HOME="$TEMP/alfalfa" \
    "$TEMP/AlfalfaCrece" "$DEMO"
# Nada se escribió en el hogar del ensayo: la demo no toca ajustes ni sesiones.
test -z "$(ls -A "$TEMP/alfalfa")"
