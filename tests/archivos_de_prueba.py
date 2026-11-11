"""PDF, Word y Excel mínimos hechos a mano para las pruebas de adjuntos (sin dependencias)."""
import zipfile


def pdf(ruta, paginas):
    """Un PDF con una página por texto de `paginas` (Helvetica, ASCII)."""
    objetos = ['<< /Type /Catalog /Pages 2 0 R >>', None,
               '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    hijos = []
    for texto in paginas:
        flujo = 'BT /F1 18 Tf 72 720 Td (' + texto.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)') \
            + ') Tj ET'
        objetos.append(f'<< /Length {len(flujo)} >>\nstream\n{flujo}\nendstream')
        objetos.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] '
                       f'/Resources << /Font << /F1 3 0 R >> >> /Contents {len(objetos)} 0 R >>')
        hijos.append(f'{len(objetos)} 0 R')
    objetos[1] = f'<< /Type /Pages /Kids [{" ".join(hijos)}] /Count {len(hijos)} >>'
    salida, posiciones = b'%PDF-1.4\n', []
    for n, objeto in enumerate(objetos, 1):
        posiciones.append(len(salida))
        salida += f'{n} 0 obj\n{objeto}\nendobj\n'.encode('latin-1')
    xref = len(salida)
    salida += f'xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n'.encode()
    salida += b''.join(f'{p:010d} 00000 n \n'.encode() for p in posiciones)
    salida += f'trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    ruta.write_bytes(salida)
    return ruta


def docx(ruta, parrafos):
    w = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    cuerpo = ''.join(f'<w:p><w:r><w:t>{p}</w:t></w:r></w:p>' for p in parrafos)
    with zipfile.ZipFile(ruta, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr('word/document.xml', f'<w:document xmlns:w="{w}"><w:body>{cuerpo}</w:body></w:document>')
    return ruta


def xlsx(ruta, filas, hoja='Datos'):
    s = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    r = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    textos = sorted({c for f in filas for c in f if isinstance(c, str)})
    xml_filas = ''
    for fila in filas:
        celdas = ''.join(f'<c t="s"><v>{textos.index(c)}</v></c>' if isinstance(c, str) else f'<c><v>{c}</v></c>'
                         for c in fila)
        xml_filas += f'<row>{celdas}</row>'
    with zipfile.ZipFile(ruta, 'w') as z:
        z.writestr('xl/workbook.xml', f'<workbook xmlns="{s}" xmlns:r="{r}"><sheets>'
                                      f'<sheet name="{hoja}" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels',
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/sharedStrings.xml', f'<sst xmlns="{s}">' + ''.join(f'<si><t>{t}</t></si>' for t in textos)
                   + '</sst>')
        z.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{s}"><sheetData>{xml_filas}</sheetData></worksheet>')
    return ruta
