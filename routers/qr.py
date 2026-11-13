from typing import Annotated

from fastapi import APIRouter, Query, Response

from services.qr import CACHE_SECONDS, MAX_TEXT_LENGTH, render_svg

router = APIRouter(tags=["QR"])


@router.get("/qr.svg")
async def qr(text: Annotated[str, Query(min_length=1, max_length=MAX_TEXT_LENGTH)]):
    return Response(
        render_svg(text),
        media_type="image/svg+xml",
        headers={"Cache-Control": f"public, max-age={CACHE_SECONDS}"},
    )
