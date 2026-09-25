import os, json, re, traceback
from typing import Optional, List, Dict, Any
from datetime import datetime
from fastapi import APIRouter, Header, HTTPException, BackgroundTasks, UploadFile, File
from pydantic import BaseModel, Field
import httpx
from pywebpush import webpush, WebPushException
from db import get_supabase

router = APIRouter()

# --- Settings ---
CLIENT_ID = os.environ.get("GDRIVE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GDRIVE_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("GDRIVE_REFRESH_TOKEN", "")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "YOUR_PUBLIC_KEY_HERE")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "YOUR_PRIVATE_KEY_HERE")
VAPID_CLAIMS = {"sub": "mailto:admin@example.com"}

http_client = httpx.AsyncClient(timeout=60.0)

# --- Models ---
class DMIDUpdate(BaseModel): dm_id: str = Field(..., min_length=3, max_length=20)
class PushSubscription(BaseModel): endpoint: str; p256dh: str; auth: str
class SendMsgReq(BaseModel):
    target_dm_id: Optional[str] = None
    room_id: Optional[int] = None
    message_type: str = "TEXT"
    content: str
    metadata: Dict[str, Any] = {}
class GroupReq(BaseModel): room_name: str; member_dm_ids: List[str]
class ReactReq(BaseModel): reaction_type: str
class TransferReq(BaseModel):
    target_user_id: str; sender_wallet_id: str; amount: int; room_id: Optional[int] = None

# --- Helpers ---
async def get_user_auth(authorization: str):
    if not authorization or not authorization.startswith("Bearer "): raise HTTPException(401, "認証エラー")
    try:
        res = await (await get_supabase()).auth.get_user(authorization.split(" ")[1])
        return res.user
    except: raise HTTPException(401, "無効なトークン")

async def is_king(user_id: str):
    res = await (await get_supabase()).table("profiles").select("role").eq("id", user_id).execute()
    return bool(res.data and res.data[0].get("role") == "king")

async def upload_image_to_drive(file_obj, filename, mime_type, file_size):
    # (既存のGoogle Driveアップロード処理。長さを抑えるため省略せずそのまま記述しますが、同一ロジックです)
    res = await http_client.post("https://oauth2.googleapis.com/token", data={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "refresh_token": REFRESH_TOKEN, "grant_type": "refresh_token"})
    token = res.json().get("access_token")
    init_res = await http_client.post("https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable", headers={"Authorization": f"Bearer {token}", "X-Upload-Content-Type": mime_type, "X-Upload-Content-Length": str(file_size)}, json={"name": filename})
    session_url = init_res.headers.get("Location")
    file_obj.seek(0)
    upload_res = await http_client.put(session_url, headers={"Content-Length": str(file_size)}, content=file_obj.read())
    file_id = upload_res.json().get("id")
    await http_client.post(f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions", headers={"Authorization": f"Bearer {token}"}, json={"role": "reader", "type": "anyone"})
    return file_id

# --- APIs ---
@router.get("/me")
async def get_me(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    res = await (await get_supabase()).table("profiles").select("id, dm_id, nickname, avatar_drive_id, role").eq("id", user.id).execute()
    return res.data[0] if res.data else {"id": user.id, "dm_id": None}

@router.get("/conversations")
async def get_conversations(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    
    # 1. 1v1 DMs (過去ログ含む)
    msgs_1v1 = await client.table("direct_messages").select("id, sender_id, receiver_id, content, is_deleted, created_at, is_read").is_("room_id", "null").or_(f"sender_id.eq.{user.id},receiver_id.eq.{user.id}").order("created_at", desc=True).execute()
    
    # 2. Groups
    my_groups = await client.table("dm_room_members").select("room_id, dm_rooms(id, room_name, updated_at)").eq("user_id", user.id).execute()
    
    convs = []
    
    # Process 1v1
    if msgs_1v1.data:
        partner_ids = {m["receiver_id"] if m["sender_id"] == user.id else m["sender_id"] for m in msgs_1v1.data}
        profs = await client.table("profiles").select("id, nickname, dm_id, avatar_drive_id").in_("id", list(partner_ids)).execute()
        prof_map = {p["id"]: p for p in (profs.data or [])}
        blocks = await client.table("dm_blocks").select("*").or_(f"blocker_user_id.eq.{user.id},blocked_user_id.eq.{user.id}").execute()
        
        added_1v1 = set()
        for msg in msgs_1v1.data:
            pid = msg["receiver_id"] if msg["sender_id"] == user.id else msg["sender_id"]
            if pid in added_1v1: continue
            added_1v1.add(pid)
            p = prof_map.get(pid, {})
            if not p.get("dm_id"): continue
            
            b_by = any(b["blocker_user_id"] == pid and b["blocked_user_id"] == user.id for b in (blocks.data or []))
            b_ing = any(b["blocker_user_id"] == user.id and b["blocked_user_id"] == pid for b in (blocks.data or []))
            
            convs.append({
                "type": "DIRECT",
                "partner_dm_id": p.get("dm_id"),
                "partner_user_id": pid,
                "name": p.get("nickname", "名無し"),
                "avatar": p.get("avatar_drive_id"),
                "latest_message": "送信取消済" if msg["is_deleted"] else msg["content"],
                "latest_time": msg["created_at"],
                "is_blocked": b_by or b_ing
            })

    # Process Groups
    for g in (my_groups.data or []):
        r = g["dm_rooms"]
        l_msg = await client.table("direct_messages").select("content, is_deleted").eq("room_id", r["id"]).order("created_at", desc=True).limit(1).execute()
        txt = (l_msg.data[0]["content"] if not l_msg.data[0]["is_deleted"] else "送信取消済") if l_msg.data else "メッセージなし"
        convs.append({
            "type": "GROUP",
            "room_id": r["id"],
            "name": r["room_name"],
            "latest_message": txt,
            "latest_time": r["updated_at"],
            "is_blocked": False
        })

    convs.sort(key=lambda x: x["latest_time"], reverse=True)
    return {"conversations": convs}

@router.get("/messages/direct/{partner_dm_id}")
async def get_direct_msgs(partner_dm_id: str, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    tgt = await client.table("profiles").select("id").eq("dm_id", partner_dm_id).execute()
    if not tgt.data: return {"messages": []}
    pid = tgt.data[0]["id"]
    
    await client.table("direct_messages").update({"is_read": True}).eq("sender_id", pid).eq("receiver_id", user.id).is_("room_id", "null").eq("is_read", False).execute()
    
    msgs = await client.table("direct_messages").select("*, dm_message_reactions(reaction_type, user_id)").is_("room_id", "null").or_(f"and(sender_id.eq.{user.id},receiver_id.eq.{pid}),and(sender_id.eq.{pid},receiver_id.eq.{user.id})").order("created_at").limit(200).execute()
    return {"messages": msgs.data or [], "partner_user_id": pid}

@router.get("/messages/group/{room_id}")
async def get_group_msgs(room_id: int, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    msgs = await client.table("direct_messages").select("*, profiles(nickname, avatar_drive_id), dm_message_reactions(reaction_type, user_id)").eq("room_id", room_id).order("created_at").limit(200).execute()
    return {"messages": msgs.data or []}

@router.post("/send")
async def send_msg(data: SendMsgReq, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    
    if data.room_id:
        await client.table("direct_messages").insert({"sender_id": user.id, "room_id": data.room_id, "message_type": data.message_type, "content": data.content, "metadata": data.metadata}).execute()
        await client.table("dm_rooms").update({"updated_at": datetime.utcnow().isoformat()}).eq("id", data.room_id).execute()
    else:
        tgt = await client.table("profiles").select("id").eq("dm_id", data.target_dm_id).execute()
        if not tgt.data: raise HTTPException(404, "宛先不明")
        pid = tgt.data[0]["id"]
        blk = await client.table("dm_blocks").select("blocker_user_id").eq("blocker_user_id", pid).eq("blocked_user_id", user.id).execute()
        if blk.data: raise HTTPException(403, "ブロックされています")
        
        await client.table("direct_messages").insert({"sender_id": user.id, "receiver_id": pid, "message_type": data.message_type, "content": data.content, "metadata": data.metadata}).execute()
    return {"message": "送信完了"}

@router.post("/groups")
async def create_group(data: GroupReq, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    profs = await client.table("profiles").select("id").in_("dm_id", data.member_dm_ids).execute()
    m_ids = [p["id"] for p in (profs.data or []) if p["id"] != user.id]
    
    r = await client.table("dm_rooms").insert({"room_name": data.room_name, "owner_user_id": user.id}).execute()
    r_id = r.data[0]["id"]
    
    m_data = [{"room_id": r_id, "user_id": user.id}] + [{"room_id": r_id, "user_id": mid} for mid in m_ids]
    await client.table("dm_room_members").insert(m_data).execute()
    return {"room_id": r_id}

@router.delete("/messages/{msg_id}")
async def delete_msg(msg_id: int, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    try:
        await (await get_supabase()).rpc("cancel_dm_message", {"p_user_id": str(user.id), "p_message_id": msg_id}).execute()
        return {"success": True}
    except Exception as e: raise HTTPException(400, str(getattr(e, "message", e)))

@router.post("/messages/{msg_id}/react")
async def react_msg(msg_id: int, data: ReactReq, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    exist = await client.table("dm_message_reactions").select("*").eq("message_id", msg_id).eq("user_id", user.id).eq("reaction_type", data.reaction_type).execute()
    if exist.data: await client.table("dm_message_reactions").delete().eq("message_id", msg_id).eq("user_id", user.id).eq("reaction_type", data.reaction_type).execute()
    else: await client.table("dm_message_reactions").insert({"message_id": msg_id, "user_id": user.id, "reaction_type": data.reaction_type}).execute()
    return {"success": True}
