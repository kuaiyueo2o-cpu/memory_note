import os
import json
import logging
from datetime import date, datetime
from sqlalchemy.orm import Session
from app.models.models import DailyBroadcast, FamilyMember, Elder

logger = logging.getLogger(__name__)

AUDIO_DIR = (
    "/tmp/xiaonuan_audio"
    if os.environ.get("VERCEL") == "1" or os.environ.get("VERCEL_ENV")
    else os.path.join(os.path.dirname(__file__), "..", "static", "audio")
)
os.makedirs(AUDIO_DIR, exist_ok=True)

# ========== TTS 服务商开关 ==========
# 可选 "minimax"（MiniMax Speech-02，付费）或 "mimo"（小米 MiMo，限时免费）。
# 用户账户已充值，默认切回 MiniMax。
TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "minimax").lower()

# ========== TTS 用量限额系统 ==========
# MiniMax Speech-02-HD：3.5 元/万字符。设置 50 元硬上限作为"保险丝"，防止超支。
USAGE_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "tts_usage.json")
TTS_BUDGET_YUAN = 50.0
TTS_PRICE_PER_10K_CHARS = 3.5  # 元/万字符（MiniMax Speech-02-HD 实际单价）
# 50 元预算对应的字符上限（50 / 3.5 * 10000 ≈ 142857 字符）
TTS_CHAR_LIMIT = int(TTS_BUDGET_YUAN / TTS_PRICE_PER_10K_CHARS * 10000)

# 记录最后一次TTS调用的错误信息（供前端展示）
_LAST_TTS_ERROR = None


def get_last_tts_error():
    """获取最近一次TTS调用的失败原因"""
    return _LAST_TTS_ERROR


def _translate_mimo_error(http_status: int, message: str) -> str:
    """把小米 MiMo 的错误翻译成用户友好的中文提示"""
    msg_lower = (message or "").lower()
    if "voice" in msg_lower:
        return f"音色参数有误：{message}"
    if "insufficient" in msg_lower or "balance" in msg_lower or "quota" in msg_lower:
        return "MiMo 账户额度不足，请检查平台余额（TTS 当前应为限时免费）。"
    if http_status in (401, 403):
        return "MiMo API 密钥无效或无权限，请检查 platform.xiaomimimo.com 上的 API Key。"
    if http_status == 429:
        return "MiMo 触发限流，请稍后重试。"
    return f"MiMo 合成失败[{http_status}]: {message}"


def _load_usage() -> dict:
    """加载TTS用量记录"""
    try:
        if os.path.exists(USAGE_FILE):
            with open(USAGE_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"读取TTS用量文件失败: {e}")
    return {"total_chars": 0, "total_calls": 0, "estimated_cost_yuan": 0.0, "history": []}


