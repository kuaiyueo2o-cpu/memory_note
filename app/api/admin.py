from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.models import DailyBroadcast, Schedule, FamilyMember, CustomEvent, AppConfig, Elder

router = APIRouter(prefix="/api/admin", tags=["管理与调试"])


@router.post("/run-pipeline")
async def run_pipeline(force: bool = False, db: Session = Depends(get_db)):
    """手动触发当日Pipeline"""
    from app.services.pipeline import generate_daily_broadcasts
    result = await generate_daily_broadcasts(db, force=force)
    return result


@router.get("/test-push/{period}")
async def test_push(period: str, db: Session = Depends(get_db)):
    """立即推送指定段播报到前端（Demo演示用）"""
    if period not in ("morning", "noon", "evening"):
        return {"error": "无效的播报段"}

    today = date.today().isoformat()
    broadcast = db.query(DailyBroadcast).filter(
        DailyBroadcast.date == today,
        DailyBroadcast.period == period,
    ).first()

    if not broadcast or broadcast.status not in ("generated", "draft", "approved"):
        # 先尝试生成
        from app.services.pipeline import generate_daily_broadcasts
        await generate_daily_broadcasts(db)
        broadcast = db.query(DailyBroadcast).filter(
            DailyBroadcast.date == today,
            DailyBroadcast.period == period,
        ).first()

    if not broadcast:
        return {"error": "无法生成播报内容"}

    import json
    timestamps = []
    if broadcast.mention_timestamps:
        try:
            timestamps = json.loads(broadcast.mention_timestamps)
        except Exception:
            pass

    payload = {
        "event": "play_broadcast",
        "period": period,
        "script": broadcast.script,
        "audio_url": broadcast.audio_path,
        "timestamps": timestamps,
    }

    # 通过WebSocket推送
    from app.main import websocket_manager
    await websocket_manager.broadcast(payload)

    return {"success": True, "pushed": payload}


@router.get("/status")
async def pipeline_status(db: Session = Depends(get_db)):
    """查看今日Pipeline运行状态"""
    today = date.today().isoformat()
    broadcasts = db.query(DailyBroadcast).filter(DailyBroadcast.date == today).all()
    members = db.query(FamilyMember).all()

    result = {
        "date": today,
        "members_count": len(members),
        "members": [{"name": m.name, "relation": m.relation, "voice_cloned": m.voice_clone_id is not None} for m in members],
        "broadcasts": {},
    }

    for period in ["morning", "noon", "evening"]:
        b = next((x for x in broadcasts if x.period == period), None)
        result["broadcasts"][period] = {
            "status": b.status if b else "not_created",
            "has_audio": bool(b and b.audio_path) if b else False,
        }

    return result


@router.get("/schedules")
async def get_schedules(db: Session = Depends(get_db)):
    """获取播放时间表"""
    schedules = db.query(Schedule).all()
    return {"schedules": [
        {"id": s.id, "period": s.period, "trigger_time": s.trigger_time, "is_active": bool(s.is_active)}
        for s in schedules
    ]}


def _event_to_dict(e: CustomEvent) -> dict:
    return {
        "id": e.id,
        "title": e.title,
        "event_time": e.event_time,
        "repeat_rule": e.repeat_rule,
        "repeat_days": e.repeat_days or "",
        "event_date": e.event_date or "",
        "is_active": bool(e.is_active),
    }


@router.get("/events")
async def list_events(db: Session = Depends(get_db)):
    """获取全部自定义日程事件"""
    events = db.query(CustomEvent).order_by(CustomEvent.event_time).all()
    return {"events": [_event_to_dict(e) for e in events]}


class EventIn(BaseModel):
    title: str
    event_time: str
    repeat_rule: str = "daily"   # once/daily/weekday/weekly
    repeat_days: Optional[str] = ""   # weekly 时：0=周一..6=周日，逗号分隔
    event_date: Optional[str] = ""    # once 时：YYYY-MM-DD
    is_active: bool = True


