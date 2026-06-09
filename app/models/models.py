from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.sql import func
from .database import Base


class Elder(Base):
    """播报收听者（老人）的身份画像"""
    __tablename__ = "elder"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(50), nullable=False, comment="老人姓名/称呼，如：奶奶、外婆")
    age = Column(Integer, nullable=True, comment="年龄")
    gender = Column(String(10), nullable=True, comment="性别：男/女")
    education = Column(String(50), nullable=True, comment="学历，如：初中、高中、大学")
    symptoms = Column(Text, nullable=True, comment="症状表现，如：近期记忆丧失、容易迷路")
    severity = Column(String(50), nullable=True, comment="症状程度：轻度/中度/重度")
    worries = Column(Text, nullable=True, comment="家属最担心什么")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class FamilyMember(Base):
    __tablename__ = "family_members"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(50), nullable=False, comment="家人姓名")
    relation = Column(String(50), nullable=False, comment="与患者关系")
    photo_path = Column(Text, nullable=True, comment="照片存储路径")
    voice_clone_id = Column(String(100), nullable=True, comment="MiniMax预设音色ID或克隆声音ID")
    voice_sample_path = Column(Text, nullable=True, comment="原始声音样本路径")
    memory_snippets = Column(Text, nullable=True, comment="共同记忆素材")
    # ===== 人物画像字段（用于构建老人心中对这个人最完整的描述）=====
    favorite_food = Column(String(200), nullable=True, comment="最爱吃什么")
    favorite_color = Column(String(100), nullable=True, comment="最喜欢什么颜色")
    personality = Column(String(200), nullable=True, comment="性格特点")
    catchphrase = Column(String(200), nullable=True, comment="口头禅/常说的话")
    special_memory = Column(Text, nullable=True, comment="与老人之间最难忘的一件事")
    created_at = Column(DateTime, server_default=func.now())


class DailyBroadcast(Base):
    __tablename__ = "daily_broadcasts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    date = Column(String(10), nullable=False, comment="播报日期 YYYY-MM-DD")
    period = Column(String(20), nullable=False, comment="morning/noon/evening")
    script = Column(Text, nullable=True, comment="Claude生成的播报文稿")
    audio_path = Column(Text, nullable=True, comment="合成MP3文件路径")
    mention_timestamps = Column(Text, nullable=True, comment="JSON数组，标记家人出现秒数")
    member_scripts = Column(Text, nullable=True, comment="JSON对象，按成员ID存储各自的独立播报文案")
    status = Column(String(20), default="pending", comment="pending/draft/approved/rejected/generated/failed")
    generated_at = Column(DateTime, server_default=func.now())


class Schedule(Base):
    __tablename__ = "schedules"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    period = Column(String(20), nullable=False, comment="morning/noon/evening")
    trigger_time = Column(String(5), nullable=False, comment="HH:MM")
    is_active = Column(Integer, default=1, comment="1=启用 0=禁用")


class PlayLog(Base):
    __tablename__ = "play_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    broadcast_id = Column(Integer, nullable=True, comment="关联daily_broadcasts.id")
    played_at = Column(DateTime, server_default=func.now())
    emotion = Column(String(30), nullable=True, comment="检测到的情绪")
    completed = Column(Integer, default=0, comment="是否完整听完")


class CustomEvent(Base):
    """家属自定义的日程事件（闹钟式：事件-时间-循环）"""
    __tablename__ = "custom_events"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    title = Column(String(100), nullable=False, comment="事件标题，例如：护士来量血压")
    event_time = Column(String(5), nullable=False, comment="时间 HH:MM")
    # 循环规则：once=不重复 / daily=每天 / weekday=工作日 / weekly=每周指定几天
    repeat_rule = Column(String(20), default="daily", comment="once/daily/weekday/weekly")
    # 当 repeat_rule=weekly 时，存储周几，逗号分隔，0=周一 ... 6=周日，例如 "0,2,4"
    repeat_days = Column(String(20), nullable=True, comment="weekly时的周几，逗号分隔(0=周一)")
    # 当 repeat_rule=once 时，存储具体日期 YYYY-MM-DD
    event_date = Column(String(10), nullable=True, comment="once时的具体日期 YYYY-MM-DD")
    is_active = Column(Integer, default=1, comment="1=启用 0=禁用")
    created_at = Column(DateTime, server_default=func.now())


class AppConfig(Base):
    """应用级键值配置（如所在城市），单行 key-value"""
    __tablename__ = "app_config"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    config_key = Column(String(50), nullable=False, unique=True, index=True, comment="配置键")
    config_value = Column(Text, nullable=True, comment="配置值")
