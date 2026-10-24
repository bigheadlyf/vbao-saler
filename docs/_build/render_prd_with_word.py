"""Use canonical DOCX rasterizer with PDF exported by native Word on Windows.

The runtime has no bundled LibreOffice on this host. The only conversion
replacement is an already generated native Word PDF; rasterization remains the
packaged render_docx.py implementation and bundled Poppler.
"""
from pathlib import Path
import importlib.util
import os
import sys
import tempfile
sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'docs' / '_build'
RUNTIME = Path(r'C:\Users\bighe\.cache\codex-runtimes\codex-primary-runtime\dependencies')
SCRIPT = Path(r'C:\Users\bighe\.codex\plugins\cache\openai-primary-runtime\documents\26.1022.11436\skills\documents\render_docx.py')
PDF = WORK / 'prd_preview.pdf'
DOCX = ROOT / 'docs' / '出海企业商机发现Agent项目开发需求文档_v1.0.docx'
OUT = WORK / 'render'
(WORK / 'tmp').mkdir(exist_ok=True)
tempfile.tempdir = str(WORK / 'tmp')
os.environ['PATH'] = str(RUNTIME / 'native' / 'poppler' / 'Library' / 'bin') + os.pathsep + os.environ.get('PATH', '')
spec = importlib.util.spec_from_file_location('canonical_docx_renderer', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
def converted_pdf(*args, **kwargs):
    if not PDF.is_file(): raise FileNotFoundError(PDF)
    return str(PDF), 'PDF exported from this DOCX by native Microsoft Word'
module.convert_to_pdf = converted_pdf
sys.argv = [str(SCRIPT), str(DOCX), '--output_dir', str(OUT), '--dpi', '125']
module.main()

from pypdf import PdfReader
from PIL import Image, ImageOps, ImageDraw
reader = PdfReader(str(PDF))
print('Pages:', len(reader.pages))
for i, page in enumerate(reader.pages, 1):
    txt = page.extract_text() or ''
    print(f'{i}: chars={len(txt)} start={txt[:45]!r} end={txt[-65:]!r}')
paths = [OUT / f'page-{n}.png' for n in range(1, len(reader.pages)+1)]
for offset in range(0, len(paths), 6):
    group = paths[offset:offset+6]
    sheet = Image.new('RGB', (1200, 1160), '#e8e8e8')
    d = ImageDraw.Draw(sheet)
    for j, path in enumerate(group):
        im = Image.open(path).convert('RGB')
        im.thumbnail((380, 540))
        x = (j % 3) * 400 + 10
        y = (j // 3) * 580 + 25
        sheet.paste(im, (x, y))
        d.text((x, y-18), path.stem, fill='black')
    sheet.save(WORK / f'overview-{offset//6+1}.png')
