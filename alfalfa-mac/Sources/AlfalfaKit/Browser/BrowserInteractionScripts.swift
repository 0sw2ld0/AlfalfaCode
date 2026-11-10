import Foundation

extension BrowserScripts {
    static let documentIdentity = prelude + "return { documento: A.documentID };"

    static let waitCondition = prelude + #"""
    if (args.ref) {
      const el = A.refs.get(args.ref);
      return { cumplida: Boolean(el && el.isConnected && visible(el)) };
    }
    return { cumplida: (document.body?.innerText || '').includes(args.texto || '\u0000') };
    """#

    /// Sólo prepara el foco y detecta envíos: la tecla real la recibe WebKit como NSEvent.
    static let keyTarget = prelude + #"""
    const el = args.ref || args.objetivo ? control(find(args)) : document.activeElement;
    if (!el || !visible(el)) throw new Error('No hay un elemento visible con foco.');
    if (args.ref || args.objetivo) { el.scrollIntoView({ block: 'center' }); el.focus(); }
    // onfocus puede cambiar el elemento activo: los permisos se calculan sobre el foco definitivo.
    const active = document.activeElement;
    if (!active || !visible(active)) throw new Error('No hay un elemento visible con foco.');
    const form = active.form || active.closest('form');
    if (args.tecla === 'Enter' && form && !(args.permitido || []).includes('formulario'))
      return { permiso: { tipo: 'formulario', detalle: describeForm(form) } };
    if (args.tecla === 'Enter' && active.closest('a[download]'))
      throw new Error('Ese enlace descarga un archivo: usa browser_download.');
    return { elemento: describe(el) };
    """#

    static let hoverTarget = prelude + #"""
    const el = find(args);
    el.scrollIntoView({ block: 'center', inline: 'center' });
    const r = el.getBoundingClientRect();
    return { elemento: describe(el), x: r.left + r.width / 2, y: r.top + r.height / 2 };
    """#

    /// El contenido sólo se entrega a la página después de la autorización explícita del motor.
    static let uploadFiles = prelude + #"""
    if (args.documento_aprobado !== A.documentID || args.url_aprobada !== location.href)
      throw new Error('La página cambió durante el permiso de subida; autorízala de nuevo.');
    const el = control(find(args));
    if (!(el instanceof HTMLInputElement) || el.type !== 'file' || el.disabled)
      throw new Error('El elemento no es un campo de subida de archivos habilitado.');
    if (args.archivos.length > 1 && !el.multiple) throw new Error('Ese campo sólo admite un archivo.');
    const transfer = new DataTransfer();
    for (const file of args.archivos) {
      const bytes = Uint8Array.from(atob(file.base64), c => c.charCodeAt(0));
      transfer.items.add(new File([bytes], file.nombre, { type: file.tipo }));
    }
    el.files = transfer.files;
    el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
    el.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
    return { elemento: describe(el), archivos: [...el.files].map(file => ({ nombre: file.name, bytes: file.size })) };
    """#
}
