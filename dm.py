import os
import json
import re
import time
import traceback
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from fastapi import APIRouter, Header, HTTPException, BackgroundTasks, UploadFile, File, Form
from pydantic import BaseModel, Field
import httpx
from pywebpush import webpush, WebPushException
from postgrest.exceptions import APIError
from agora_token_builder import RtcTokenBuilder
from db import get_supabase

router = APIRouter()

# --- VAPIDキー・外部設定 ---
CLIENT_ID = os.environ.get("GDRIVE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GDRIVE_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("GDRIVE_REFRESH_TOKEN", "")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "YOUR_PUBLIC_KEY_HERE")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "YOUR_PRIVATE_KEY_HERE")
VAPID_CLAIMS = {"sub": "mailto:admin@example.com"}
AGORA_APP_ID = os.environ.get("AGORA_APP_ID", "")
AGORA_APP_CERTIFICATE = os.environ.get("AGORA_APP_CERTIFICATE", "")

http_client = httpx.AsyncClient(timeout=60.0)

# --- Models ---
class DMIDUpdate(BaseModel):
    dm_id: str = Field(..., min_length=3, max_length=20, description="3文字以上20文字以内")

class DMRequest(BaseModel):
    target_dm_id: Optional[str] = None
    room_id: Optional[int] = None
    message_type: str = "TEXT"
    content: str = Field(..., min_length=1, max_length=1000)
    metadata: Dict[str, Any] = Field(default_factory=dict)

class PushSubscription(BaseModel):
    endpoint: str
    p256dh: str
    auth: str

class GroupReq(BaseModel):
    room_name: str
    member_dm_ids: List[str]

class ReactReq(BaseModel):
    reaction_type: str

class TransferReq(BaseModel):
    target_user_id: str
    sender_wallet_id: str
    amount: int
    room_id: Optional[int] = None

class BlockReq(BaseModel):
    target_user_id: str

class ReportReq(BaseModel):
    message_id: int
    reason: str

class CallSignalReq(BaseModel):
    target_id: Optional[str] = None
    room_id: Optional[int] = None
    signal_type: str
    signal_data: Dict[str, Any]

class StampPurchaseReq(BaseModel):
    pack_id: int
    wallet_id: str

# --- Helpers ---
async def get_user_auth(authorization: str):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="認証トークンがありません")
    token = authorization.split(" ")[1]
    client = await get_supabase()
    try:
        res = await client.auth.get_user(token)
        return res.user
    except Exception:
        raise HTTPException(status_code=401, detail="無効なトークンです")

async def is_king_user(user_id: str) -> bool:
    try:
        client = await get_supabase()
        res = await client.table("profiles").select("role").eq("id", user_id).execute()
        return bool(res.data and res.data[0].get("role") == "king")
    except Exception:
        return False

async def upload_image_to_drive(file_obj, filename: str, mime_type: str, file_size: int) -> str:
    res = await http_client.post("https://oauth2.googleapis.com/token", data={
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "refresh_token": REFRESH_TOKEN, "grant_type": "refresh_token"
    })
    token = res.json().get("access_token")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": mime_type, "X-Upload-Content-Length": str(file_size)}
    init_res = await http_client.post("https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable", headers=headers, json={"name": filename})
    session_url = init_res.headers.get("Location")
    file_obj.seek(0)
    upload_res = await http_client.put(session_url, headers={"Content-Length": str(file_size)}, content=file_obj.read())
    file_id = upload_res.json().get("id")
    await http_client.post(f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions", headers={"Authorization": f"Bearer {token}"}, json={"role": "reader", "type": "anyone"})
    return file_id

async def send_web_push(receiver_id: str, sender_name: str, message_text: str):
    if VAPID_PRIVATE_KEY in ["YOUR_PRIVATE_KEY_HERE", "YOUR_PUBLIC_KEY_HERE", ""]: return
    try:
        client = await get_supabase()
        subs_res = await client.table("push_subscriptions").select("*").eq("user_id", receiver_id).execute()
        if not subs_res.data: return
        payload = {"title": f"村岡王国: {sender_name} からの密書", "body": message_text[:50], "icon": "/templates/icon.png", "url": "/dm"}
        for sub in subs_res.data:
            try: webpush(subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}}, data=json.dumps(payload), vapid_private_key=VAPID_PRIVATE_KEY, vapid_claims=VAPID_CLAIMS)
            except Exception: pass
    except Exception: pass

