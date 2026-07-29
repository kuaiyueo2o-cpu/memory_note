import os
import uuid
import shutil
import logging
import asyncio
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from app.models.database import get_db, SessionLocal
from app.models.models import FamilyMember

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/members", tags=["家庭成员管理"])

# ★ 关键修复：上传目录必须与 main.py 中 StaticFiles 挂载的目录一致
APP_DIR = os.path.dirname(os.path.dirname(__file__))  # .../app
STATIC_DIR = os.path.join(APP_DIR, "static")
UPLOAD_DIR = os.path.join(STATIC_DIR, "uploads")
PHOTO_DIR = os.path.join(UPLOAD_DIR, "photos")
VOICE_DIR = os.path.join(UPLOAD_DIR, "voices")

os.makedirs(PHOTO_DIR, exist_ok=True)
os.makedirs(VOICE_DIR, exist_ok=True)

# 预设音色池（仅在声音克隆失败时作为兜底）
VOICE_POOL = [
    "Chinese (Mandarin)_Lyrical_Voice",
    "Chinese (Mandarin)_HK_Flight_Attendant",
    "English_Graceful_Lady",
    "English_Insightful_Speaker",
    "English_radiant_girl",
    "English_Persuasive_Man",
]


def _safe_filename(prefix: int | str, original: str, default_ext: str) -> str:
    """生成安全的 ASCII 文件名，避免中文/特殊字符导致的 URL 编码与静态服务问题。"""
    ext = os.path.splitext(original or "")[1].lower() or default_ext
    if not ext.startswith("."):
        ext = "." + ext
    return f"{prefix}_{uuid.uuid4().hex[:8]}{ext}"


def _assign_voice_fallback(member: FamilyMember) -> str:
    """兜底：当真正的声音克隆失败时，分配预设音色。"""
    seed = member.id if member.id else (hash(member.name) & 0xffff)
    return VOICE_POOL[seed % len(VOICE_POOL)]


async def _do_voice_clone(member_id: int, voice_file_path: str):
    """后台任务：调用 MiniMax 声音克隆 API，成功后更新 voice_clone_id。"""
    from app.services.pipeline import clone_voice_minimax

    result = await clone_voice_minimax(voice_file_path)

    db = SessionLocal()
    try:
        member = db.query(FamilyMember).filter(FamilyMember.id == member_id).first()
        if not member:
            return
        if result and result.startswith("CLONE_FAIL:"):
            # 克隆失败，保存失败原因，前端可展示
            member.voice_clone_id = result
            logger.warning(f"⚠️ 成员 {member.name}(id={member_id}) 声音克隆失败: {result}")
        elif result:
            # 克隆成功
            member.voice_clone_id = result
            logger.info(f"✅ 成员 {member.name}(id={member_id}) 声音克隆成功: {result}")
        else:
            # 未知错误：保留真实状态，不伪造客服或收费信息。
            member.voice_clone_id = "CLONE_FAIL:音色复刻未完成，请检查 API Key、实名认证和录音格式"
            logger.warning(f"⚠️ 成员 {member.name}(id={member_id}) 声音克隆失败（未知原因）")
        db.commit()
    finally:
        db.close()


def _save_upload(upload: UploadFile, target_dir: str, filename: str) -> None:
    path = os.path.join(target_dir, filename)
    with open(path, "wb") as f:
        shutil.copyfileobj(upload.file, f)


@router.get("")
async def get_members(db: Session = Depends(get_db)):
    """获取所有家庭成员"""
    members = db.query(FamilyMember).all()
    result = []
    for m in members:
        # 解析声音克隆状态
        clone_id = m.voice_clone_id
        if clone_id and clone_id.startswith("CLONE_FAIL:"):
            voice_status = "failed"
            voice_error = clone_id[len("CLONE_FAIL:"):]
            voice_cloned = False
            effective_voice_id = None
        elif clone_id == "cloning...":
            voice_status = "cloning"
            voice_error = None
            voice_cloned = False
            effective_voice_id = None
        elif clone_id:
            voice_status = "cloned"
            voice_error = None
            voice_cloned = True
            effective_voice_id = clone_id
        else:
            voice_status = "none"
            voice_error = None
            voice_cloned = False
            effective_voice_id = None

        result.append({
            "id": m.id,
            "name": m.name,
            "relation": m.relation,
            "photo_path": m.photo_path,
            "voice_clone_id": effective_voice_id,
            "memory_snippets": m.memory_snippets,
            "favorite_food": m.favorite_food,
            "favorite_color": m.favorite_color,
            "personality": m.personality,
            "catchphrase": m.catchphrase,
            "special_memory": m.special_memory,
            "has_voice": m.voice_sample_path is not None,
            "voice_cloned": voice_cloned,
            "voice_status": voice_status,
            "voice_error": voice_error,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        })
    return {"members": result}


