from collections import defaultdict
from datetime import date,datetime,timedelta
from fastapi import APIRouter,Depends,Query
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.models import ChatMessage,FamilyMember
router=APIRouter(prefix="/api/insights",tags=["日报"])
@router.get("")
async def insights(days:int=Query(14,ge=7,le=90),db:Session=Depends(get_db)):
    rows=db.query(ChatMessage).filter(ChatMessage.created_at>=datetime.now()-timedelta(days=days)).all()
    g=defaultdict(list)
    for r in rows:g[r.created_at.date().isoformat()].append(r)
    daily=[]
    for i in range(days-1,-1,-1):
        day=(date.today()-timedelta(days=i)).isoformat();us=[x for x in g[day] if x.role=="user"];text=" ".join(x.content for x in us)
        topics=[n for n,ws in {"家人":["小红","家人","来看"],"日常":["吃","散步","天气","睡"],"时间":["今天","明天","几点","星期"],"健康":["药","血压","疼","头晕"]}.items() if any(w in text for w in ws)]
        signals=sum(text.count(w) for w in ["几点","星期几","你是谁","我在哪","什么时候"])
        mood="焦虑" if any(w in text for w in ["担心","害怕","着急"]) else "平稳"
        daily.append({"date":day,"message_count":len(us),"topics":topics,"mood":mood,"signals":signals})
    t=daily[-1];names={m.id:m.name for m in db.query(FamilyMember).all()};companions=sorted({names[x.member_id] for x in g[date.today().isoformat()] if x.member_id in names})
    t["summary"]=f"今天与{'、'.join(companions) or '数字家人'}聊了{t['message_count']}轮，主要谈到{'、'.join(t['topics']) or '日常陪伴'}。整体情绪{t['mood']}。"
    active=[x for x in daily if x["message_count"]];label="数据积累中" if len(active)<4 else ("近期需关注" if sum(x["signals"] for x in active[-3:])>sum(x["signals"] for x in active[:-3]) else "变化不明显")
    return {"today":t,"trend":{"label":label,"days_with_data":len(active)},"daily":daily,"disclaimer":"来自陪聊文本的观察信号，不能用于诊断或判断疾病进展；如持续担忧，请咨询专业医生。"}