def _save_usage(usage: dict):
    """保存TTS用量记录"""
    try:
        with open(USAGE_FILE, "w") as f:
            json.dump(usage, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存TTS用量文件失败: {e}")


def check_tts_quota(text: str) -> tuple[bool, str]:
    """检查TTS调用是否在限额内
    
    Returns:
        (allowed, message): 是否允许调用, 提示信息
    """
    char_count = len(text)
    usage = _load_usage()
    remaining = TTS_CHAR_LIMIT - usage["total_chars"]

    if char_count > remaining:
        return False, (
            f"⚠️ 已达 50 元预算上限：本次需 {char_count} 字符，"
            f"剩余仅 {remaining} 字符（约 ¥{remaining/10000*TTS_PRICE_PER_10K_CHARS:.2f}）。"
            f"已用 {usage['total_chars']} 字符 / ¥{usage['estimated_cost_yuan']:.2f}。"
        )

    cost = char_count / 10000 * TTS_PRICE_PER_10K_CHARS
    return True, (
        f"本次 {char_count} 字符（约 ¥{cost:.3f}），"
        f"剩余预算 ¥{(remaining-char_count)/10000*TTS_PRICE_PER_10K_CHARS:.2f} ✅"
    )


def record_tts_usage(text: str, success: bool):
    """记录TTS调用用量"""
    char_count = len(text)
    cost = char_count / 10000 * TTS_PRICE_PER_10K_CHARS
    usage = _load_usage()
    usage["total_chars"] += char_count
    usage["total_calls"] += 1
    usage["estimated_cost_yuan"] += cost
    usage["history"].append({
        "date": datetime.now().isoformat(),
        "chars": char_count,
        "cost_yuan": round(cost, 4),
        "success": success,
    })
    # 只保留最近50条历史
    if len(usage["history"]) > 50:
        usage["history"] = usage["history"][-50:]
    _save_usage(usage)
    logger.info(f"TTS用量: +{char_count}字符(¥{cost:.3f}), 累计{usage['total_chars']}字符(¥{usage['estimated_cost_yuan']:.2f})")


def get_tts_usage_info() -> dict:
    """获取TTS用量信息（供API返回）"""
    usage = _load_usage()
    remaining = max(0, TTS_CHAR_LIMIT - usage["total_chars"])
    return {
        "total_chars": usage["total_chars"],
        "total_calls": usage["total_calls"],
        "estimated_cost_yuan": round(usage["estimated_cost_yuan"], 2),
        "char_limit": TTS_CHAR_LIMIT,
        "remaining_chars": remaining,
        "remaining_cost_yuan": round(remaining / 10000 * TTS_PRICE_PER_10K_CHARS, 2),
        "budget_yuan": TTS_BUDGET_YUAN,
        "price_per_10k_chars": TTS_PRICE_PER_10K_CHARS,
        "usage_percent": round(usage["total_chars"] / TTS_CHAR_LIMIT * 100, 1),
    }

# ========== 默认兜底文案 ==========
FALLBACK_SCRIPTS = {
    "morning": "{elder_name}，早上好。今天天气不错，记得穿暖和点。今天好好照顾自己，我们都想你。",
    "noon": "{elder_name}，中午好。想和您慢慢说几句家里的事。您先安心休息，我们一直惦记您。",
    "evening": "{elder_name}，今天过得好吗？好好休息，明天又是新的一天。晚安，我们都爱你。",
}

# ========== 大模型 Prompt 模板（基于详细家人画像，生成温情内容）==========
SYSTEM_PERSONA = """你是经家属授权的家庭陪伴播报系统，依据真实建档资料，为认知障碍长者生成温和、可理解的口语播报。你不是现实中的家人，不替任何家人承诺现实行动。

【收听者画像——你必须根据这个人的真实情况来调整说话方式】
{elder_profile}

【沟通原则】
根据收听者画像，你需要做到：
- 用最简单的口语，像面对面拉家常一样，绝不用书面语和华丽辞藻
- 句子短，每句不超过15个字，多用短句和停顿
- 语气温柔、缓慢、笃定，传递安全感；尊重对方，不使用幼态化、命令式或考核式表达
- 根据老人的症状和程度调整措辞：轻度可以多说一些信息；中重度一次只传达一个主题，不堆叠家人、安排或问题
- 如果老人容易焦虑，多用"没事的""我在呢"；如果老人容易迷路，多提醒"你在家里，很安全"
- 可以自然说出真实的家人名字、关系、爱好和共同回忆，但只用于建立熟悉感；不要求、暗示或测试老人是否记得
- 绝不质问、不说"你怎么又忘了""你还记得我吗"，不要求老人证明记忆；先回应情绪，再给简短、可核实的信息
- 不把系统说成唯一依靠，不说"只有我陪你"；不索取隐私、钱财、验证码，也不引导与现实家人隔离
- 称呼老人时，使用老人画像中填写的称呼（{elder_name}），不可自行替换

【🔴 真实原则——最严厉的禁令，违反即为不合格输出】
你的每一句话都必须有据可查。以下行为是绝对禁止的：

1. 禁止编造任何动作场景：不说"给你热了牛奶""给你拿衣服""陪你散步""给你端茶"——除非系统提供的真实日程或家人画像中明确写了这件事，否则一个字都不能提。
2. 禁止编造任何时间安排：不说"今天下午谁来""明天一起去哪"——除非系统提供了真实的日程事件。
3. 禁止编造任何家人行为：不说"小刚今天回来了""孙女来看你了"——除非日程中有明确记录。
4. 禁止编造任何回忆场景：不说"还记得我们一起包饺子吗"——除非家人画像中明确写了"最难忘的事"或"共同回忆"包含这个场景。
5. 禁止编造任何饮食：不说"吃了什么""给你做了什么"——除非家人画像明确写了相关食物偏好。
6. 禁止编造天气建议的具体行动：不能说"出门记得带伞"除非天气数据确实有雨；不能说"穿厚点"除非气温确实低。
7. 信息缺失时只能用安全的泛化表达："今天好好照顾自己""我们都在想你""明天又是新的一天"——绝不填充具体内容。
8. 关于"今天的好事"和"明天的安排"，只能使用系统明确提供的内容。如果系统没有提供任何安排，就简单说"今天好好休息"或"明天又是新的一天"，绝不编造。
9. 不做诊断、治疗建议或紧急承诺。若资料出现明显危险、急性不适、走失、受骗或伤害线索，只建议立即联系身边可信家属、照护者或当地紧急服务，不淡化风险。

请直接输出可以照着念的口语播报稿，不要任何标题、前缀、括号说明或表情符号。"""

MORNING_PROMPT = """现在是早晨，请给{elder_name}做一段温暖的「早安播报」。

【当前实时天气】{weather}
【今天的真实安排】{calendar}
【家人画像】（以下是基于真实建档信息的家人描述，只可使用这些信息，不可编造）
{member_profiles}
【今天日期】{today}

写作要求：
1. 第一句先亲切地叫一声「{elder_name}」
2. 用当前实时天气提醒今天该怎么穿、注意什么，结合老人的实际情况给出建议（天气数据只能用上面提供的，不能自行补充）
3. 如果「今天的真实安排」中有内容，自然带出让老人心里有数；如果写的是"没有特别安排"，就说"今天好好休息"——绝对不能编造任何安排
4. 如果家属有担心的事项（如容易迷路、忘记吃药），温柔地穿插提醒
5. 提到家人时，只使用画像中真实存在的信息（姓名、关系、爱好等），绝不编造
6. 绝对禁止编造任何动作场景：不说"给你热了牛奶""给你拿衣服""给你端茶"——除非上面画像中明确写了这件事
7. 不指示老人点击屏幕，也不要求回复或完成任务
8. 全篇100到150字，口语短句，结尾给一点盼头

直接输出播报稿。"""

NOON_PROMPT = """现在是午间，请做一段「熟悉感陪伴」播报，让{elder_name}感到被惦记和安心；不要测试或要求回忆。

【需要重点介绍的家人画像】（以下是基于真实建档信息的家人描述，只可使用这些信息，不可编造）
{member_profiles}
【今天日期】{today}

写作要求：
1. 第一句用「{elder_name}，中午好，想和您慢慢说几句家里的事。」或同等温和说法开头
2. 根据老人的程度选择重点：中重度只介绍一位家人；轻度最多介绍两位。每位只说姓名、关系和一项真实信息，不堆叠信息
3. 只用画像中真实存在的爱好、口头禅、最难忘的小事来建立熟悉感，不要求老人想起或回答
4. 如果画像中某项标注了[未填写]，绝对不能编造替代内容，直接跳过该话题
5. 绝对禁止编造任何动作场景：不说"给你热了牛奶""给你拿衣服"——除非画像明确写了
6. 如果老人有记忆障碍，语气更耐心，内容更简洁；绝不说"你忘了"或"你还记得我吗"
7. 给老人安全感，但不替现实家人承诺行动；可说"家里人一直惦记您"
8. 不指示老人点击屏幕，也不要求回复或完成任务
9. 全篇80到130字，口语短句，让老人感到被关心

直接输出播报稿。"""

EVENING_PROMPT = """现在是晚上，请做一段温柔的「晚安陪伴」播报。

【今天真实发生的事/安排】{calendar}
【明天的真实安排】{tomorrow_calendar}
【家人画像】（以下是基于真实建档信息的家人描述，只可使用这些信息，不可编造）
{member_profiles}
【今天日期】{today}

写作要求：
1. 第一句先亲切地叫一声「{elder_name}」
2. 如果有今天真实发生的事或安排，可以简单回顾；如果写的是"没有特别安排"，就说「今天好好休息了一天，真棒」——绝不编造吃了什么、见了谁、做了什么
3. 如果有明天真实的家人安排，提一句给盼头；如果写的是"没有特别安排"，就说「明天又是新的一天」——绝不编造谁来探望
4. 绝对禁止编造任何动作场景：不说"给你热了牛奶""给你盖被子""给你拿衣服"——除非上面安排中明确写了
5. 如果老人夜间容易不安或焦虑，多给安全感，说"你很安全""我们都在"
6. 提到家人时，只使用画像中真实存在的信息，绝不编造
7. 不指示老人点击屏幕，也不要求回复或完成任务
8. 全篇100到150字，口语短句，以「晚安」结尾

直接输出播报稿。"""

TIMESTAMPS_PROMPT = """分析以下播报稿，找出其中提到家人的片段，输出JSON数组。

播报稿：
{script}

家庭成员列表：{members}

输出格式：
[{{"member_id": id, "name": "姓名", "start_sec": 开始秒数, "end_sec": 结束秒数, "excerpt": "提到的原文片段"}}]

规则：
- 语速1.0x，每字约0.3秒
- start_sec和end_sec是相对于音频开始的秒数
- 只输出JSON数组，不要加任何其他文字"""

# ========== 按人定制的播报Prompt ==========

MEMBER_GREETING_PROMPT = """现在是{period_label}，请替{member_relation}「{member_name}」给{elder_name}说一段温暖的播报话。

【收听者画像】
{elder_profile}

【这位家人的画像】（只可使用以下真实信息，不可编造）
{member_profile}

【当前实时天气】{weather}
【今天的真实安排】{calendar}
【今天日期】{today}

写作要求：
1. 第一句叫一声「{elder_name}」
2. 自报身份：「我是{member_name}，你的{member_relation}」
3. 根据时段调整内容：
   - 早晨：温暖问候+天气提醒（只能用上面提供的天气数据），给今天一点盼头
   - 午间：用真实存在的爱好、口头禅、最难忘的小事建立熟悉感；不测试记忆、不要求回答
   - 晚上：温柔陪伴+安全感+晚安
4. 绝对禁止编造：
   - 不说"给你热了牛奶""给你拿衣服""陪你散步"——除非画像或安排中明确写了
   - 不说"今天我来看你了"——除非安排中确实有
   - 不说任何画像中[未填写]的内容
5. 全篇50到80字，口语短句，像面对面拉家常
6. 不指示老人点击屏幕，也不要求回复或完成任务

直接输出播报稿。"""

MEMBER_INTRO_PROMPT = """请替{member_relation}「{member_name}」做一段简短的自我介绍，帮助{elder_name}建立熟悉感和安全感；不要测试记忆。

【收听者画像】
{elder_profile}

【这位家人的画像】（只可使用以下真实信息，不可编造）
{member_profile}

【今天日期】{today}

写作要求：
1. 开头：「{elder_name}，您好，我是{member_name}。」或同等温和说法
2. 自报姓名和关系：「我是{member_name}，你的{member_relation}」
3. 用画像中真实存在的爱好、口头禅、最难忘的小事建立熟悉感，不要求老人想起来或回答
4. 如果画像中某项标注[未填写]，绝对不能编造，直接跳过
5. 绝对禁止编造动作场景（不说"给你热牛奶""给你拿衣服"等）
6. 如果老人有记忆障碍，要更耐心、更简洁；绝不说"你忘了"或"你还记得我吗"
7. 给老人安全感，但不替现实家人承诺行动；可说"家里人一直惦记您"
8. 全篇60到100字，口语短句
9. 不指示老人点击屏幕，也不要求回复或完成任务

直接输出播报稿。"""


async def call_deepseek_api(prompt: str, system: str = "", temperature: float = 0.85, max_tokens: int = 600) -> str:
    """调用 DeepSeek Chat API 生成文本。

    DeepSeek 与 OpenAI 接口兼容：POST {base}/chat/completions，Bearer 鉴权。
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return ""

    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        import httpx
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            if response.status_code == 200:
                data = response.json()
                return (data["choices"][0]["message"]["content"] or "").strip()
            logger.error(f"DeepSeek API错误: {response.status_code} {response.text[:300]}")
            return ""
    except Exception as e:
        logger.error(f"DeepSeek API调用失败: {e}")
        return ""


async def call_claude_api(prompt: str) -> str:
    """调用 Anthropic Claude API 生成文本（直接调用，不做任何回退）。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY 未设置")
        return ""

    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 500,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            if response.status_code == 200:
                data = response.json()
                return data["content"][0]["text"]
            else:
                logger.error(f"Claude API错误: {response.status_code} {response.text}")
                return ""
    except Exception as e:
        logger.error(f"Claude API调用失败: {e}")
        return ""


# 小米 MiMo TTS 可用音色（经实测确认）
MIMO_VOICES = ["mimo_default", "冰糖", "茉莉", "苏打", "白桦", "Mia", "Chloe", "Milo", "Dean"]
MIMO_DEFAULT_VOICE = "茉莉"  # 温柔女声，适合给家人的温暖播报


async def call_mimo_tts(text: str, voice: str = MIMO_DEFAULT_VOICE) -> bytes:
    """调用小米 MiMo-V2.5-TTS 合成语音（限时免费）

    接口规范（经实测验证）：
    - 端点: {base}/v1/chat/completions
    - 待合成文本放在 assistant 角色消息的 content 中
    - modalities=["audio"]，audio.voice 用中文/英文音色名
    - 返回的音频在 choices[0].message.audio.data（base64 编码的 mp3）

    Args:
        text: 要合成的文本
        voice: 音色名，默认"茉莉"。可选: mimo_default/冰糖/茉莉/苏打/白桦/Mia/Chloe/Milo/Dean

    Returns:
        音频二进制数据(bytes)，失败返回None。失败原因可通过 get_last_tts_error() 获取。
    """
    import base64
    global _LAST_TTS_ERROR
    _LAST_TTS_ERROR = None

    # 兼容旧的 MiniMax 音色ID：若传入的不是 MiMo 合法音色，回退到默认音色
    if voice not in MIMO_VOICES:
        voice = MIMO_DEFAULT_VOICE

    api_key = os.environ.get("MIMO_API_KEY", "") or os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        _LAST_TTS_ERROR = "未配置 MIMO_API_KEY 环境变量"
        logger.warning("MIMO_API_KEY未设置，跳过TTS")
        return None

    # ★ 安全阀检查：防止异常情况下的无限调用
    allowed, quota_msg = check_tts_quota(text)
    if not allowed:
        _LAST_TTS_ERROR = quota_msg
        logger.warning(f"TTS限额拒绝: {quota_msg}")
        return None
    logger.info(f"TTS限额通过: {quota_msg}")

    base_url = os.environ.get("MIMO_BASE_URL", "https://api.xiaomimimo.com")
    model = os.environ.get("MIMO_TTS_MODEL", "mimo-v2.5-tts")

    try:
        import httpx
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "modalities": ["audio"],
                    "audio": {"voice": voice, "format": "mp3"},
                    # ★ 关键：MiMo TTS 要求待朗读文本放在 assistant 角色
                    "messages": [{"role": "assistant", "content": text}],
                },
            )

            if response.status_code != 200:
                # 尝试解析结构化错误信息
                err_msg = response.text[:200]
                try:
                    err_json = response.json()
                    err_msg = err_json.get("error", {}).get("message", err_msg)
                except Exception:
                    pass
                _LAST_TTS_ERROR = _translate_mimo_error(response.status_code, err_msg)
                logger.error(f"MiMo TTS错误: {response.status_code} {response.text[:300]}")
                return None

            data = response.json()
            # 提取音频：choices[0].message.audio.data 为 base64 mp3
            audio_result = None
            try:
                audio_b64 = data["choices"][0]["message"]["audio"]["data"]
                if audio_b64:
                    audio_result = base64.b64decode(audio_b64)
            except (KeyError, IndexError, TypeError):
                audio_result = None

            if audio_result:
                # ★ 真正合成成功才计入用量（MiMo TTS 限时免费，费用按0计）
                record_tts_usage(text, True)
                return audio_result
            else:
                _LAST_TTS_ERROR = f"返回格式异常，未找到音频数据: {list(data.keys())}"
                logger.error(f"MiMo TTS返回格式异常: {str(data)[:300]}")
                return None
    except Exception as e:
        _LAST_TTS_ERROR = f"网络请求异常: {e}"
        logger.error(f"MiMo TTS调用失败: {e}")
        return None


