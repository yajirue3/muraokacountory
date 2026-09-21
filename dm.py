import os
import json
import re
from datetime import datetime
from fastapi import APIRouter, Header, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from pywebpush import webpush, WebPushException
from db import get_supabase

router = APIRouter()

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "YOUR_PUBLIC_KEY_HERE")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "YOUR_PRIVATE_KEY_HERE")
VAPID_CLAIMS = {"sub": "mailto:admin@example.com"}

class DMIDUpdate(BaseModel):
    dm_id: str = Field(..., min_length=3, max_length=20)

class DMRequest(BaseModel):
    target_dm_id: str
    content: str = Field(..., min_length=1, max_length=500)

class PushSubscription(BaseModel):
    endpoint: str
    p256dh: str
    auth: str

async def get_user_auth(authorization: str):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="認証エラー")
    client = await get_supabase()
    try:
        res = await client.auth.get_user(authorization.split(" ")[1])
        return res.user
    except Exception:
        raise HTTPException(status_code=401, detail="トークン無効")

async def is_king_user(user_id: str):
    client = await get_supabase()
    res = await client.table("profiles").select("role").eq("id", user_id).execute()
    return bool(res.data and res.data[0].get("role") == "king")

async def send_web_push(receiver_id: str, sender_name: str, message_text: str):
    if VAPID_PRIVATE_KEY == "YOUR_PRIVATE_KEY_HERE": return
    client = await get_supabase()
    subs = await client.table("push_subscriptions").select("*").eq("user_id", receiver_id).execute()
    if not subs.data: return
    
    payload = {"title": f"{sender_name}からのDM", "body": message_text[:50], "url": "/dm"}
    for sub in subs.data:
        try:
            webpush({"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                    json.dumps(payload), vapid_private_key=VAPID_PRIVATE_KEY, vapid_claims=VAPID_CLAIMS)
        except WebPushException as e:
            if e.response and e.response.status_code == 410:
                await client.table("push_subscriptions").delete().eq("id", sub["id"]).execute()

@router.get("/me")
async def get_my_dm_info(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    res = await client.table("profiles").select("dm_id, role").eq("id", user.id).execute()
    return res.data[0] if res.data else {"dm_id": None, "role": "user"}

@router.post("/set-id")
async def set_dm_id(data: DMIDUpdate, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    if not re.match(r"^[a-zA-Z0-9_]+$", data.dm_id):
        raise HTTPException(status_code=400, detail="英数字とアンダースコアのみ使用可能")
    client = await get_supabase()
    exist = await client.table("profiles").select("id").eq("dm_id", data.dm_id).neq("id", user.id).execute()
    if exist.data: raise HTTPException(status_code=400, detail="そのIDは既に使用されています")
    await client.table("profiles").update({"dm_id": data.dm_id}).eq("id", user.id).execute()
    return {"message": f"DM IDを @{data.dm_id} に設定しました"}

@router.get("/vapid-public-key")
async def get_vapid(): return {"public_key": VAPID_PUBLIC_KEY}

@router.post("/subscribe")
async def subscribe_push(sub: PushSubscription, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    exist = await client.table("push_subscriptions").select("id").eq("endpoint", sub.endpoint).execute()
    if exist.data:
        await client.table("push_subscriptions").update({"user_id": user.id, "p256dh": sub.p256dh, "auth": sub.auth}).eq("id", exist.data[0]["id"]).execute()
    else:
        await client.table("push_subscriptions").insert({"user_id": user.id, "endpoint": sub.endpoint, "p256dh": sub.p256dh, "auth": sub.auth}).execute()
    return {"message": "通知ON"}

@router.post("/send")
async def send_dm(data: DMRequest, bt: BackgroundTasks, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    target = await client.table("profiles").select("id").eq("dm_id", data.target_dm_id).execute()
    if not target.data: raise HTTPException(status_code=404, detail="指定されたDM IDが存在しません")
    target_id = target.data[0]["id"]
    if user.id == target_id: raise HTTPException(status_code=400, detail="自分には送信できません")

    res = await client.table("direct_messages").insert({"sender_id": user.id, "receiver_id": target_id, "content": data.content.strip()}).execute()
    prof = await client.table("profiles").select("nickname").eq("id", user.id).execute()
    bt.add_task(send_web_push, target_id, prof.data[0]["nickname"] if prof.data else "不明", data.content.strip())
    return {"message": "送信完了"}

@router.get("/conversations")
async def get_conversations(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    msgs = await client.table("direct_messages").select(
        "sender_id, receiver_id, content, is_read, created_at, sender:sender_id(nickname, dm_id), receiver:receiver_id(nickname, dm_id)"
    ).or_(f"sender_id.eq.{user.id},receiver_id.eq.{user.id}").order("created_at", desc=True).execute()

    convs = {}
    for msg in (msgs.data or []):
        is_sender = (msg["sender_id"] == user.id)
        pid = msg["receiver_id"] if is_sender else msg["sender_id"]
        if pid not in convs:
            p_data = msg["receiver"] if is_sender else msg["sender"]
            convs[pid] = {"partner_dm_id": p_data.get("dm_id"), "partner_name": p_data.get("nickname"), "latest_message": msg["content"], "unread": 0}
        if not is_sender and not msg["is_read"]: convs[pid]["unread"] += 1
    return {"conversations": list(convs.values())}

@router.get("/messages/{partner_dm_id}")
async def get_messages(partner_dm_id: str, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    target = await client.table("profiles").select("id").eq("dm_id", partner_dm_id).execute()
    if not target.data: return {"messages": []}
    pid = target.data[0]["id"]
    await client.table("direct_messages").update({"is_read": True}).eq("sender_id", pid).eq("receiver_id", user.id).eq("is_read", False).execute()
    res = await client.table("direct_messages").select("sender_id, content, created_at").or_(
        f"and(sender_id.eq.{user.id},receiver_id.eq.{pid}),and(sender_id.eq.{pid},receiver_id.eq.{user.id})"
    ).order("created_at", desc=False).limit(200).execute()
    return {"messages": res.data or [], "partner_id": pid}
