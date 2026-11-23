'use strict';
// Dónde está `alfalfa`. Un editor abierto desde el Dock no hereda el PATH del shell,
// así que, si el ajuste no lo dice, se prueba en el shell de inicio y en las rutas habituales.
const { execFile } = require('node:child_process');
const { accessSync, constants } = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const HABITUALES = ['/opt/homebrew/bin', '/usr/local/bin', path.join(os.homedir(), '.local/bin')];

function ejecutable(ruta) {
    try {
        accessSync(ruta, constants.X_OK);
        return true;
    } catch {
        return false;
    }
}

function delShell() {
    const shell = process.env.SHELL || '/bin/zsh';
    return new Promise(resolver => {
        execFile(shell, ['-lc', 'command -v alfalfa'], { timeout: 5000 }, (error, salida) => {
            const ruta = String(salida || '').trim().split('\n').pop();
            resolver(!error && ruta.startsWith('/') && ejecutable(ruta) ? ruta : null);
        });
    });
}

/** La ruta de `alfalfa`, o null si no aparece. `ajuste` es el de `alfalfa.ejecutable`. */
async function buscarAlfalfa(ajuste) {
    if (ajuste) {
        const ruta = ajuste.startsWith('~') ? path.join(os.homedir(), ajuste.slice(1)) : ajuste;
        if (!ruta.includes('/')) return ruta;          // un nombre: que lo busque el PATH
        return ejecutable(ruta) ? ruta : null;
    }
    for (const carpeta of (process.env.PATH || '').split(path.delimiter)) {
        if (carpeta && ejecutable(path.join(carpeta, 'alfalfa'))) return path.join(carpeta, 'alfalfa');
    }
    const delInicio = await delShell();
    if (delInicio) return delInicio;
    for (const carpeta of HABITUALES) {
        if (ejecutable(path.join(carpeta, 'alfalfa'))) return path.join(carpeta, 'alfalfa');
    }
    return null;
}

/** El entorno del proceso con las carpetas habituales en el PATH (git, ollama, node… para las herramientas). */
function entorno() {
    const actuales = (process.env.PATH || '').split(path.delimiter).filter(Boolean);
    const extra = HABITUALES.filter(c => !actuales.includes(c));
    return { ...process.env, PATH: [...actuales, ...extra].join(path.delimiter) };
}

module.exports = { buscarAlfalfa, entorno };