# ========== MiniMax Speech TTS ==========
# MiniMax 国内版接口（api.minimaxi.com，备用 api-bj.minimaxi.com）
# 鉴权使用 JWT 格式的 API Key（eyJ... 开头，非 sk-api- 开头），可在
# 控制台「账户管理 > 接口密钥」获取：
# https://platform.minimax.io/user-center/basic-information/interface-key
MINIMAX_DEFAULT_VOICE = "Chinese (Mandarin)_Lyrical_Voice"  # 中文温暖女声，适合播报
# 旧的 MiMo 音色名 → MiniMax 国际版音色映射
_MIMO_TO_MINIMAX_VOICE = {
    "茉莉": "Chinese (Mandarin)_Lyrical_Voice",
    "冰糖": "Chinese (Mandarin)_HK_Flight_Attendant",
    "苏打": "English_Graceful_Lady",
    "白桦": "English_Insightful_Speaker",
    "mimo_default": "Chinese (Mandarin)_Lyrical_Voice",
}


def _translate_minimax_error(status_code, message: str) -> str:
    """把 MiniMax 的错误码翻译成用户友好的中文提示"""
    msg = message or ""
    if status_code in (1008,) or "insufficient" in msg.lower() or "balance" in msg.lower():
        return "MiniMax 账户余额不足，请到 platform.minimaxi.com 充值后重试。"
    if status_code in (1004, 1001) or "invalid" in msg.lower() or "auth" in msg.lower():
        return "MiniMax API 密钥无效，请检查 MINIMAX_API_KEY 是否为 JWT 格式（eyJ 开头，非 sk-api-）。"
    if status_code == 1002 or "rate" in msg.lower():
        return "MiniMax 触发限流，请稍后重试。"
    return f"MiniMax 合成失败[{status_code}]: {msg}"


