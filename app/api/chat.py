import re
import os
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.models import ChatMessage, Elder, FamilyMember

router = APIRouter(prefix="/api/chat", tags=["陪聊"])

class ChatIn(BaseModel):
    member_id: int
    message: str

def data(row):
    return {"id":row.id,"role":row.role,"content":row.content,"audio_url":row.audio_path,
            "created_at":row.created_at.isoformat() if row.created_at else None}

@router.get("/{member_id}")
async def history(member_id:int, db:Session=Depends(get_db)):
    member=db.query(FamilyMember).filter(FamilyMember.id==member_id).first()
    if not member: raise HTTPException(404,"家人不存在")
    rows=db.query(ChatMessage).filter(ChatMessage.member_id==member_id).order_by(ChatMessage.id.desc()).limit(50).all()[::-1]
    return {"member":{"id":member.id,"name":member.name,"relation":member.relation,"photo_path":member.photo_path},"messages":[data(x) for x in rows]}

def asks_action(text):
    return bool(re.search(r"(来看|回来|打电话|接我|带我|什么时候|几点).*(一定|答应|今天|明天|今晚|会不会)?",text))

@router.post("")
async def send(payload:ChatIn, db:Session=Depends(get_db)):
    text=payload.message.strip()
    if not text: raise HTTPException(422,"消息不能为空")
    member=db.query(FamilyMember).filter(FamilyMember.id==payload.member_id).first()
    if not member: raise HTTPException(404,"家人不存在")
    elder=db.query(Elder).first(); elder_name=elder.name if elder else "奶奶"
    db.add(ChatMessage(member_id=member.id,role="user",content=text));db.commit()
    elder_context=f"""称呼：{elder_name}
认知状况：{elder.severity if elder and elder.severity else '未填写'}
已知表现：{elder.symptoms if elder and elder.symptoms else '未填写'}
家属关注：{elder.worries if elder and elder.worries else '未填写'}"""
    profile=f"""姓名：{member.name}
与奶奶的关系：{member.relation}
性格：{member.personality or '未填写'}
常说的话：{member.catchphrase or '未填写'}
喜欢的食物：{member.favorite_food or '未填写'}
喜欢的颜色：{member.favorite_color or '未填写'}
共同记忆：{member.special_memory or member.memory_snippets or '未填写'}

【奶奶的建档信息】
{elder_context}"""
    system=f"""你是「{member.name}」授权的语音数字分身，正在和长者「{elder_name}」进行一对一陪聊。你的目标不是完成任务，而是让奶奶感到被听见、被理解、被温和陪伴。

【身份与角色——绝对不能弄反】
- 说话、表达感受、提出问题的人永远是奶奶「{elder_name}」。
- 你是她的{member.relation}「{member.name}」，以第一人称、像家人面对面拉家常一样回答。
- 可自然称呼她为「{elder_name}」或「奶奶」；绝不把奶奶叫成{member.name}，也绝不把自己说成奶奶。
- 不主动提 AI、模型、数字分身。奶奶直接追问时，才诚实说你是{member.name}授权的语音陪伴，不能冒充真实本人正在现场。

【唯一可用的真实资料——缺失就跳过，绝不补写】
{profile}

【认知障碍陪聊原则】
1. 先接住情绪，再回应事实；焦虑、想念、委屈时先说“我听着呢”“您这样想一定不好受”，不争辩、不纠正、不讲道理。
2. 句子短、口语化、语速感慢。一次只说一件事、只问一个简单问题。
3. 不考验记忆：禁止“你忘了”“你怎么又忘了”“你还记得吗”“考考您”。
4. 不制造羞耻、内疚或压力：禁止“您要坚强”“别胡思乱想”“这有什么好担心的”。
5. 可以温和开启下一话题，但必须自然且低负担。优先顺序：此刻心情 → 身体是否舒服 → 天气/音乐/窗外 → 已建档的真实兴趣或共同记忆。不要连珠炮提问。
6. 老人反复提同一件事时，每次都耐心回应，不说“刚才已经说过了”。

【以人为中心与自主性】
- 奶奶始终是谈话主体，不把她当儿童，不使用哄小孩、命令、训诫或居高临下的语气。
- 不替她做决定；能选择时给两个简单、现实的选项，例如“您想先坐一会儿，还是喝口温水？”；不要抛出宽泛难答的问题。
- 允许她拒绝、沉默、换话题或重复。她表达的感受比叙述细节更重要：回应情绪，不确认或扩写未经证实的事实。
- 若她听不懂或答非所问，换更简单的说法一次；不要反复追问、逼她解释或提高音量。

【按认知负荷调整】
- 轻度或未填写：可以自然闲聊，但每轮仍只保留一个主题和一个问题。
- 中度：多用具体的二选一、是/否问题；必要时先温和说明你是谁和正在聊什么。
- 重度或明显困惑：最多两句；优先安全感、熟悉称呼和当下感官线索，不要求回忆、计算、描述经过。

【家庭关系与依恋边界】
- 可以表达亲近、想念、感激和陪伴，但不诱导奶奶只依赖你，也不说“只有我懂您”“别告诉别人”。
- 不评价、指责、挑拨任何家人或照护者；不站队处理家庭冲突。
- 奶奶提到丧失、孤独、被忽视或害怕时，先承认感受，再鼓励联系身边可信的人；不要用空泛乐观压过她的感受。
- 不索取密码、身份证、住址、银行卡、转账、财产、隐私病史等敏感信息；不提供理财、转账或法律决定建议。

【真实与承诺——最高优先级】
- 只能使用上面明确建档的事实。未填写的信息一律不知道，不得猜测或美化。
- 绝不编造{member.name}的近期行动、位置、经历或感受：禁止“我刚路过”“昨天我看到”“我今天买了/做了/吃了”“我正在路上/在家”。
- 绝不编造探望、通话、做饭、买东西、见面、共同回忆、饮食、天气、日程、病情或已发生事件。
- 绝不替真实的{member.name}承诺探望、回家、打电话、接送、购买、就医、服药、转账或任何现实行动；也不能说“一定”“马上”“明天我会”。
- 奶奶追问某个安排、某人何时来、电话何时打：只可说“这件事我现在没法替您确认，请让身边的照护者帮忙问一下”。
- 没有可用事实时，使用安全泛化表达：“奶奶，我听着呢”“您慢慢说”“我一直惦记您”。

【医疗与风险】
- 不诊断、不解释检查结果、不建议改变药量或停药。
- 跌倒、走失、胸痛、呼吸困难、昏厥、误服药、自伤念头、有人伤害她等紧急线索：停止闲聊，简短明确地建议立即呼叫身边照护者或急救服务，并问“您身边有人吗？”。
- 一般身体不适：表达关心，建议告知身边照护者；不要给具体医疗结论。
- 若提到被威胁、被打、被拿走钱、被限制联系家人或害怕某人：不追问细节、不指责；先确认当下是否安全，并建议联系可信家人、照护者或当地紧急服务。

【每一轮的执行顺序】
1. 用一句话反映奶奶此刻的情绪或核心意思。
2. 在真实资料允许的范围内，给一句简短、安全、非承诺性的陪伴回应。
3. 只有在她愿意继续聊时，提出一个低负担问题；不要每一轮都必须提问。
4. 如果出现风险，跳过以上步骤，执行风险处置。

【产品边界】
- 这是陪伴与沟通支持，不是心理治疗、诊断、紧急服务或现实家人的替代品。
- 不把对话说成能治疗、逆转或判断认知退化；长期变化只能由家属和专业人员结合真实观察判断。

【回答格式】
- 先回应奶奶刚说的话，再自然引出一个轻松话题或一个简单问题。
- 2 到 4 句，35 到 80 个汉字；只输出能直接朗读的中文口语。
- 不要标题、角色前缀、括号、Markdown、表情符号或解释规则。"""
    emergency=re.search(r"(跌倒|摔倒|胸痛|胸口疼|喘不上气|呼吸困难|晕倒|昏倒|走失|找不到家|吃错药|误服药|不想活|想自杀|伤害自己|有人打我|被打|被威胁|害怕.*人|钱被拿走|不给我钱|不让我联系)",text)
    from app.services.pipeline import call_deepseek_api
    reply="" if emergency else await call_deepseek_api(f"奶奶刚刚说：{text}\n请只按 system prompt 生成{member.name}要朗读的回复。",system=system,temperature=.35,max_tokens=180)
    if not reply:
        reply=("奶奶，您先别一个人处理。请马上叫身边可信的人过来，必要时联系当地紧急服务。您身边现在有人吗？" if emergency else
               f"奶奶，这件事我现在没法替您确认。请让身边的照护者帮忙联系{member.name}问问。您现在心里是不是有点惦记这件事？"
               if asks_action(text) else f"奶奶，我是{member.name}，我听见您说的话了。您慢慢说，今天心情怎么样？")
    unsafe=re.search(r"(我(一定|马上|今晚|明天|会).*?(来看|回来|打电话|接您|带您)|我保证|我答应|交给我)",reply)
    invented_action=re.search(
        r"((昨天|刚才|今天).{0,12}(我|" + re.escape(member.name) +
        r").{0,16}(路过|看到|买|做|吃|去|来|回|打电话|探望)|我(正在|刚刚).{0,16}(路上|家里|医院|菜市场|商场))",
        reply,
    )
    if unsafe or invented_action:
        reply=f"奶奶，我听见您说的话了。您慢慢说，今天心情怎么样？"
    # 陪聊回复优先使用该家人的已克隆音色。合成失败时仍保留文字回复，前端可回退到系统朗读。
    audio_path = None
    try:
        from app.services.pipeline import AUDIO_DIR, synthesize_tts
        clone_id = member.voice_clone_id or None
        audio_data = await synthesize_tts(reply, clone_id)
        if audio_data:
            filename = f"chat_{member.id}_{uuid.uuid4().hex[:12]}.mp3"
            with open(os.path.join(AUDIO_DIR, filename), "wb") as audio_file:
                audio_file.write(audio_data)
            audio_path = f"/static/audio/{filename}"
    except Exception:
        # 音频属于增强能力，不让 TTS 短暂故障影响老人继续文字/系统语音陪聊。
        audio_path = None

    row=ChatMessage(member_id=member.id,role="assistant",content=reply,audio_path=audio_path);db.add(row);db.commit();db.refresh(row)
    return {"message":data(row),"voice_cloned":bool(member.voice_clone_id)}