@router.post("/events")
async def create_event(payload: EventIn, db: Session = Depends(get_db)):
    """新增一条自定义日程事件"""
    ev = CustomEvent(
        title=payload.title.strip(),
        event_time=payload.event_time,
        repeat_rule=payload.repeat_rule,
        repeat_days=payload.repeat_days or None,
        event_date=payload.event_date or None,
        is_active=1 if payload.is_active else 0,
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return {"success": True, "event": _event_to_dict(ev)}


@router.put("/events/{event_id}")
async def update_event(event_id: int, payload: EventIn, db: Session = Depends(get_db)):
    """更新一条自定义日程事件"""
    ev = db.query(CustomEvent).filter(CustomEvent.id == event_id).first()
    if not ev:
        return {"success": False, "error": "事件不存在"}
    ev.title = payload.title.strip()
    ev.event_time = payload.event_time
    ev.repeat_rule = payload.repeat_rule
    ev.repeat_days = payload.repeat_days or None
    ev.event_date = payload.event_date or None
    ev.is_active = 1 if payload.is_active else 0
    db.commit()
    return {"success": True, "event": _event_to_dict(ev)}


@router.delete("/events/{event_id}")
async def delete_event(event_id: int, db: Session = Depends(get_db)):
    """删除一条自定义日程事件"""
    ev = db.query(CustomEvent).filter(CustomEvent.id == event_id).first()
    if ev:
        db.delete(ev)
        db.commit()
    return {"success": True}


@router.get("/elder")
async def get_elder(db: Session = Depends(get_db)):
    """获取老人身份画像（单例，只有一条记录）"""
    elder = db.query(Elder).first()
    if not elder:
        return {"elder": None}
    return {
        "elder": {
            "id": elder.id,
            "name": elder.name or "",
            "age": elder.age,
            "gender": elder.gender or "",
            "education": elder.education or "",
            "symptoms": elder.symptoms or "",
            "severity": elder.severity or "",
            "worries": elder.worries or "",
        }
    }


class ElderIn(BaseModel):
    name: str = ""
    age: Optional[int] = None
    gender: str = ""
    education: str = ""
    symptoms: str = ""
    severity: str = ""
    worries: str = ""


@router.post("/elder")
async def save_elder(payload: ElderIn, db: Session = Depends(get_db)):
    """保存老人身份画像（单例：有则更新，无则创建）"""
    elder = db.query(Elder).first()
    if elder:
        elder.name = payload.name.strip()
        elder.age = payload.age
        elder.gender = payload.gender.strip()
        elder.education = payload.education.strip()
        elder.symptoms = payload.symptoms.strip()
        elder.severity = payload.severity.strip()
        elder.worries = payload.worries.strip()
    else:
        elder = Elder(
            name=payload.name.strip(),
            age=payload.age,
            gender=payload.gender.strip(),
            education=payload.education.strip(),
            symptoms=payload.symptoms.strip(),
            severity=payload.severity.strip(),
            worries=payload.worries.strip(),
        )
        db.add(elder)
    db.commit()
    db.refresh(elder)
    return {"success": True, "elder": {
        "id": elder.id, "name": elder.name, "age": elder.age,
        "gender": elder.gender, "education": elder.education,
        "symptoms": elder.symptoms, "severity": elder.severity, "worries": elder.worries,
    }}


@router.get("/config")
async def get_app_config(db: Session = Depends(get_db)):
    """获取应用配置（城市、播放时间等）"""
    from app.services.pipeline import get_config_value
    schedules = db.query(Schedule).all()
    sched_map = {s.period: s.trigger_time for s in schedules}
    return {
        "weather_city": get_config_value(db, "weather_city", "北京"),
        "schedules": {
            "morning": sched_map.get("morning", "07:30"),
            "noon": sched_map.get("noon", "14:00"),
            "evening": sched_map.get("evening", "20:00"),
        },
    }


class ConfigIn(BaseModel):
    weather_city: Optional[str] = None
    schedules: Optional[dict] = None  # {"morning": "07:30", ...}


@router.post("/config")
async def save_app_config(payload: ConfigIn, db: Session = Depends(get_db)):
    """保存应用配置（城市 + 播放时间）"""
    from app.services.pipeline import set_config_value
    if payload.weather_city is not None:
        set_config_value(db, "weather_city", payload.weather_city.strip())
    if payload.schedules:
        for period, t in payload.schedules.items():
            if period not in ("morning", "noon", "evening"):
                continue
            s = db.query(Schedule).filter(Schedule.period == period).first()
            if s:
                s.trigger_time = t
            else:
                db.add(Schedule(period=period, trigger_time=t, is_active=1))
        db.commit()
    return {"success": True}


def init_base_config(db: Session):
    """仅初始化最基础的默认配置（播放时间 + 城市），不添加任何演示成员/日程/播报。
    真实产品从空白起步，用户按需配置。"""
    # 确保默认时间表存在
    if not db.query(Schedule).all():
        default_schedules = [
            Schedule(period="morning", trigger_time="07:30", is_active=1),
            Schedule(period="noon", trigger_time="14:00", is_active=1),
            Schedule(period="evening", trigger_time="20:00", is_active=1),
        ]
        for s in default_schedules:
            db.add(s)
    # 确保默认城市配置存在
    if not db.query(AppConfig).filter(AppConfig.config_key == "weather_city").first():
        db.add(AppConfig(config_key="weather_city", config_value="北京"))
    db.commit()


@router.post("/seed-demo")
async def seed_demo_data(db: Session = Depends(get_db)):
    """填充演示数据"""
    # 添加演示家庭成员
    existing = db.query(FamilyMember).all()
    if not existing:
        demo_members = [
            FamilyMember(
                name="小红",
                relation="孙女",
                photo_path="/static/images/demo_daughter.jpg",
                favorite_food="奶奶做的红烧肉",
                favorite_color="桃红色",
                personality="开朗爱笑、心细体贴",
                catchphrase="奶奶，你别累着",
                special_memory="小时候每天早上都坐在小板凳上，让奶奶给她梳辫子",
                memory_snippets="在城东开花店，做的红烧肉最好吃，每周六固定打电话回家",
            ),
            FamilyMember(
                name="小明",
                relation="孙子",
                photo_path="/static/images/demo_son.jpg",
                favorite_food="奶奶做的桂花糕",
                favorite_color="天蓝色",
                personality="憨厚老实、嘴上不说心里疼奶奶",
                catchphrase="奶奶，我下个月就回来",
                special_memory="高考那年奶奶每天凌晨起来给他煮鸡蛋，他一直记到现在",
                memory_snippets="在外地工作，小时候总跟姐姐争遥控器，承诺每个月回家看奶奶",
            ),
            FamilyMember(
                name="老王",
                relation="老伴",
                photo_path="/static/images/demo_husband.jpg",
                favorite_food="她腌的酱萝卜",
                favorite_color="青灰色",
                personality="话不多、踏实可靠、疼了她一辈子",
                catchphrase="慢点走，我扶着你",
                special_memory="四十年前在老槐树下第一次牵她的手，他到现在还记得那天的月亮",
                memory_snippets="结婚四十年了，每天早上给她泡一杯红枣茶，傍晚一起在院子里散步，晚上给她念报纸",
            ),
        ]
        for m in demo_members:
            db.add(m)

    # 添加默认时间表
    existing_schedules = db.query(Schedule).all()
    if not existing_schedules:
        default_schedules = [
            Schedule(period="morning", trigger_time="07:30", is_active=1),
            Schedule(period="noon", trigger_time="14:00", is_active=1),
            Schedule(period="evening", trigger_time="20:00", is_active=1),
        ]
        for s in default_schedules:
            db.add(s)

    # 添加默认城市配置
    if not db.query(AppConfig).filter(AppConfig.config_key == "weather_city").first():
        db.add(AppConfig(config_key="weather_city", config_value="北京"))

    # 添加演示自定义日程事件
    if not db.query(CustomEvent).all():
        demo_events = [
            CustomEvent(title="护士来量血压", event_time="09:00", repeat_rule="daily"),
            CustomEvent(title="吃降压药", event_time="12:30", repeat_rule="daily"),
            CustomEvent(title="到楼下花园散步", event_time="16:00", repeat_rule="weekday"),
            CustomEvent(title="小红来探望", event_time="18:30", repeat_rule="weekly", repeat_days="5,6"),
        ]
        for e in demo_events:
            db.add(e)

    db.commit()

    # 生成演示播报
    from app.services.pipeline import generate_demo_broadcasts
    await generate_demo_broadcasts(db)

    return {"success": True, "message": "演示数据已填充"}