def _normalize_minimax_voice(voice_id: str) -> str:
    """把传入的音色 ID 规整为 MiniMax 合法音色。"""
    if not voice_id:
        return MINIMAX_DEFAULT_VOICE
    # 若是 MiMo 中文音色名，映射到 MiniMax；映射不到则用默认
    if voice_id in _MIMO_TO_MINIMAX_VOICE:
        return _MIMO_TO_MINIMAX_VOICE[voice_id]
    if voice_id in MIMO_VOICES:
        return MINIMAX_DEFAULT_VOICE
    # 否则认为本身就是 MiniMax 音色 ID，原样使用
    return voice_id


async def call_minimax_tts(text: str, voice_id: str = MINIMAX_DEFAULT_VOICE) -> bytes:
    """调用 MiniMax Speech TTS 合成语音（国内版，付费）。

    接口：POST https://api.minimaxi.com/v1/t2a_v2
    鉴权：Bearer + JWT 格式 API Key（eyJ... 开头）
    返回的音频为 hex 编码字符串，位于 data.audio。
    """
    global _LAST_TTS_ERROR
    _LAST_TTS_ERROR = None

    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        _LAST_TTS_ERROR = "未配置 MINIMAX_API_KEY 环境变量"
        logger.warning("MINIMAX_API_KEY未设置，跳过TTS")
        return None

    # ★ 50 元预算安全阀
    allowed, quota_msg = check_tts_quota(text)
    if not allowed:
        _LAST_TTS_ERROR = quota_msg
        logger.warning(f"TTS限额拒绝: {quota_msg}")
        return None
    logger.info(f"TTS限额通过: {quota_msg}")

    base_url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com")
    model = os.environ.get("MINIMAX_TTS_MODEL", "speech-2.8-hd")
    group_id = os.environ.get("MINIMAX_GROUP_ID", "")
    voice = _normalize_minimax_voice(voice_id)

    url = f"{base_url}/v1/t2a_v2"
    if group_id:
        url += f"?GroupId={group_id}"

    try:
        import httpx
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "text": text,
                    "stream": False,
                    "language_boost": "auto",
                    "output_format": "hex",
                    "voice_setting": {
                        "voice_id": voice,
                        "speed": 1.0,
                        "vol": 1.0,
                        "pitch": 0,
                    },
                    "audio_setting": {
                        "sample_rate": 32000,
                        "bitrate": 128000,
                        "format": "mp3",
                        "channel": 1,
                    },
                },
            )

            if response.status_code != 200:
                _LAST_TTS_ERROR = _translate_minimax_error(response.status_code, response.text[:200])
                logger.error(f"MiniMax HTTP错误: {response.status_code} {response.text[:300]}")
                return None

            data = response.json()
            base_resp = data.get("base_resp", {})
            status_code = base_resp.get("status_code", 0)
            if status_code != 0:
                _LAST_TTS_ERROR = _translate_minimax_error(status_code, base_resp.get("status_msg", ""))
                logger.error(f"MiniMax 业务错误: {base_resp}")
                return None

            audio_hex = (data.get("data") or {}).get("audio")
            if not audio_hex:
                _LAST_TTS_ERROR = f"MiniMax 返回格式异常，未找到音频: {list(data.keys())}"
                logger.error(f"MiniMax 返回异常: {str(data)[:300]}")
                return None

            audio_result = bytes.fromhex(audio_hex)
            # ★ 仅在真正合成成功后计费
            record_tts_usage(text, True)
            return audio_result
    except Exception as e:
        _LAST_TTS_ERROR = f"网络请求异常: {e}"
        logger.error(f"MiniMax TTS调用失败: {e}")
        return None


# ========== MiniMax 国内版音色快速复刻（两步流程）==========
async def clone_voice_minimax(audio_file_path: str) -> str:
    """调用 MiniMax 国内版音色快速复刻 API，上传音频文件并获得 voice_id。

    流程（两步）：
    1. POST /v1/files/upload (multipart/form-data, purpose=voice_clone) → 获取 file_id
    2. POST /v1/voice_clone (JSON, file_id + 自定义 voice_id) → 克隆成功

    Args:
        audio_file_path: 服务器本地的音频文件绝对路径（支持 mp3/m4a/wav）

    Returns:
        克隆成功返回 voice_id 字符串；失败返回以 CLONE_FAIL: 开头的中文原因。
    """
    import hashlib

    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        logger.warning("声音克隆：未配置 MINIMAX_API_KEY")
        return "CLONE_FAIL:未配置 MiniMax 国内版 API Key"

    if not os.path.exists(audio_file_path):
        logger.error(f"声音克隆：音频文件不存在 {audio_file_path}")
        return "CLONE_FAIL:录音文件不存在，请重新上传"

    file_size = os.path.getsize(audio_file_path)
    if file_size > 20 * 1024 * 1024:
        return "CLONE_FAIL:录音超过 20MB，请压缩后重新上传"

    # 克隆与 TTS 必须使用同一个国内 MiniMax 账号/区域，否则克隆音色无法用于合成。
    base_url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com").rstrip("/")
    if "minimax.io" in base_url:
        return "CLONE_FAIL:请改用 MiniMax 国内版接口 api.minimaxi.com"

    # 读取音频文件
    with open(audio_file_path, "rb") as f:
        audio_data = f.read()

    # 生成唯一的voice_id（必须：8-256字符，字母开头，可含字母数字-_，不能以-_结尾）
    file_hash = hashlib.md5(audio_data).hexdigest()[:16]
    custom_voice_id = f"MemClone{file_hash}"

    # 判断文件MIME类型，并确保文件名后缀是API支持的（mp3/m4a/wav）
    ext = os.path.splitext(audio_file_path)[1].lower()
    mime_map = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav", ".mp4": "audio/mp4"}
    content_type = mime_map.get(ext, "audio/mpeg")
    # MiniMax 只接受 mp3/m4a/wav，mp4和m4a实际是同一种MPEG-4容器
    upload_filename = os.path.basename(audio_file_path)
    if ext == ".mp4":
        upload_filename = upload_filename.rsplit(".", 1)[0] + ".m4a"

    try:
        import httpx

        async with httpx.AsyncClient(timeout=120) as client:
            # ===== Step 1: 上传音频文件 =====
            upload_url = f"{base_url}/v1/files/upload"
            logger.info(f"声音克隆 Step1: 上传文件到 {upload_url}")

            upload_resp = await client.post(
                upload_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                },
                files={
                    "file": (upload_filename, audio_data, content_type),
                },
                data={
                    "purpose": "voice_clone",
                },
            )

            if upload_resp.status_code != 200:
                logger.error(f"声音克隆上传HTTP错误: {upload_resp.status_code} {upload_resp.text[:300]}")
                return f"CLONE_FAIL:录音上传失败（{upload_resp.status_code}）"

            upload_data = upload_resp.json()
            upload_base_resp = upload_data.get("base_resp", {})
            if upload_base_resp.get("status_code", 0) != 0:
                logger.error(f"声音克隆上传业务错误: {upload_base_resp}")
                return f"CLONE_FAIL:{upload_base_resp.get('status_msg') or '录音上传失败'}"

            file_id = upload_data.get("file", {}).get("file_id")
            if not file_id:
                logger.error(f"声音克隆上传返回无file_id: {upload_data}")
                return "CLONE_FAIL:上传成功但未获取到文件编号"

            logger.info(f"声音克隆 Step1 成功: file_id={file_id}")

            # ===== Step 2: 执行声音克隆 =====
            clone_url = f"{base_url}/v1/voice_clone"
            logger.info(f"声音克隆 Step2: 创建国内版 voice_id={custom_voice_id}")

            clone_resp = await client.post(
                clone_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "file_id": int(file_id),
                    "voice_id": custom_voice_id,
                },
            )

            logger.info(f"声音克隆 Step2 响应: status={clone_resp.status_code}, body={clone_resp.text[:500]}")

            if clone_resp.status_code != 200:
                logger.error(f"声音克隆HTTP错误: {clone_resp.status_code} {clone_resp.text[:300]}")
                return f"CLONE_FAIL:音色复刻失败（{clone_resp.status_code}），请检查实名认证和账户额度"

            clone_data = clone_resp.json()
            clone_base_resp = clone_data.get("base_resp", {})
            if clone_base_resp.get("status_code", 0) != 0:
                error_msg = clone_base_resp.get("status_msg", "")
                logger.error(f"声音克隆业务错误: {clone_base_resp}")
                return f"CLONE_FAIL:{error_msg or '音色复刻未通过，请检查录音和账号权限'}"

            # 克隆成功
            logger.info(f"✅ 声音克隆成功: voice_id={custom_voice_id}")
            return custom_voice_id

    except Exception as e:
        logger.error(f"声音克隆异常: {e}")
        return "CLONE_FAIL:网络连接失败，请稍后重试"