@router.post("")
async def add_member(
    name: str = Form(...),
    relation: str = Form(...),
    favorite_food: str = Form(...),
    favorite_color: str = Form(...),
    personality: str = Form(...),
    catchphrase: str = Form(""),
    special_memory: str = Form(...),
    memory_snippets: str = Form(""),
    photo: UploadFile = File(None),
    voice_sample: UploadFile = File(None),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
):
    """添加家庭成员"""
    # 强制必填校验（姓名、关系、最爱吃、最喜欢颜色、性格、最难忘的事）
    required = {
        "姓名": name, "关系": relation, "最爱吃什么": favorite_food,
        "最喜欢的颜色": favorite_color, "性格": personality, "最难忘的事": special_memory,
    }
    missing = [k for k, v in required.items() if not (v and v.strip())]
    if missing:
        raise HTTPException(status_code=422, detail=f"请填写必填项：{ '、'.join(missing) }")

    member = FamilyMember(
        name=name,
        relation=relation,
        favorite_food=favorite_food,
        favorite_color=favorite_color,
        personality=personality,
        catchphrase=catchphrase,
        special_memory=special_memory,
        memory_snippets=memory_snippets,
    )
    # 先入库拿到 id，便于用 id 命名文件与分配音色
    db.add(member)
    db.commit()
    db.refresh(member)

    # 保存照片（安全文件名）
    if photo and photo.filename:
        photo_filename = _safe_filename(member.id, photo.filename, ".jpg")
        _save_upload(photo, PHOTO_DIR, photo_filename)
        member.photo_path = f"/static/uploads/photos/{photo_filename}"

    # 保存声音样本，触发真正的声音克隆
    voice_cloning = False
    if voice_sample and voice_sample.filename:
        voice_filename = _safe_filename(member.id, voice_sample.filename, ".mp3")
        _save_upload(voice_sample, VOICE_DIR, voice_filename)
        member.voice_sample_path = f"/static/uploads/voices/{voice_filename}"
        # 先设为"克隆中"状态（用特殊标记），后台异步完成真正的克隆
        member.voice_clone_id = "cloning..."
        voice_cloning = True

    db.commit()
    db.refresh(member)

    # 后台异步执行声音克隆
    if voice_cloning:
        voice_file_path = os.path.join(VOICE_DIR, voice_filename)
        asyncio.create_task(_do_voice_clone(member.id, voice_file_path))

    return {
        "id": member.id,
        "name": member.name,
        "voice_clone_id": member.voice_clone_id,
        "voice_cloned": member.voice_clone_id is not None and member.voice_clone_id != "cloning...",
        "voice_cloning": voice_cloning,
    }


@router.post("/{member_id}/reclone")
async def reclone_voice(member_id: int, db: Session = Depends(get_db)):
    """对已有声音样本的成员重新触发声音克隆（用于旧数据升级为真正克隆）"""
    member = db.query(FamilyMember).filter(FamilyMember.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="成员不存在")
    if not member.voice_sample_path:
        raise HTTPException(status_code=400, detail="该成员未上传声音样本")

    # 找到声音文件的本地路径
    # voice_sample_path 格式: /static/uploads/voices/xxx.mp3
    relative_path = member.voice_sample_path.lstrip("/")  # static/uploads/voices/xxx.mp3
    voice_file_path = os.path.join(APP_DIR, relative_path)

    if not os.path.exists(voice_file_path):
        raise HTTPException(status_code=404, detail="声音文件不存在，请重新上传")

    # 标记为克隆中
    member.voice_clone_id = "cloning..."
    db.commit()

    # 后台异步执行声音克隆
    asyncio.create_task(_do_voice_clone(member.id, voice_file_path))

    return {
        "success": True,
        "message": f"已为「{member.name}」触发声音克隆，稍后刷新查看结果",
        "voice_cloning": True,
    }


@router.delete("/{member_id}")
async def delete_member(member_id: int, db: Session = Depends(get_db)):
    """删除家庭成员"""
    member = db.query(FamilyMember).filter(FamilyMember.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="成员不存在")
    db.delete(member)
    db.commit()
    return {"success": True}


@router.put("/{member_id}")
async def update_member(
    member_id: int,
    name: str = Form(None),
    relation: str = Form(None),
    favorite_food: str = Form(None),
    favorite_color: str = Form(None),
    personality: str = Form(None),
    catchphrase: str = Form(None),
    special_memory: str = Form(None),
    memory_snippets: str = Form(None),
    photo: UploadFile = File(None),
    voice_sample: UploadFile = File(None),
    db: Session = Depends(get_db),
):
    """更新家庭成员信息"""
    member = db.query(FamilyMember).filter(FamilyMember.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="成员不存在")

    if name is not None:
        member.name = name
    if relation is not None:
        member.relation = relation
    if favorite_food is not None:
        member.favorite_food = favorite_food
    if favorite_color is not None:
        member.favorite_color = favorite_color
    if personality is not None:
        member.personality = personality
    if catchphrase is not None:
        member.catchphrase = catchphrase
    if special_memory is not None:
        member.special_memory = special_memory
    if memory_snippets is not None:
        member.memory_snippets = memory_snippets

    if photo and photo.filename:
        photo_filename = _safe_filename(member.id, photo.filename, ".jpg")
        _save_upload(photo, PHOTO_DIR, photo_filename)
        member.photo_path = f"/static/uploads/photos/{photo_filename}"

    voice_cloning = False
    if voice_sample and voice_sample.filename:
        voice_filename = _safe_filename(member.id, voice_sample.filename, ".mp3")
        _save_upload(voice_sample, VOICE_DIR, voice_filename)
        member.voice_sample_path = f"/static/uploads/voices/{voice_filename}"
        # 标记为克隆中，后台异步完成真正的声音克隆
        member.voice_clone_id = "cloning..."
        voice_cloning = True

    db.commit()

    # 后台异步执行声音克隆
    if voice_cloning:
        voice_file_path = os.path.join(VOICE_DIR, voice_filename)
        asyncio.create_task(_do_voice_clone(member.id, voice_file_path))

    return {
        "success": True,
        "id": member.id,
        "voice_clone_id": member.voice_clone_id,
        "voice_cloned": member.voice_clone_id is not None and member.voice_clone_id != "cloning...",
        "voice_cloning": voice_cloning,
    }
