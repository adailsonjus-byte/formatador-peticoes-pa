"""Formatador DOCX sem reescrita de texto; preserva runs e elementos do corpo."""
from copy import deepcopy
from io import BytesIO
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt
from docx.oxml.ns import qn
from docx.oxml import OxmlElement


def format_docx(source: bytes, letterhead: bytes) -> bytes:
    original = Document(BytesIO(source))
    target = Document(BytesIO(letterhead))
    # Remove apenas o conteúdo do corpo do modelo; mantém sectPr, cabeçalhos,
    # rodapés, desenhos de fundo, relações e estilos do papel timbrado.
    body = target.element.body
    for child in list(body):
        if child.tag != qn('w:sectPr'):
            body.remove(child)
    # Copiar parágrafos e tabelas como XML preserva texto, runs, listas e campos.
    # NOTA: imagens, hyperlinks externos, notas, comentários e numeração complexa
    # podem depender de relações não transferidas. Esses casos devem ser sinalizados.
    for child in original.element.body:
        if child.tag == qn('w:sectPr'):
            continue
        body.insert(len(body) - (1 if body.sectPr is not None else 0), deepcopy(child))
    for sec in target.sections:
        sec.page_width, sec.page_height = Cm(21), Cm(29.7)
        sec.top_margin, sec.left_margin = Cm(3), Cm(3)
        sec.bottom_margin, sec.right_margin = Cm(2), Cm(2)
    for p in target.paragraphs:
        pf = p.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(6)
        pf.line_spacing = 1.0
        # Preserve títulos centralizados, citações, listas e estilos especiais.
        style = (p.style.name or '').lower()
        is_special = any(x in style for x in ('heading', 'título', 'title', 'list', 'lista', 'quote', 'cita'))
        is_list = p._p.pPr is not None and p._p.pPr.numPr is not None
        if not is_special and not is_list:
            if p.alignment in (None, WD_ALIGN_PARAGRAPH.JUSTIFY):
                p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                pf.first_line_indent = Cm(1.25)
        for run in p.runs:
            run.font.name = 'Cambria'
            run.font.size = Pt(13)
            rpr = run._r.get_or_add_rPr()
            rfonts = rpr.rFonts
            if rfonts is None:
                rfonts = OxmlElement('w:rFonts')
                rpr.insert(0, rfonts)
            for k in ('ascii','hAnsi','eastAsia','cs'):
                rfonts.set(qn('w:'+k), 'Cambria')
    out = BytesIO()
    target.save(out)
    return out.getvalue()


def inspect_risks(source: bytes) -> list[str]:
    """Conservador: bloquear documentos com relações não suportadas."""
    from zipfile import ZipFile
    risks = []
    with ZipFile(BytesIO(source)) as z:
        names = set(z.namelist())
        if any(n.startswith('word/media/') for n in names): risks.append('imagens incorporadas')
        if any(n.startswith('word/embeddings/') for n in names): risks.append('objetos incorporados')
        if any(n in names for n in ('word/footnotes.xml','word/endnotes.xml')): risks.append('notas de rodapé/fim')
        if 'word/comments.xml' in names: risks.append('comentários')
        if 'word/numbering.xml' in names and b'<w:numPr' in z.read('word/document.xml'): risks.append('numeração/listas complexas')
        rel = z.read('word/_rels/document.xml.rels').decode() if 'word/_rels/document.xml.rels' in names else ''
        if 'hyperlink' in rel: risks.append('hiperlinks')
        if 'header' in rel or 'footer' in rel: risks.append('cabeçalho/rodapé próprios')
    return risks


def pasted_text_to_docx(text: str) -> bytes:
    """Converte texto simples em parágrafos Word, sem reescrever conteúdo."""
    document = Document()
    normalized = text.replace('\r\n', '\n').replace('\r', '\n')
    for line in normalized.split('\n'):
        document.add_paragraph(line)
    out = BytesIO()
    document.save(out)
    return out.getvalue()