# ========== 统一 TTS 入口（按 TTS_PROVIDER 分发）==========
async def synthesize_tts(text: str, voice_id: str = None) -> bytes:
    """统一 TTS 合成入口，根据 TTS_PROVIDER 选择服务商。"""
    if TTS_PROVIDER == "mimo":
        return await call_mimo_tts(text, voice_id or MIMO_DEFAULT_VOICE)
    return await call_minimax_tts(text, voice_id or MINIMAX_DEFAULT_VOICE)


async def concat_audio_segments(segments: list) -> bytes:
    """拼接多段MP3音频为一个完整文件。
    
    直接拼接MP3帧（MP3格式天然支持帧拼接，无需重编码）。
    大多数播放器和浏览器都能正确播放拼接后的MP3。
    """
    if not segments:
        return None
    if len(segments) == 1:
        return segments[0]
    
    # MP3帧格式天然支持直接拼接
    return b"".join(segments)



# wttr.in 天气代码 → 中文天气描述映射（WWO weatherCode）
_WEATHER_CODE_ZH = {
    "113": "晴", "116": "局部多云", "119": "多云", "122": "阴",
    "143": "薄雾", "248": "雾", "260": "冻雾",
    "176": "局部有雨", "263": "小毛毛雨", "266": "毛毛雨", "281": "冻毛毛雨", "284": "强冻毛毛雨",
    "293": "局部小雨", "296": "小雨", "299": "局部中雨", "302": "中雨",
    "305": "局部大雨", "308": "大雨", "311": "小冻雨", "314": "中到大冻雨",
    "353": "局部阵雨", "356": "中到大阵雨", "359": "倾盆阵雨",
    "179": "局部有雪", "227": "风吹雪", "230": "暴风雪",
    "323": "局部小雪", "326": "小雪", "329": "局部中雪", "332": "中雪",
    "335": "局部大雪", "338": "大雪", "350": "冰雹", "377": "雷暴冰雹",
    "200": "局部雷雨", "386": "局部雷阵雨", "389": "强雷阵雨", "392": "局部雷雪", "395": "中到大雷雪",
    "317": "小雨夹雪", "320": "中到大雨夹雪", "362": "局部雨夹雪", "365": "中到大雨夹雪", "368": "小阵雪", "371": "中到大阵雪", "374": "局部冰粒", "317": "雨夹雪",
}

# 英文天气描述 → 中文（wttr.in 的 weatherDesc 兜底）
_WEATHER_DESC_ZH = {
    "sunny": "晴", "clear": "晴", "partly cloudy": "局部多云", "cloudy": "多云",
    "overcast": "阴", "mist": "薄雾", "fog": "雾", "freezing fog": "冻雾",
    "haze": "霾", "smoky haze": "烟霾", "smoke": "烟雾", "dust": "浮尘", "sand": "沙尘",
    "patchy rain possible": "可能有零星小雨", "patchy rain nearby": "附近有零星小雨",
    "light rain": "小雨", "moderate rain": "中雨", "heavy rain": "大雨",
    "light drizzle": "毛毛雨", "patchy light drizzle": "零星毛毛雨",
    "light rain shower": "小阵雨", "moderate or heavy rain shower": "中到大阵雨",
    "torrential rain shower": "暴雨", "thundery outbreaks possible": "可能有雷阵雨",
    "light snow": "小雪", "moderate snow": "中雪", "heavy snow": "大雪",
    "patchy snow possible": "可能有零星小雪", "blizzard": "暴风雪",
    "light sleet": "小雨夹雪", "moderate or heavy sleet": "中到大雨夹雪",
}


def _desc_to_zh(code: str, desc_en: str) -> str:
    """把天气代码/英文描述转为中文，逐级兜底"""
    if code in _WEATHER_CODE_ZH:
        return _WEATHER_CODE_ZH[code]
    key = (desc_en or "").strip().lower()
    if key in _WEATHER_DESC_ZH:
        return _WEATHER_DESC_ZH[key]
    for k, v in _WEATHER_DESC_ZH.items():
        if k in key:
            return v
    return desc_en or "晴"


def _clothing_advice(temp: float) -> str:
    """根据气温给出适老化穿衣建议"""
    if temp >= 30:
        return "天气很热，穿轻薄透气的衣服，多喝水。"
    if temp >= 22:
        return "天气温暖，穿件长袖就好。"
    if temp >= 15:
        return "有点凉，记得加件薄外套。"
    if temp >= 8:
        return "天气偏冷，穿厚外套，注意保暖。"
    return "天气很冷，穿羽绒服戴围巾，别着凉。"


def get_config_value(db: Session, key: str, default: str = "") -> str:
    """读取应用配置（带默认值）"""
    try:
        from app.models.models import AppConfig
        row = db.query(AppConfig).filter(AppConfig.config_key == key).first()
        if row and row.config_value:
            return row.config_value
    except Exception as e:
        logger.warning(f"读取配置 {key} 失败: {e}")
    return default


def set_config_value(db: Session, key: str, value: str):
    """写入应用配置"""
    from app.models.models import AppConfig
    row = db.query(AppConfig).filter(AppConfig.config_key == key).first()
    if row:
        row.config_value = value
    else:
        db.add(AppConfig(config_key=key, config_value=value))
    db.commit()


