import secrets
from datetime import datetime,timedelta
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.models import PairingCode,CompanionDevice,Elder
router=APIRouter(prefix="/api/device",tags=["设备"])
class BindIn(BaseModel):
    code:str;device_token:str;device_name:str="老人陪伴设备"
@router.post("/invite")
async def invite(db:Session=Depends(get_db)):
    now=datetime.now()
    for x in db.query(PairingCode).filter(PairingCode.used_at.is_(None)).all(): x.used_at=now
    code=f"{secrets.randbelow(1000000):06d}";row=PairingCode(code=code,expires_at=now+timedelta(minutes=10));db.add(row);db.commit()
    return {"code":code,"expires_in":600}
@router.post("/bind")
async def bind(p:BindIn,db:Session=Depends(get_db)):
    now=datetime.now();c=db.query(PairingCode).filter(PairingCode.code==p.code).first()
    if not c or c.used_at or c.expires_at<=now: raise HTTPException(422,"邀请码无效或已过期")
    elder=db.query(Elder).first();d=CompanionDevice(device_token=p.device_token,device_name=p.device_name,elder_id=elder.id if elder else None,bound_at=now,last_seen_at=now)
    db.add(d);c.used_at=now;db.commit();return {"success":True,"elder_name":elder.name if elder else "长者"}
@router.get("/status/{token}")
async def status(token:str,db:Session=Depends(get_db)):
    d=db.query(CompanionDevice).filter(CompanionDevice.device_token==token,CompanionDevice.is_active==1).first()
    if not d:return {"bound":False}
    d.last_seen_at=datetime.now();db.commit();e=db.query(Elder).first()
    return {"bound":True,"device_name":d.device_name,"elder_name":e.name if e else "长者"}
@router.get("/list")
async def devices(db:Session=Depends(get_db)):
    xs=db.query(CompanionDevice).filter(CompanionDevice.is_active==1).all()
    return {"devices":[{"id":x.id,"device_name":x.device_name,"last_seen_at":x.last_seen_at.isoformat() if x.last_seen_at else None} for x in xs]}
