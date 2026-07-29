from datetime import date
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.models.database import get_db
from app.models.models import DailyBroadcast
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter(prefix="/api/broadcast", tags=["播报内容"])


@router.get("/today")
async def get_today_broadcasts(db: Session = Depends(get_db)):
    """获取今日三段播报内容"""
    today = date.today().isoformat()
    broadcasts = db.query(DailyBroadcast).filter(DailyBroadcast.date == today).all()

    result = {"morning": None, "noon": None, "evening": None}
    for b in broadcasts:
        import json
        timestamps = []
        if b.mention_timestamps:
            try:
                timestamps = json.loads(b.mention_timestamps)
            except Exception:
                pass
        member_scripts = {}
        if b.member_scripts:
            try:
                member_scripts = json.loads(b.member_scripts)
            except Exception:
                pass
        result[b.period] = {
            "script": b.script,
            "audio_url": b.audio_path,
            "timestamps": timestamps,
            "member_scripts": member_scripts,
            "status": b.status,
            "generated_at": b.generated_at.isoformat() if b.generated_at else None,
        }
    return result


@router.get("/tts-usage")
async def get_tts_usage():
    """获取TTS用量和限额信息"""
    from app.services.pipeline import get_tts_usage_info
    return get_tts_usage_info()


@router.get("/{period}")
async def get_period_broadcast(period: str, db: Session = Depends(get_db)):
    """获取今日指定段播报"""
    if period not in ("morning", "noon", "evening"):
        return {"error": "无效的播报段，请使用 morning/noon/evening"}

    today = date.today().isoformat()
    broadcast = db.query(DailyBroadcast).filter(
        DailyBroadcast.date == today,
        DailyBroadcast.period == period,
    ).first()

    if not broadcast:
        return {"script": None, "audio_url": None, "timestamps": [], "status": "pending"}

    import json
    timestamps = []
    if broadcast.mention_timestamps:
        try:
            timestamps = json.loads(broadcast.mention_timestamps)
        except Exception:
            pass
    member_scripts = {}
    if broadcast.member_scripts:
        try:
            member_scripts = json.loads(broadcast.member_scripts)
        except Exception:
            pass

    return {
        "script": broadcast.script,
        "audio_url": broadcast.audio_path,
        "timestamps": timestamps,
        "member_scripts": member_scripts,
        "status": broadcast.status,
        "generated_at": broadcast.generated_at.isoformat() if broadcast.generated_at else None,
    }


@router.post("/generate-tts/{period}")
async def generate_tts_for_period(period: str, db: Session = Depends(get_db)):
    """为指定时段的播报生成TTS音频（含费用限额校验）"""
    import os
    if period not in ("morning", "noon", "evening"):
        return JSONResponse(status_code=400, content={"error": "无效的播报段，请使用 morning/noon/evening"})

    today = date.today().isoformat()
    broadcast = db.query(DailyBroadcast).filter(
        DailyBroadcast.date == today,
        DailyBroadcast.period == period,
    ).first()

    if not broadcast or not broadcast.script:
        return JSONResponse(status_code=404, content={"error": "该时段暂无播报文稿，请先生成播报"})

    from app.services.pipeline import synthesize_tts, concat_audio_segments, AUDIO_DIR, check_tts_quota, get_tts_usage_info, get_last_tts_error

    # ★ 安全阀预检：在真正调用API之前先检查
    allowed, quota_msg = check_tts_quota(broadcast.script)
    if not allowed:
        usage = get_tts_usage_info()
        return JSONResponse(
            status_code=429,
            content={
                "error": "用量上限",
                "detail": quota_msg,
                "usage": usage,
            }
        )

    # ★ 按成员各自音色分别合成
    import json as _json
    member_scripts = {}
    if broadcast.member_scripts:
        try:
            member_scripts = _json.loads(broadcast.member_scripts)
        except:
            pass

    if member_scripts:
        from app.models.models import FamilyMember
        all_members = db.query(FamilyMember).all()
        member_map = {str(m.id): m for m in all_members}
        audio_segments = []
        for mid, script_text in member_scripts.items():
            member_obj = member_map.get(mid)
            raw_voice = member_obj.voice_clone_id if member_obj and member_obj.voice_clone_id else None
            voice = raw_voice if raw_voice and not raw_voice.startswith("CLONE_FAIL:") and raw_voice != "cloning..." else None
            seg = await synthesize_tts(script_text, voice)
            if seg:
                audio_segments.append(seg)
        audio_data = await concat_audio_segments(audio_segments) if audio_segments else None
    else:
        audio_data = await synthesize_tts(broadcast.script, None)

    if audio_data:
        audio_filename = f"{today}_{period}.mp3"
        from app.services.media_store import save_media_bytes
        audio_path = await save_media_bytes(
            pathname=f"audio/{audio_filename}",
            body=audio_data,
            content_type="audio/mpeg",
            local_dir=AUDIO_DIR,
            local_filename=audio_filename,
            local_url=f"/static/audio/{audio_filename}",
        )

        # 更新数据库
        broadcast.audio_path = audio_path
        db.commit()

        # 返回最新用量信息
        usage = get_tts_usage_info()
        return {
            "success": True,
            "audio_url": audio_path,
            "period": period,
            "message": "音频生成成功",
            "usage": usage,
        }
    else:
        # ★ 透传具体失败原因
        err = get_last_tts_error() or "TTS音频生成失败，请检查 TTS 服务商 API 密钥配置"
        return JSONResponse(status_code=502, content={"error": err, "detail": err})


