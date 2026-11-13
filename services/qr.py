import io

import segno

MAX_TEXT_LENGTH = 300
CACHE_SECONDS = 3600


def render_svg(text: str) -> bytes:
    code = segno.make(text, error="m", micro=False)
    buffer = io.BytesIO()
    code.save(buffer, kind="svg", scale=1, border=2, xmldecl=False, svgns=True, nl=False)
    return buffer.getvalue()
