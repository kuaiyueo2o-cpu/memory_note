from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.services.media_store import blob_storage_enabled

router = APIRouter(prefix="/media", tags=["媒体文件"])

ALLOWED_PUBLIC_PREFIXES = ("photos/", "audio/")


@router.get("/{blob_path:path}")
async def get_media(blob_path: str):
    if not blob_storage_enabled():
        raise HTTPException(status_code=404, detail="媒体文件未启用云存储")

    if not blob_path.startswith(ALLOWED_PUBLIC_PREFIXES):
        raise HTTPException(status_code=403, detail="该媒体文件不可直接访问")

    from vercel.blob import AsyncBlobClient

    client = AsyncBlobClient()
    result = await client.get(blob_path, access="private")
    if result is None or result.status_code != 200 or result.stream is None:
        raise HTTPException(status_code=404, detail="媒体文件不存在")

    return StreamingResponse(
        result.stream,
        media_type=result.blob.content_type or "application/octet-stream",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-cache",
            "ETag": result.blob.etag,
        },
    )
