from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.services.media_store import blob_storage_enabled, fetch_blob_bytes

router = APIRouter(prefix="/media", tags=["媒体文件"])

ALLOWED_PUBLIC_PREFIXES = ("photos/", "audio/")


@router.get("/{blob_path:path}")
async def get_media(blob_path: str):
    if not blob_storage_enabled():
        raise HTTPException(status_code=404, detail="媒体文件未启用云存储")

    if not blob_path.startswith(ALLOWED_PUBLIC_PREFIXES):
        raise HTTPException(status_code=403, detail="该媒体文件不可直接访问")

    try:
        data, content_type = await fetch_blob_bytes(blob_path)
    except Exception:
        raise HTTPException(status_code=404, detail="媒体文件不存在")

    return StreamingResponse(
        iter([data]),
        media_type=content_type or "application/octet-stream",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-cache",
        },
    )