async def generate_single_broadcast(db: Session, period: str, force: bool = False) -> dict:
    """重新生成指定时段的播报内容（用于审核拒绝后重试）"""
    if period not in ("morning", "noon", "evening"):
        return {"error": "无效的播报段"}

    today = date.today().isoformat()
    logger.info(f"重新生成 {today} {period} 的播报内容...")

    weather = await fetch_weather(db)
    calendar = await fetch_calendar_events(db)
    member_profiles = get_member_profiles(db)
    members = db.query(FamilyMember).all()

    prompt_template = {
        "morning": MORNING_PROMPT,
        "noon": NOON_PROMPT,
        "evening": EVENING_PROMPT,
    }[period]

    try:
        # 获取老人画像
        elder_info = get_elder_info(db)
        elder_name = elder_info["elder_name"]
        elder_profile = elder_info["elder_profile"]
        system_prompt = SYSTEM_PERSONA.format(elder_profile=elder_profile, elder_name=elder_name)

        if period == "morning":
            prompt = prompt_template.format(
                elder_name=elder_name, weather=weather, calendar=calendar,
                member_profiles=member_profiles, today=today
            )
        elif period == "noon":
            prompt = prompt_template.format(
                elder_name=elder_name,
                member_profiles=member_profiles, today=today
            )
        else:
            tomorrow_calendar = await fetch_calendar_events(db, for_tomorrow=True)
            prompt = prompt_template.format(
                elder_name=elder_name,
                calendar=calendar,
                tomorrow_calendar=tomorrow_calendar,
                member_profiles=member_profiles, today=today
            )

        script = ""
        if os.environ.get("DEEPSEEK_API_KEY", ""):
            script = await call_deepseek_api(prompt, system=system_prompt)
        if not script and os.environ.get("ANTHROPIC_API_KEY", ""):
            script = await call_claude_api(prompt)
        if not script:
            script = FALLBACK_SCRIPTS[period].format(elder_name=elder_name)

        # 生成mention_timestamps
        timestamps_json = "[]"
        if members:
            ts_prompt = TIMESTAMPS_PROMPT.format(
                script=script,
                members=", ".join([f"{m.name}(id:{m.id})" for m in members])
            )
            ts_result = ""
            if os.environ.get("DEEPSEEK_API_KEY", ""):
                ts_result = await call_deepseek_api(ts_prompt, system="你是一个JSON输出助手，只输出合法JSON数组，不要加任何其他文字。", temperature=0.3, max_tokens=800)
            if not ts_result and os.environ.get("ANTHROPIC_API_KEY", ""):
                ts_result = await call_claude_api(ts_prompt)
            if ts_result:
                try:
                    parsed = json.loads(ts_result)
                    timestamps_json = json.dumps(parsed, ensure_ascii=False)
                except json.JSONDecodeError:
                    timestamps_json = "[]"

        # TTS
        audio_path = None
        voice_id = None
        for member in members:
            if member.voice_clone_id and not member.voice_clone_id.startswith("CLONE_FAIL:") and member.voice_clone_id != "cloning...":
                voice_id = member.voice_clone_id
                break
        audio_data = await synthesize_tts(script, voice_id)
        if audio_data:
            audio_filename = f"{today}_{period}.mp3"
            audio_file_path = os.path.join(AUDIO_DIR, audio_filename)
            with open(audio_file_path, "wb") as f:
                f.write(audio_data)
            audio_path = f"/static/audio/{audio_filename}"

        # 存为 draft 状态
        broadcast = db.query(DailyBroadcast).filter(
            DailyBroadcast.date == today,
            DailyBroadcast.period == period,
        ).first()

        if broadcast:
            broadcast.script = script
            broadcast.audio_path = audio_path
            broadcast.mention_timestamps = timestamps_json
            broadcast.status = "draft"
        else:
            broadcast = DailyBroadcast(
                date=today,
                period=period,
                script=script,
                audio_path=audio_path,
                mention_timestamps=timestamps_json,
                status="draft",
            )
            db.add(broadcast)

        db.commit()
        return {
            "success": True,
            "period": period,
            "status": "draft",
            "script": script,
            "has_audio": audio_path is not None,
        }

    except Exception as e:
        logger.error(f"重新生成{period}播报失败: {e}")
        return {"success": False, "period": period, "error": str(e)}
async def fetch_weather(db: Session = None) -> str:
    """获取当前实时天气，包含天气状态和截止时间。

    优先使用腾讯地图天气API（需配置QQ_MAP_KEY），否则使用wttr.in。
    城市从应用配置 weather_city 读取，由家属在配置页填写，默认北京。
    """
    city = "北京"
    if db is not None:
        city = get_config_value(db, "weather_city", "") or "北京"

    import httpx
    from urllib.parse import quote

    # 1. 优先尝试腾讯地图天气API（国内数据更准确）
    qq_key = os.environ.get("QQ_MAP_KEY", "")
    if qq_key:
        try:
            # 地理编码获取adcode
            geocode_url = f"https://apis.map.qq.com/ws/geocoder/v1/?address={quote(city)}&key={qq_key}"
            async with httpx.AsyncClient(timeout=12) as client:
                geo_resp = await client.get(geocode_url)
                if geo_resp.status_code == 200:
                    geo_data = geo_resp.json()
                    if geo_data.get("status") == 0:
                        adcode = geo_data["result"]["ad_info"]["adcode"]
                        # 获取实时天气
                        weather_url = f"https://apis.map.qq.com/ws/weather/v1?adcode={adcode}&key={qq_key}&type=now"
                        w_resp = await client.get(weather_url)
                        if w_resp.status_code == 200:
                            w_data = w_resp.json()
                            if w_data.get("status") == 0:
                                now_info = w_data["result"]["real"]
                                weather_desc = now_info.get("weather", "")
                                temp = now_info.get("temperature", "")
                                wind_dir = now_info.get("wind_direction", "")
                                wind_power = now_info.get("wind_power", "")
                                humidity = now_info.get("humidity", "")
                                # 构建播报文案
                                parts = [f"{city}当前{weather_desc}，气温{temp}℃"]
                                if wind_dir and wind_power:
                                    parts.append(f"{wind_dir}{wind_power}")
                                if humidity:
                                    parts.append(f"湿度{humidity}%")
                                # 截止时间
                                now_str = datetime.now().strftime("%H:%M")
                                cutoff = f"（截止{now_str}）"
                                temp_val = float(temp) if str(temp).replace(".", "").replace("-", "").isdigit() else 20
                                advice = _clothing_advice(temp_val)
                                return "，".join(parts) + f"。{cutoff}{advice}"
        except Exception as e:
            logger.warning(f"腾讯地图天气API调用失败，回退到wttr.in: {e}")

    # 2. 兜底：使用 wttr.in
    try:
        url = f"https://wttr.in/{quote(city)}?format=j1"
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.get(url, headers={"User-Agent": "curl/8.0"})
            if response.status_code == 200:
                data = response.json()
                cur = data["current_condition"][0]
                temp = float(cur["temp_C"])
                feels = cur["FeelsLikeC"]
                code = str(cur.get("weatherCode", ""))
                desc = _desc_to_zh(code, cur["weatherDesc"][0]["value"])
                # 截止时间：用当前时间
                now_str = datetime.now().strftime("%H:%M")
                cutoff = f"（截止{now_str}）"
                advice = _clothing_advice(temp)
                return f"{city}当前{desc}，气温{int(temp)}度，体感{feels}度。{cutoff}{advice}"
    except Exception as e:
        logger.error(f"天气API调用失败: {e}")

    return f"{city}当前天气未知，出门记得看一眼天气。"


# 中文星期 → repeat_days 索引（0=周一 ... 6=周日）
def _event_matches_today(ev, today) -> bool:
    """判断一条自定义事件今天是否生效"""
    rule = (ev.repeat_rule or "daily").lower()
    weekday = today.weekday()  # 0=周一 ... 6=周日
    if rule == "daily":
        return True
    if rule == "weekday":
        return weekday < 5  # 周一到周五
    if rule == "weekly":
        if not ev.repeat_days:
            return False
        days = [d.strip() for d in str(ev.repeat_days).split(",") if d.strip() != ""]
        return str(weekday) in days
    if rule == "once":
        return ev.event_date == today.isoformat()
    return False


async def fetch_calendar_events(db: Session = None, for_tomorrow: bool = False) -> str:
    """读取家属自定义的日程事件，返回今天（或明天）生效的事件描述。"""
    if db is None:
        return "没有特别安排。"
    try:
        from app.models.models import CustomEvent
        target_date = date.today()
        if for_tomorrow:
            from datetime import timedelta
            target_date = target_date + timedelta(days=1)
        events = db.query(CustomEvent).filter(CustomEvent.is_active == 1).all()
        todays = [e for e in events if _event_matches_today(e, target_date)]
        todays.sort(key=lambda e: e.event_time or "99:99")
        if todays:
            return "、".join([f"{e.event_time} {e.title}" for e in todays])
        day_label = "明天" if for_tomorrow else "今天"
        return f"{day_label}没有特别安排。"
    except Exception as e:
        logger.error(f"自定义事件读取失败: {e}")
    return "没有特别安排。"


