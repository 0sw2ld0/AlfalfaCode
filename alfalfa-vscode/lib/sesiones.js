'use strict';
// Las sesiones guardadas del motor: las mismas que lista la app de Mac. `alfalfa --sesiones --json` da todas;
// aquí se quedan las de la carpeta abierta. `alfalfa --historial ID --json` da la conversación para pintarla.
const { execFile } = require('node:child_process');
const { realpathSync } = require('node:fs');
const path = require('node:path');

const OBJETIVO_PACAI = 'Sesión PACAI';   // encargo de una sesión `--pacai` que aún no recibió ningún mensaje
const MAX_SESIONES = 50;

function json(ruta, argumentos, env, plazo) {
    return new Promise(resolver => {
        execFile(ruta, argumentos, { env, timeout: plazo, maxBuffer: 64 * 1024 * 1024 }, (error, salida) => {
            if (error) return resolver(null);
            try {
                resolver(JSON.parse(salida));
            } catch {
                resolver(null);
            }
        });
    });
}

function real(ruta) {
    try {
        return realpathSync(ruta);
    } catch {
        return ruta;
    }
}

/** ¿`cwd` es la carpeta o está dentro de ella? Compara también sin enlaces (/tmp y /private/tmp en el Mac). */
function enLaCarpeta(cwd, carpeta) {
    if (typeof cwd !== 'string' || !cwd) return false;
    const dentro = (a, b) => a === b || a.startsWith(b.endsWith(path.sep) ? b : b + path.sep);
    return dentro(cwd, carpeta) || dentro(real(cwd), real(carpeta));
}

/** Las filas de `--sesiones` de esta carpeta, de la más reciente a la más antigua, sin las que no tienen nada. */
function sesionesDeLaCarpeta(filas, carpeta) {
    return (Array.isArray(filas) ? filas : [])
        .filter(s => s && typeof s.id === 'string' && enLaCarpeta(s.cwd, carpeta))
        .filter(s => (s.turnos || 0) > 0 || (s.encargo && s.encargo !== OBJETIVO_PACAI))
        .sort((a, b) => (b.fecha || 0) - (a.fecha || 0))
        .slice(0, MAX_SESIONES);
}

/** Las sesiones guardadas de `carpeta`, o null si `alfalfa` no las pudo listar. */
async function listarSesiones(ruta, carpeta, env) {
    const datos = await json(ruta, ['--sesiones', '--json'], env, 20000);
    return datos && Array.isArray(datos.sesiones) ? sesionesDeLaCarpeta(datos.sesiones, carpeta) : null;
}

/** `{eventos, recortado}` de una sesión, o null si no se pudo leer. */
async function leerHistorial(ruta, id, env) {
    const datos = await json(ruta, ['--historial', id, '--json'], env, 20000);
    return datos && Array.isArray(datos.eventos) ? { eventos: datos.eventos, recortado: Boolean(datos.recortado) } : null;
}

module.exports = { listarSesiones, leerHistorial, sesionesDeLaCarpeta, enLaCarpeta, OBJETIVO_PACAI };