# =====================================================================
# API: ユーザー情報・設定
# =====================================================================
@router.get("/me")
async def get_my_dm_info(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    res = await client.table("profiles").select("id, dm_id, nickname, avatar_drive_id, role").eq("id", user.id).execute()
    return res.data[0] if res.data else {"id": user.id, "dm_id": None, "role": "user"}

@router.post("/set-id")
async def set_dm_id(data: DMIDUpdate, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    if not re.match(r"^[a-zA-Z0-9_]+$", data.dm_id):
        raise HTTPException(status_code=400, detail="DM IDは半角英数字とアンダースコア(_)のみ使用可能です。")
    client = await get_supabase()
    exist = await client.table("profiles").select("id").eq("dm_id", data.dm_id).neq("id", user.id).execute()
    if exist.data:
        raise HTTPException(status_code=400, detail="そのIDは既に他の国民が使用しています。")
    await client.table("profiles").update({"dm_id": data.dm_id}).eq("id", user.id).execute()
    return {"message": f"DM IDを @{data.dm_id} に設定しました！"}

@router.get("/vapid-public-key")
async def get_vapid_public_key():
    return {"public_key": VAPID_PUBLIC_KEY}

@router.get("/agora-app-id")
async def get_agora_app_id():
    return {"app_id": AGORA_APP_ID}

@router.get("/agora-token")
async def get_agora_token(channel_name: str, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    if not AGORA_APP_ID or not AGORA_APP_CERTIFICATE:
        raise HTTPException(status_code=500, detail="Agora設定が不足しています")
    expire_time = int(time.time()) + 86400
    token = RtcTokenBuilder.buildTokenWithUid(
        AGORA_APP_ID,
        AGORA_APP_CERTIFICATE,
        channel_name,
        0,
        1,
        expire_time
    )
    return {"token": token, "app_id": AGORA_APP_ID}


@router.post("/push-subscribe")
async def subscribe_push(sub: PushSubscription, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    exist = await client.table("push_subscriptions").select("id").eq("endpoint", sub.endpoint).execute()
    if exist.data:
        await client.table("push_subscriptions").update({"user_id": user.id, "p256dh": sub.p256dh, "auth": sub.auth}).eq("id", exist.data[0]["id"]).execute()
    else:
        await client.table("push_subscriptions").insert({"user_id": user.id, "endpoint": sub.endpoint, "p256dh": sub.p256dh, "auth": sub.auth}).execute()
    return {"message": "通知設定を有効化しました。"}

@router.post("/blocks/toggle")
async def toggle_block(data: BlockReq, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    exist = await client.table("dm_blocks").select("*").eq("blocker_user_id", user.id).eq("blocked_user_id", data.target_user_id).execute()
    if exist.data:
        await client.table("dm_blocks").delete().eq("blocker_user_id", user.id).eq("blocked_user_id", data.target_user_id).execute()
        return {"message": "ブロックを解除しました。"}
    else:
        await client.table("dm_blocks").insert({"blocker_user_id": user.id, "blocked_user_id": data.target_user_id}).execute()
        return {"message": "この国民をブロックしました。"}

# =====================================================================
# API: スレッド一覧・メッセージ操作
# =====================================================================
@router.post("/send")
async def send_dm(data: DMRequest, background_tasks: BackgroundTasks, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    
    if data.room_id:
        await client.table("direct_messages").insert({
            "sender_id": user.id, "room_id": data.room_id, "message_type": data.message_type, "content": data.content.strip(), "metadata": data.metadata
        }).execute()
        await client.table("dm_rooms").update({"updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", data.room_id).execute()
        return {"message": "送信完了しました。"}
    else:
        target_res = await client.table("profiles").select("id").eq("dm_id", data.target_dm_id).execute()
        if not target_res.data: raise HTTPException(status_code=404, detail="ユーザーが見つかりません。")
        target_id = target_res.data[0]["id"]
        if str(user.id) == str(target_id): raise HTTPException(status_code=400, detail="自分自身には送信できません。")

        blk = await client.table("dm_blocks").select("*").eq("blocker_user_id", target_id).eq("blocked_user_id", user.id).execute()
        if blk.data: raise HTTPException(status_code=403, detail="この国民には拒絶（ブロック）されています。")

        await client.table("direct_messages").insert({
            "sender_id": user.id, "receiver_id": target_id, "message_type": data.message_type, "content": data.content.strip(), "metadata": data.metadata
        }).execute()

        prof = await client.table("profiles").select("nickname").eq("id", user.id).execute()
        sender_name = prof.data[0]["nickname"] if prof.data else "不明な国民"
        background_tasks.add_task(send_web_push, target_id, sender_name, data.content.strip())
        return {"message": "送信完了しました。"}

@router.get("/conversations")
async def get_conversations(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    
    filter_str = f"sender_id.eq.{user.id},receiver_id.eq.{user.id}"
    msgs_1v1 = await client.table("direct_messages").select("id, sender_id, receiver_id, content, is_read, is_deleted, created_at").is_("room_id", "null").or_(filter_str).order("created_at", desc=True).limit(500).execute()

    my_groups_res = await client.table("dm_room_members").select("room_id").eq("user_id", user.id).execute()
    group_ids = [g["room_id"] for g in (my_groups_res.data or [])]
    groups_data = []
    if group_ids:
        r_res = await client.table("dm_rooms").select("id, room_name, updated_at").in_("id", group_ids).execute()
        groups_data = r_res.data or []

    blocks_res = await client.table("dm_blocks").select("*").or_(f"blocker_user_id.eq.{user.id},blocked_user_id.eq.{user.id}").execute()
    blocks = blocks_res.data or []

    convs = {}
    
    if msgs_1v1.data:
        partner_ids = {m["receiver_id"] if m["sender_id"] == user.id else m["sender_id"] for m in msgs_1v1.data}
        profiles_res = await client.table("profiles").select("id, nickname, dm_id, avatar_drive_id").in_("id", list(partner_ids)).execute()
        prof_map = {p["id"]: p for p in (profiles_res.data or [])}

        for msg in msgs_1v1.data:
            is_sender = (msg["sender_id"] == user.id)
            partner_id = msg["receiver_id"] if is_sender else msg["sender_id"]
            p_info = prof_map.get(partner_id)
            if not p_info or not p_info.get("dm_id"): continue

            is_b_by = any(b["blocker_user_id"] == partner_id and b["blocked_user_id"] == user.id for b in blocks)
            is_b_ing = any(b["blocker_user_id"] == user.id and b["blocked_user_id"] == partner_id for b in blocks)

            if partner_id not in convs:
                convs[partner_id] = {
                    "type": "DIRECT",
                    "partner_dm_id": p_info.get("dm_id"),
                    "partner_user_id": partner_id,
                    "partner_name": p_info.get("nickname") or "名無し国民",
                    "avatar": p_info.get("avatar_drive_id"),
                    "latest_message": "送信取り消し済" if msg.get("is_deleted") else msg["content"],
                    "latest_time": msg["created_at"],
                    "unread_count": 0,
                    "is_blocked": is_b_by or is_b_ing
                }
            if not is_sender and not msg.get("is_read", False):
                convs[partner_id]["unread_count"] += 1

    result_list = list(convs.values())

    for r in groups_data:
        l_msg = await client.table("direct_messages").select("content, is_deleted").eq("room_id", r["id"]).order("created_at", desc=True).limit(1).execute()
        txt = (l_msg.data[0]["content"] if not l_msg.data[0]["is_deleted"] else "送信取り消し済") if l_msg.data else "メッセージなし"
        result_list.append({
            "type": "GROUP",
            "room_id": r["id"],
            "partner_name": r["room_name"],
            "latest_message": txt,
            "latest_time": r["updated_at"],
            "unread_count": 0,
            "is_blocked": False
        })

    result_list.sort(key=lambda x: x["latest_time"], reverse=True)
    return {"conversations": result_list}

@router.get("/messages/{partner_dm_id}")
async def get_messages(partner_dm_id: str, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    
    tgt = await client.table("profiles").select("id").eq("dm_id", partner_dm_id).execute()
    if not tgt.data: return {"messages": [], "partner_user_id": None}
    partner_id = tgt.data[0]["id"]
    
    # 既読更新
    await client.table("direct_messages").update({"is_read": True}).eq("sender_id", partner_id).eq("receiver_id", user.id).is_("room_id", "null").eq("is_read", False).execute()

    cond = f"and(sender_id.eq.{user.id},receiver_id.eq.{partner_id}),and(sender_id.eq.{partner_id},receiver_id.eq.{user.id})"
    # 最新から200件取得して反転（古い順）
    res = await client.table("direct_messages").select(
        "id, sender_id, receiver_id, message_type, content, metadata, is_pinned, is_deleted, is_read, created_at"
    ).is_("room_id", "null").or_(cond).order("created_at", desc=True).limit(200).execute()
    msgs = res.data or []
    msgs.reverse()

    msg_ids = [m["id"] for m in msgs]
    r_map = {}
    if msg_ids:
        r_res = await client.table("dm_message_reactions").select("message_id, reaction_type, user_id").in_("message_id", msg_ids).execute()
        for r in (r_res.data or []):
            mid = r["message_id"]
            if mid not in r_map: r_map[mid] = []
            r_map[mid].append(r)

    for m in msgs: m["reactions"] = r_map.get(m["id"], [])
    return {"messages": msgs, "partner_user_id": partner_id}

@router.get("/messages/group/{room_id}")
async def get_group_messages(room_id: int, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    res = await client.table("direct_messages").select(
        "id, sender_id, room_id, message_type, content, metadata, is_pinned, is_deleted, created_at"
    ).eq("room_id", room_id).order("created_at", desc=True).limit(200).execute()
    msgs = res.data or []
    msgs.reverse()

    u_ids = list({m["sender_id"] for m in msgs})
    p_map = {}
    if u_ids:
        profs = await client.table("profiles").select("id, nickname, avatar_drive_id").in_("id", u_ids).execute()
        p_map = {p["id"]: p for p in (profs.data or [])}

    msg_ids = [m["id"] for m in msgs]
    r_map = {}
    if msg_ids:
        r_res = await client.table("dm_message_reactions").select("message_id, reaction_type, user_id").in_("message_id", msg_ids).execute()
        for r in (r_res.data or []):
            mid = r["message_id"]
            if mid not in r_map: r_map[mid] = []
            r_map[mid].append(r)

    for m in msgs:
        m["sender"] = p_map.get(m["sender_id"], {"nickname": "不明"})
        m["reactions"] = r_map.get(m["id"], [])
    return {"messages": msgs}

@router.delete("/messages/{message_id}")
async def delete_msg(message_id: int, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    try:
        await (await get_supabase()).rpc("cancel_dm_message", {"p_user_id": str(user.id), "p_message_id": message_id}).execute()
        return {"success": True}
    except Exception as e: raise HTTPException(400, detail=str(getattr(e, "message", e)))

@router.post("/messages/{message_id}/react")
async def toggle_react(message_id: int, data: ReactReq, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    exist = await client.table("dm_message_reactions").select("id").eq("message_id", message_id).eq("user_id", user.id).eq("reaction_type", data.reaction_type).execute()
    if exist.data:
        await client.table("dm_message_reactions").delete().eq("id", exist.data[0]["id"]).execute()
    else:
        await client.table("dm_message_reactions").insert({"message_id": message_id, "user_id": user.id, "reaction_type": data.reaction_type}).execute()
    return {"success": True}

@router.post("/messages/{message_id}/pin")
async def toggle_pin(message_id: int, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    m = await client.table("direct_messages").select("is_pinned").eq("id", message_id).execute()
    if not m.data: raise HTTPException(404, detail="Not Found")
    await client.table("direct_messages").update({"is_pinned": not m.data[0]["is_pinned"]}).eq("id", message_id).execute()
    return {"success": True}

@router.post("/reports")
async def report_msg(data: ReportReq, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    m = await client.table("direct_messages").select("sender_id").eq("id", data.message_id).execute()
    if not m.data: raise HTTPException(404, detail="Not Found")
    await client.table("dm_reports").insert({"message_id": data.message_id, "reporter_id": user.id, "reported_user_id": m.data[0]["sender_id"], "reason": data.reason}).execute()
    return {"message": "国王へ密告しました。"}

# =====================================================================
# API: グループ結成・送金・画像アップロード
# =====================================================================
@router.post("/groups")
async def create_group(data: GroupReq, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    p_res = await client.table("profiles").select("id").in_("dm_id", data.member_dm_ids).execute()
    m_ids = [p["id"] for p in (p_res.data or []) if str(p["id"]) != str(user.id)]
    r = await client.table("dm_rooms").insert({"room_name": data.room_name, "owner_user_id": user.id}).execute()
    r_id = r.data[0]["id"]
    ins_data = [{"room_id": r_id, "user_id": user.id}] + [{"room_id": r_id, "user_id": mid} for mid in m_ids]
    await client.table("dm_room_members").insert(ins_data).execute()
    return {"room_id": r_id}

@router.post("/transfer-gold")
async def transfer_gold(data: TransferReq, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    try:
        await (await get_supabase()).rpc("execute_dm_transfer", {
            "p_sender_id": str(user.id), "p_sender_wallet_id": data.sender_wallet_id, "p_target_user_id": data.target_user_id, "p_amount": data.amount, "p_room_id": data.room_id
        }).execute()
        return {"message": f"{data.amount} Gold を送金しました！"}
    except Exception as e: raise HTTPException(400, detail=str(getattr(e, "message", e)))

@router.post("/upload-image")
async def upload_img(file: UploadFile = File(...), target_dm_id: str = Form(None), room_id: int = Form(None), authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    file.file.seek(0, os.SEEK_END); size = file.file.tell(); file.file.seek(0)
    drive_id = await upload_image_to_drive(file.file, file.filename, file.content_type or "image/jpeg", size)
    client = await get_supabase()
    if room_id:
        await client.table("direct_messages").insert({"sender_id": user.id, "room_id": room_id, "message_type": "IMAGE", "content": "🖼️ 画像を受信しました", "metadata": {"drive_file_id": drive_id}}).execute()
    else:
        tgt = await client.table("profiles").select("id").eq("dm_id", target_dm_id).execute()
        await client.table("direct_messages").insert({"sender_id": user.id, "receiver_id": tgt.data[0]["id"], "message_type": "IMAGE", "content": "🖼️ 画像を受信しました", "metadata": {"drive_file_id": drive_id}}).execute()
    return {"success": True}

# =====================================================================
# API: スタンプ機能
# =====================================================================
@router.post("/stamps/packs")
async def create_stamp_pack(title: str = Form(...), price: int = Form(...), file: UploadFile = File(...), authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    
    file.file.seek(0, os.SEEK_END); size = file.file.tell(); file.file.seek(0)
    drive_id = await upload_image_to_drive(file.file, file.filename, "image/png", size)

    r = await client.table("dm_stamp_packs").insert({"creator_user_id": user.id, "title": title.strip(), "price": price}).execute()
    pack_id = r.data[0]["id"]
    await client.table("dm_stamps").insert({"pack_id": pack_id, "drive_file_id": drive_id}).execute()
    await client.table("user_dm_stamps").insert({"user_id": user.id, "pack_id": pack_id}).execute()
    return {"success": True}

@router.get("/stamps/market")
async def get_stamp_market(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    packs = await client.table("dm_stamp_packs").select("id, title, price, creator_user_id").execute()
    p_data = packs.data or []
    
    owned = await client.table("user_dm_stamps").select("pack_id").eq("user_id", user.id).execute()
    owned_ids = [o["pack_id"] for o in (owned.data or [])]

    if p_data:
        pack_ids = [p["id"] for p in p_data]
        stamps = await client.table("dm_stamps").select("pack_id, drive_file_id").in_("pack_id", pack_ids).execute()
        s_map = {}
        for s in (stamps.data or []):
            if s["pack_id"] not in s_map: s_map[s["pack_id"]] = s["drive_file_id"]
        
        profs = await client.table("profiles").select("id, nickname").in_("id", [p["creator_user_id"] for p in p_data]).execute()
        prof_map = {pr["id"]: pr["nickname"] for pr in (profs.data or [])}

        for p in p_data:
            p["cover_file_id"] = s_map.get(p["id"])
            p["creator_name"] = prof_map.get(p["creator_user_id"], "不明")
            p["is_owned"] = p["id"] in owned_ids

    return {"market": p_data}

@router.post("/stamps/purchase")
async def purchase_stamp(data: StampPurchaseReq, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    
    pack = await client.table("dm_stamp_packs").select("price, creator_user_id").eq("id", data.pack_id).execute()
    if not pack.data: raise HTTPException(404, detail="パックが見つかりません")
    price = pack.data[0]["price"]
    
    if price > 0:
        w = await client.table("wallets").select("id, balance").eq("wallet_id", data.wallet_id).eq("user_id", user.id).execute()
        if not w.data or w.data[0]["balance"] < price:
            raise HTTPException(400, detail="残高不足です")
        await client.table("wallets").update({"balance": w.data[0]["balance"] - price}).eq("id", w.data[0]["id"]).execute()
        
        c_w = await client.table("wallets").select("id, balance").eq("user_id", pack.data[0]["creator_user_id"]).order("created_at").limit(1).execute()
        if c_w.data:
            await client.table("wallets").update({"balance": c_w.data[0]["balance"] + price}).eq("id", c_w.data[0]["id"]).execute()

    await client.table("user_dm_stamps").insert({"user_id": user.id, "pack_id": data.pack_id}).execute()
    return {"message": "スタンプを購入しました！"}

@router.get("/stamps/my-stamps")
async def get_my_stamps(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    owned = await client.table("user_dm_stamps").select("pack_id").eq("user_id", user.id).execute()
    if not owned.data: return {"stamps": []}
    p_ids = [o["pack_id"] for o in owned.data]
    stamps = await client.table("dm_stamps").select("id, drive_file_id").in_("pack_id", p_ids).execute()
    return {"stamps": stamps.data or []}

# =====================================================================
# API: WebRTC シグナリング & 着信プッシュ通知
# =====================================================================
@router.post("/call/signal")
async def send_call_signal(data: CallSignalReq, background_tasks: BackgroundTasks, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()

    target_uid = data.target_id
    # 宛先IDが未解決で target_dm_id のみある場合のフォールバック
    if not target_uid and not data.room_id and data.signal_data.get("target_dm_id"):
        t_res = await client.table("profiles").select("id").eq("dm_id", data.signal_data["target_dm_id"]).execute()
        if t_res.data:
            target_uid = t_res.data[0]["id"]

    await client.table("dm_call_signals").insert({
        "sender_id": user.id, 
        "target_id": target_uid, 
        "room_id": data.room_id, 
        "signal_type": data.signal_type, 
        "signal_data": data.signal_data
    }).execute()

    # 発信（OFFER）時に相手へWeb Push通知を送信
    if data.signal_type == "OFFER" and target_uid:
        prof = await client.table("profiles").select("nickname").eq("id", user.id).execute()
        sender_name = prof.data[0]["nickname"] if prof.data else "不明な国民"
        call_type = "ビデオ通話" if data.signal_data.get("isVideo") else "音声通話"
        background_tasks.add_task(
            send_web_push, 
            target_uid, 
            sender_name, 
            f"📞 {call_type}の着信があります！"
        )

    return {"success": True}

@router.get("/call/signals")
async def poll_call_signals(target_user_id: Optional[str] = None, room_id: Optional[int] = None, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    
    # タイムゾーン付きUTC（過去35秒以内の有効シグナルのみ抽出）
    now_35s_ago = datetime.now(timezone.utc).timestamp() - 35
    time_limit_str = datetime.fromtimestamp(now_35s_ago, tz=timezone.utc).isoformat()
    
    query = client.table("dm_call_signals").select("*").gt("created_at", time_limit_str).order("created_at")
    
    if room_id: 
        res = await query.eq("room_id", room_id).neq("sender_id", user.id).execute()
    else: 
        if not target_user_id:
            return {"signals": []}
        res = await query.eq("target_id", user.id).eq("sender_id", target_user_id).execute()
        
    return {"signals": res.data or []}

# =====================================================================
# API: 国王専用 監視ツール (完全維持)
# =====================================================================
@router.get("/admin/threads")
async def admin_get_all_threads(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    if not await is_king_user(user.id): raise HTTPException(status_code=403, detail="権限がありません。")
    client = await get_supabase()
    msgs = await client.table("direct_messages").select("id, sender_id, receiver_id, content, created_at").is_("room_id", "null").order("created_at", desc=True).limit(500).execute()
    if not msgs.data: return {"threads": []}
    u_ids = list({m["sender_id"] for m in msgs.data} | {m["receiver_id"] for m in msgs.data})
    profs = await client.table("profiles").select("id, nickname, dm_id").in_("id", u_ids).execute()
    prof_map = {p["id"]: p for p in (profs.data or [])}
    threads = {}
    for msg in msgs.data:
        users = sorted([msg["sender_id"], msg["receiver_id"]])
        thread_key = f"{users[0]}_{users[1]}"
        if thread_key not in threads:
            p_a = prof_map.get(msg["sender_id"], {})
            p_b = prof_map.get(msg["receiver_id"], {})
            threads[thread_key] = {
                "user_a_id": msg["sender_id"], "user_a_name": p_a.get("nickname", "不明"), "user_a_dm_id": p_a.get("dm_id", ""),
                "user_b_id": msg["receiver_id"], "user_b_name": p_b.get("nickname", "不明"), "user_b_dm_id": p_b.get("dm_id", ""),
                "latest_message": msg["content"], "latest_time": msg["created_at"], "msg_count": 1
            }
        else: threads[thread_key]["msg_count"] += 1
    return {"threads": list(threads.values())}

@router.get("/admin/messages/{user_a_id}/{user_b_id}")
async def admin_get_thread_messages(user_a_id: str, user_b_id: str, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    if not await is_king_user(user.id): raise HTTPException(status_code=403, detail="権限がありません。")
    client = await get_supabase()
    cond = f"and(sender_id.eq.{user_a_id},receiver_id.eq.{user_b_id}),and(sender_id.eq.{user_b_id},receiver_id.eq.{user_a_id})"
    res = await client.table("direct_messages").select("id, sender_id, receiver_id, content, is_deleted, created_at").is_("room_id", "null").or_(cond).order("created_at", desc=False).limit(200).execute()
    msgs = res.data or []
    profs = await client.table("profiles").select("id, nickname").in_("id", [user_a_id, user_b_id]).execute()
    p_map = {p["id"]: p["nickname"] for p in (profs.data or [])}
    for m in msgs: m["sender"] = {"nickname": p_map.get(m["sender_id"], "不明")}
    return {"messages": msgs}

@router.delete("/admin/messages/{message_id}")
async def admin_delete_msg(message_id: int, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    if not await is_king_user(user.id): raise HTTPException(status_code=403, detail="権限がありません。")
    await (await get_supabase()).rpc("cancel_dm_message", {"p_user_id": str(user.id), "p_message_id": message_id}).execute()
    return {"success": True}
