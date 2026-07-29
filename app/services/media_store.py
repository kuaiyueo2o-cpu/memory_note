import os
import tempfile
from typing import Optional

import httpx

MEDIA_PROXY_PREFIX = "/media/"


def blob_storage_enabled() -> bool:
    return bool(
        os.environ.get("BLOB_READ_WRITE_TOKEN")
        or os.environ.get("VERCEL_OIDC_TOKEN")
        or os.environ.get("VERCEL")
        or os.environ.get("VERCEL_ENV")
    )


def blob_runtime_config() -> tuple[str, str]:
    store_id = os.environ.get("BLOB_STORE_ID", "").strip()
    token = (
        os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip()
        or os.environ.get("VERCEL_BLOB_READ_WRITE_TOKEN", "").strip()
    )
    if not store_id or not token:
        raise RuntimeError("Blob 配置缺失")
    return store_id, token


async def fetch_blob_bytes(pathname: str) -> tuple[bytes, str]:
    store_id, token = blob_runtime_config()
    blob_url = f"https://{store_id}.private.blob.vercel-storage.com/{pathname}"
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.get(blob_url, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        return resp.content, resp.headers.get("content-type", "application/octet-stream")


async def save_media_bytes(
    *,
    pathname: str,
    body: bytes,
    content_type: Optional[str],
    local_dir: str,
    local_filename: str,
    local_url: str,
    access: str = "private",
    expose_via_app: bool = False,
) -> str:
    """优先存到 Vercel Blob；本地开发环境退回静态目录。"""
    if blob_storage_enabled():
        from vercel.blob import AsyncBlobClient

        client = AsyncBlobClient()
        blob = await client.put(
            pathname,
            body,
            access=access,
            content_type=content_type,
            add_random_suffix=False,
        )
        return f"{MEDIA_PROXY_PREFIX}{blob.pathname}" if expose_via_app else blob.pathname

    os.makedirs(local_dir, exist_ok=True)
    local_path = os.path.join(local_dir, local_filename)
    with open(local_path, "wb") as f:
        f.write(body)
    return local_url


async def materialize_path_to_temp(path_or_url: str, suffix: str = "") -> str:
    """把远程 URL 或本地相对路径转成一个可读取的临时文件路径。"""
    if path_or_url.startswith(MEDIA_PROXY_PREFIX) or (
        "/" not in path_or_url and path_or_url.startswith(("photos", "audio", "voices"))
    ):
        blob_path = (
            path_or_url[len(MEDIA_PROXY_PREFIX):]
            if path_or_url.startswith(MEDIA_PROXY_PREFIX)
            else path_or_url
        )
        data, _ = await fetch_blob_bytes(blob_path)
    elif path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(path_or_url)
            resp.raise_for_status()
            data = resp.content
    else:
        app_dir = os.path.dirname(os.path.dirname(__file__))
        relative_path = path_or_url.lstrip("/")
        local_path = os.path.join(app_dir, relative_path)
        with open(local_path, "rb") as f:
            data = f.read()

    fd, temp_path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as temp_file:
        temp_file.write(data)
    return temp_path