def build_member_profile(m, elder_name: str = "老人") -> str:
    """把一位家人的所有信息组织成一段自然语言画像，供大模型构建温情内容。
    严格只输出真实存在的信息，缺失字段明确标注'未填写'，防止AI编造。"""
    parts = [f"· {m.relation}「{m.name}」"]
    details = []
    if getattr(m, "favorite_food", None) and m.favorite_food.strip():
        details.append(f"最爱吃{m.favorite_food}")
    else:
        details.append("最爱吃什么：[未填写，不可提及任何食物偏好]")
    if getattr(m, "favorite_color", None) and m.favorite_color.strip():
        details.append(f"最喜欢{m.favorite_color}")
    else:
        details.append("最喜欢的颜色：[未填写，不可提及任何颜色偏好]")
    if getattr(m, "personality", None) and m.personality.strip():
        details.append(f"性格{m.personality}")
    else:
        details.append("性格特点：[未填写，不可编造性格]")
    if getattr(m, "catchphrase", None) and m.catchphrase.strip():
        details.append(f"常把「{m.catchphrase}」挂在嘴边")
    else:
        details.append("口头禅：[未填写，不可编造任何口头禅]")
    if details:
        parts.append("，".join(details) + "。")
    if getattr(m, "special_memory", None) and m.special_memory.strip():
        parts.append(f"和{elder_name}最难忘的一件事：{m.special_memory}。")
    else:
        parts.append(f"和{elder_name}最难忘的事：[未填写，不可编造任何具体场景或回忆]。")
    if m.memory_snippets and m.memory_snippets.strip():
        parts.append(f"其它共同回忆：{m.memory_snippets}。")
    return "".join(parts)


def build_elder_profile(elder) -> str:
    """把老人的身份画像组织成自然语言描述，供大模型理解收听者情况。"""
    if not elder:
        return "老人信息未填写，默认为一位有记忆障碍的长辈，称呼为「老人」。请用最简单温柔的口语沟通。"
    parts = []
    # 称呼
    name = elder.name.strip() if elder.name else "老人"
    parts.append(f"称呼：{name}")
    # 年龄和性别
    if elder.age:
        parts.append(f"年龄：{elder.age}岁")
    if elder.gender:
        parts.append(f"性别：{elder.gender}")
    # 学历
    if elder.education and elder.education.strip():
        parts.append(f"学历：{elder.education.strip()}")
    # 症状表现
    if elder.symptoms and elder.symptoms.strip():
        parts.append(f"症状表现：{elder.symptoms.strip()}")
    else:
        parts.append("症状表现：[未填写，不可编造任何症状]")
    # 症状程度
    if elder.severity and elder.severity.strip():
        parts.append(f"症状程度：{elder.severity.strip()}")
    else:
        parts.append("症状程度：[未填写，按轻度处理]")
    # 家属最担心什么
    if elder.worries and elder.worries.strip():
        parts.append(f"家属最担心：{elder.worries.strip()}")
    else:
        parts.append("家属最担心：[未填写，不可编造担忧]")
    return "\n".join(parts)


def get_elder_info(db: Session) -> dict:
    """获取老人画像信息，返回 {elder_name, elder_profile_text, elder_obj}。"""
    elder = db.query(Elder).first()
    elder_name = elder.name.strip() if elder and elder.name and elder.name.strip() else "老人"
    elder_profile_text = build_elder_profile(elder)
    return {"elder_name": elder_name, "elder_profile": elder_profile_text, "elder_obj": elder}


def get_member_profiles(db: Session, elder_name: str = "老人") -> str:
    """获取所有家人的完整画像描述（用于喂给大模型）。"""
    members = db.query(FamilyMember).all()
    if not members:
        return "暂无家人信息"
    return "\n".join(build_member_profile(m, elder_name) for m in members)


def get_member_memories(db: Session, elder_name: str = "老人") -> str:
    """获取所有家人的记忆素材（兼容旧调用）。"""
    return get_member_profiles(db, elder_name)


