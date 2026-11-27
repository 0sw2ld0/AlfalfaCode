'use strict';
// «Vista enriquecida» (`alfalfa.vistaEnriquecida`): desde 0.99.57 viene apagada en una instalación nueva. Quien ya
// usaba la extensión no la pierde al actualizar: mientras no toque el ajuste, sigue encendida, como la tenía.

/** Clave del estado global: la extensión ya se usaba antes de 0.99.57 (true) o es una instalación nueva (false). */
const CLAVE_HEREDADA = 'alfalfa.vistaEnriquecidaHeredada';

/** Al activar por primera vez con esta versión: ¿ya había estado de la extensión (sesión reciente, modos…)? */
function yaSeUsaba(claves) {
    return claves.some(clave => clave.startsWith('alfalfa.') && clave !== CLAVE_HEREDADA);
}

/**
 * El valor que vale: el que el usuario puso (carpeta, espacio de trabajo o usuario) y, si no puso ninguno, encendida
 * en una instalación de antes de 0.99.57 y apagada en una nueva. `inspeccion` es `getConfiguration().inspect()`.
 */
function vistaEnriquecida(inspeccion, heredada) {
    const puesto = inspeccion?.workspaceFolderValue ?? inspeccion?.workspaceValue ?? inspeccion?.globalValue;
    return typeof puesto === 'boolean' ? puesto : heredada === true;
}

module.exports = { CLAVE_HEREDADA, yaSeUsaba, vistaEnriquecida };