# ===== 播报内容审核 API =====

class ScriptReviewIn(BaseModel):
    """审核/编辑播报文稿的请求体"""
    script: str
    approved: bool = True  # True=确认通过固定, False=拒绝打回重生成


@router.get("/drafts")
async def get_draft_broadcasts(db: Session = Depends(get_db)):
    """获取今日所有草稿状态的播报（待审核）"""
    today = date.today().isoformat()
    drafts = db.query(DailyBroadcast).filter(
        DailyBroadcast.date == today,
        DailyBroadcast.status.in_(["draft", "pending"])
    ).all()

    result = []
    for b in drafts:
        import json
        timestamps = []
        if b.mention_timestamps:
            try:
                timestamps = json.loads(b.mention_timestamps)
            except Exception:
                pass
        result.append({
            "id": b.id,
            "period": b.period,
            "script": b.script,
            "audio_url": b.audio_path,
            "timestamps": timestamps,
            "status": b.status,
        })
    return {"drafts": result}


@router.put("/review/{broadcast_id}")
async def review_broadcast(broadcast_id: int, payload: ScriptReviewIn, db: Session = Depends(get_db)):
    """审核播报内容：确认固定 或 编辑后确认"""
    broadcast = db.query(DailyBroadcast).filter(DailyBroadcast.id == broadcast_id).first()
    if not broadcast:
        return JSONResponse(status_code=404, content={"error": "播报不存在"})

    if payload.approved:
        # 用户确认（可能编辑了文稿），固定内容
        if payload.script and payload.script.strip():
            broadcast.script = payload.script.strip()
        broadcast.status = "approved"
        db.commit()
        return {"success": True, "message": "播报内容已确认固定", "status": "approved"}
    else:
        # 拒绝，打回重生成
        broadcast.status = "rejected"
        db.commit()
        return {"success": True, "message": "已拒绝，将重新生成", "status": "rejected"}


@router.post("/regenerate/{period}")
async def regenerate_broadcast(period: str, db: Session = Depends(get_db)):
    """重新生成指定时段的播报内容（拒绝后重试）"""
    if period not in ("morning", "noon", "evening"):
        return JSONResponse(status_code=400, content={"error": "无效的播报段"})

    from app.services.pipeline import generate_single_broadcast
    result = await generate_single_broadcast(db, period, force=True)
    return result


@router.get("/all-status")
async def get_all_broadcast_status(db: Session = Depends(get_db)):
    """获取今日所有播报状态概览"""
    today = date.today().isoformat()
    broadcasts = db.query(DailyBroadcast).filter(DailyBroadcast.date == today).all()

    result = {}
    for period in ["morning", "noon", "evening"]:
        b = next((x for x in broadcasts if x.period == period), None)
        result[period] = {
            "exists": b is not None,
            "status": b.status if b else "not_created",
            "has_audio": bool(b and b.audio_path) if b else False,
            "script_preview": (b.script[:80] + "...") if b and b.script else None,
        }
    return result