async def generate_daily_broadcasts(db: Session, force: bool = False) -> dict:
    """每日Pipeline——按家人并行生成，每生成一人立即推送"""
    today = date.today().isoformat()
    logger.info(f"开始生成 {today} 的播报内容（按人定制+流式推送）...")

    # 检查是否已生成
    existing = db.query(DailyBroadcast).filter(DailyBroadcast.date == today).all()
    if existing and not force:
        generated = [b for b in existing if b.status == "generated"]
        if len(generated) >= 3:
            return {"status": "already_exists", "message": f"{today}的播报已生成"}

    # ★ 预先获取所有共享数据
    weather = await fetch_weather(db)
    calendar = await fetch_calendar_events(db)
    tomorrow_calendar = await fetch_calendar_events(db, for_tomorrow=True)
    elder_info = get_elder_info(db)
    elder_name = elder_info["elder_name"]
    elder_profile = elder_info["elder_profile"]
    members = db.query(FamilyMember).all()
    system_prompt = SYSTEM_PERSONA.format(elder_profile=elder_profile, elder_name=elder_name)

    results = {"date": today, "periods": {}}
    period_labels = {"morning": "早晨", "noon": "午间", "evening": "晚上"}

    import asyncio

    for period in ["morning", "noon", "evening"]:
        try:
            period_label = period_labels[period]
            # ★ 并行为每个家人生成独立文案
            async def _gen_member_script(member: FamilyMember) -> dict:
                """生成单个家人的播报文案"""
                member_profile = build_member_profile(member, elder_name)
                if period == "noon":
                    prompt = MEMBER_INTRO_PROMPT.format(
                        elder_name=elder_name, elder_profile=elder_profile,
                        member_name=member.name, member_relation=member.relation,
                        member_profile=member_profile, today=today
                    )
                else:
                    prompt = MEMBER_GREETING_PROMPT.format(
                        period_label=period_label, elder_name=elder_name,
                        elder_profile=elder_profile,
                        member_name=member.name, member_relation=member.relation,
                        member_profile=member_profile,
                        weather=weather, calendar=calendar if period == "morning" else (tomorrow_calendar if period == "evening" else ""),
                        today=today
                    )

                script = ""
                if os.environ.get("DEEPSEEK_API_KEY", ""):
                    script = await call_deepseek_api(prompt, system=system_prompt, max_tokens=400)
                if not script and os.environ.get("ANTHROPIC_API_KEY", ""):
                    script = await call_claude_api(prompt)
                if not script:
                    script = f"{elder_name}，我是{member.name}，你的{member.relation}。我一直都在想你。{elder_name}，点一下这个头像，看看我长什么样子"

                return {"member_id": member.id, "member_name": member.name, "script": script}

            # ★ 并行生成所有家人的文案
            if members:
                member_tasks = [_gen_member_script(m) for m in members]
                member_results = await asyncio.gather(*member_tasks, return_exceptions=True)
            else:
                member_results = []

            # 组装完整文稿：各家人文案按顺序拼接
            valid_members = []
            for r in member_results:
                if isinstance(r, Exception):
                    logger.error(f"生成家人文案失败: {r}")
                    continue
                valid_members.append(r)

            # 按成员顺序拼接完整文稿，每人一段，用换行分隔
            full_script_parts = []
            for mr in valid_members:
                full_script_parts.append(mr["script"])
            full_script = "\n".join(full_script_parts)

            if not full_script:
                full_script = FALLBACK_SCRIPTS[period].format(elder_name=elder_name)

            # 生成mention_timestamps（基于拼接后的文稿）
            timestamps_json = "[]"
            if valid_members:
                # 根据每人文案的字符数估算时间戳
                ts_list = []
                cum_sec = 0.0
                for mr in valid_members:
                    mid = mr["member_id"]
                    mname = mr["member_name"]
                    script_len = len(mr["script"])
                    dur = script_len * 0.3  # 每字约0.3秒
                    ts_list.append({
                        "member_id": mid,
                        "name": mname,
                        "start_sec": round(cum_sec, 1),
                        "end_sec": round(cum_sec + dur, 1),
                        "excerpt": mr["script"][:30],
                    })
                    cum_sec += dur + 0.8  # 段间停顿0.8秒
                timestamps_json = json.dumps(ts_list, ensure_ascii=False)

            # TTS合成 —— ★ 每个成员使用各自专属音色分别合成，再拼接
            audio_path = None
            member_audio_segments = []
            for mr in valid_members:
                mid = mr["member_id"]
                script_text = mr["script"]
                # 找到该成员的专属音色
                member_obj = next((m for m in members if m.id == mid), None)
                member_voice = member_obj.voice_clone_id if member_obj and member_obj.voice_clone_id else None
                segment_data = await synthesize_tts(script_text, member_voice)
                if segment_data:
                    member_audio_segments.append(segment_data)
                else:
                    logger.warning(f"成员{mr['member_name']}(id:{mid})的TTS合成失败")

            # 拼接所有成员的音频段
            if member_audio_segments:
                combined_audio = await concat_audio_segments(member_audio_segments)
                if combined_audio:
                    audio_filename = f"{today}_{period}.mp3"
                    audio_file_path = os.path.join(AUDIO_DIR, audio_filename)
                    with open(audio_file_path, "wb") as f:
                        f.write(combined_audio)
                    audio_path = f"/static/audio/{audio_filename}"

            # 存入数据库
            broadcast = db.query(DailyBroadcast).filter(
                DailyBroadcast.date == today,
                DailyBroadcast.period == period,
            ).first()

            # 构建member_scripts字段（按人独立文案JSON）
            member_scripts_json = json.dumps(
                {str(mr["member_id"]): mr["script"] for mr in valid_members},
                ensure_ascii=False
            )

            if broadcast:
                broadcast.script = full_script
                broadcast.audio_path = audio_path
                broadcast.mention_timestamps = timestamps_json
                broadcast.member_scripts = member_scripts_json
                broadcast.status = "draft"
            else:
                broadcast = DailyBroadcast(
                    date=today,
                    period=period,
                    script=full_script,
                    audio_path=audio_path,
                    mention_timestamps=timestamps_json,
                    member_scripts=member_scripts_json,
                    status="draft",
                )
                db.add(broadcast)

            db.commit()  # ★ 立即提交，不等其他时段

            results["periods"][period] = {
                "status": "draft",
                "script_length": len(full_script),
                "has_audio": audio_path is not None,
                "member_count": len(valid_members),
            }

            # ★ 流式推送：这个时段生成完就立即推送
            try:
                from app.main import websocket_manager
                ts_list_for_push = json.loads(timestamps_json) if timestamps_json != "[]" else []
                payload = {
                    "event": "play_broadcast",
                    "period": period,
                    "script": full_script,
                    "audio_url": audio_path,
                    "timestamps": ts_list_for_push,
                    "member_scripts": {str(mr["member_id"]): mr["script"] for mr in valid_members},
                }
                await websocket_manager.broadcast(payload)
                logger.info(f"已流式推送 {period} 时段")
            except Exception as e:
                logger.warning(f"流式推送 {period} 失败: {e}")

        except Exception as e:
            logger.error(f"生成{period}播报失败: {e}")
            broadcast = db.query(DailyBroadcast).filter(
                DailyBroadcast.date == today,
                DailyBroadcast.period == period,
            ).first()
            if not broadcast:
                db.add(DailyBroadcast(
                    date=today, period=period,
                    script=FALLBACK_SCRIPTS[period].format(elder_name=elder_name),
                    status="failed",
                ))
                db.commit()
            results["periods"][period] = {"status": "failed", "error": str(e)}

    logger.info(f"{today} 播报生成完成: {list(results['periods'].keys())}")
    return results


async def generate_demo_broadcasts(db: Session):
    """生成演示用的播报数据（不需要真实API调用）"""
    today = date.today().isoformat()

    # 获取老人身份画像
    elder_info = get_elder_info(db)
    elder_name = elder_info["elder_name"]

    demo_scripts = {
        "morning": f"{elder_name}，早上好，我是小红。今天晴天十八度，记得加件薄外套。上午护士来量血压，你配合一下哦。晚上我来看你，给你带红烧肉。等我哦。",
        "noon": f"{elder_name}，你还记得我吗？我是小红，你的孙女呀。我在城东开花店，你最爱的桂花就是我种的。你最喜欢吃我做的红烧肉，还记得那个味道吗？每次你都说，小红做的菜最好吃。",
        "evening": f"{elder_name}，今天你吃了红烧肉，还睡了午觉，真棒。明天小明说要来看你，带你最爱的桂花糕。老王也会给你念报纸。早点睡，我们都爱你，晚安。",
    }

    demo_timestamps = {
        "morning": json.dumps([
            {"member_id": 1, "name": "小红", "start_sec": 2, "end_sec": 8, "excerpt": "我是小红"},
            {"member_id": 1, "name": "小红", "start_sec": 28, "end_sec": 38, "excerpt": "晚上我来看你"},
        ], ensure_ascii=False),
        "noon": json.dumps([
            {"member_id": 1, "name": "小红", "start_sec": 3, "end_sec": 15, "excerpt": "我是小红，你的女儿"},
            {"member_id": 1, "name": "小红", "start_sec": 30, "end_sec": 42, "excerpt": "你最喜欢吃我做的红烧肉"},
        ], ensure_ascii=False),
        "evening": json.dumps([
            {"member_id": 1, "name": "小红", "start_sec": 2, "end_sec": 6, "excerpt": "吃了红烧肉"},
            {"member_id": 2, "name": "小明", "start_sec": 16, "end_sec": 26, "excerpt": "小明说要来看你"},
            {"member_id": 3, "name": "老王", "start_sec": 30, "end_sec": 38, "excerpt": "老王也会给你念报纸"},
        ], ensure_ascii=False),
    }

    for period in ["morning", "noon", "evening"]:
        existing = db.query(DailyBroadcast).filter(
            DailyBroadcast.date == today,
            DailyBroadcast.period == period,
        ).first()

        audio_path = None
        # 尝试使用 TTS 生成实际可播放的音频（按 TTS_PROVIDER 选择服务商）
        audio_data = await synthesize_tts(demo_scripts[period], None)
        if audio_data:
            audio_filename = f"demo_{period}.mp3"
            audio_file_path = os.path.join(AUDIO_DIR, audio_filename)
            with open(audio_file_path, "wb") as f:
                f.write(audio_data)
            audio_path = f"/static/audio/{audio_filename}"
        else:
            # TTS失败时仍设置路径（前端会回退到浏览器语音合成）
            logger.warning(f"Demo TTS生成失败({period})，音频将不可用")

        if existing:
            existing.script = demo_scripts[period]
            existing.audio_path = audio_path
            existing.mention_timestamps = demo_timestamps[period]
            existing.status = "approved"
        else:
            db.add(DailyBroadcast(
                date=today,
                period=period,
                script=demo_scripts[period],
                audio_path=audio_path,
                mention_timestamps=demo_timestamps[period],
                status="approved",
            ))

    db.commit()
