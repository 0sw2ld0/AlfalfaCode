// El renderizador de pantallas (ui.js) es un módulo: lo deja a mano del panel (chat.js), que es un script normal.
import { Vista } from './ui.js';

window.PantallasPacai = { Vista };
window.dispatchEvent(new Event('pantallas-listas'));
