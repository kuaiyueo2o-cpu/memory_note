from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/emotion", tags=["情绪检测"])


class EmotionRequest(BaseModel):
    emotion: str
    current_speed: float = 0.85


# 情绪语速映射表
EMOTION_SPEED_MAP = {
    "fearful": 0.70,
    "angry": 0.70,
    "sad": 0.80,
    "neutral": 0.85,
    "happy": 0.85,
    "surprised": 0.80,
}


@router.post("/adjust")
async def adjust_speed(req: EmotionRequest):
    """上报情绪，获取建议语速"""
    new_speed = EMOTION_SPEED_MAP.get(req.emotion, 0.85)
    return {"new_speed": new_speed, "detected_emotion": req.emotion}
