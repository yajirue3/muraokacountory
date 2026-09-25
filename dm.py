import os
import json
import re
import traceback
from typing import Optional, List, Dict, Any
from datetime import datetime
from fastapi import APIRouter, Header, HTTPException, BackgroundTasks, UploadFile, File
from pydantic import BaseModel, Field
import httpx
from pywebpush import webpush, WebPushException
from postgrest.exceptions import APIError
from db import get_supabase

router = APIRouter()

# --- 各種設定 ---
CLIENT_ID = os.environ.get("GDRIVE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GDRIVE_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("GDRIVE_REFRESH_TOKEN", "")
FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "")

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "YOUR_PUBLIC_KEY_HERE")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "YOUR_PRIVATE_KEY_HERE")
VAPID_CLAIMS = {"sub": "mailto:admin@example.com"}

http_client = httpx.AsyncClient(timeout=60.0)

# --- Pydantic リクエストモデル ---
class DMIDUpdate(BaseModel):
    dm_id: str = Field(..., min_length=3, max_length=20)

class PushSubscription(BaseModel):
    endpoint: str
    p256dh: str
    auth: str

class SendMsgReq(BaseModel):
    target_dm_id: Optional[str] = None
    room_id: Optional[int] = None
    message_type: str = "TEXT"
    content: str = Field(..., min_length=1, max_length=1000)
    metadata: Dict[str, Any] = Field(default_factory=dict)

class GroupReq(BaseModel):
    room_name: str
    member_dm_ids: List[str]

class ReactReq(BaseModel):
    reaction_type: str = Field(..., pattern="^(👍|❤️|😂|🙏|👀|🔥)$")

class TransferReq(BaseModel):
    target_user_id: str
    sender_wallet_id: str
    amount: int = Field(..., gt=0)
    room_id: Optional[int] = None

class BlockReq(BaseModel):
    target_user_id: str

class ReportReq(BaseModel):
    message_id: int
    reason: str = ""

class CallSignalReq(BaseModel):
    target_id: Optional[str] = None
    room_id: Optional[int] = None
    signal_type: str
    signal_data: Dict[str, Any]

class StampPackCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=50)
    price: int = Field(0, ge=0)

# --- 認証 & Google Drive & WebPush ヘルパー ---
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
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "grant_type": "refresh_token"
    })
    token = res.json().get("access_token")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": mime_type,
        "X-Upload-Content-Length": str(file_size)
    }
    metadata = {"name": filename}
    if FOLDER_ID:
        metadata["parents"] = [FOLDER_ID]

    init_res = await http_client.post("https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable", headers=headers, json=metadata)
    session_url = init_res.headers.get("Location")

    file_obj.seek(0)
    upload_res = await http_client.put(session_url, headers={"Content-Length": str(file_size)}, content=file_obj.read())
    file_id = upload_res.json().get("id")

    await http_client.post(f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions", headers={"Authorization": f"Bearer {token}"}, json={"role": "reader", "type": "anyone"})
    return file_id

async def send_web_push(receiver_id: str, sender_name: str, message_text: str):
    if VAPID_PRIVATE_KEY in ["YOUR_PRIVATE_KEY_HERE", "YOUR_PUBLIC_KEY_HERE", ""]:
        return
    try:
        client = await get_supabase()
        subs_res = await client.table("push_subscriptions").select("*").eq("user_id", receiver_id).execute()
        if not subs_res.data:
            return

        payload = {
            "title": f"村岡王国: {sender_name} からの密書",
            "body": message_text[:50] + ("..." if len(message_text) > 50 else ""),
            "icon": "/templates/icon.png",
            "url": "/dm"
        }

        for sub in subs_res.data:
            try:
                webpush(
                    subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                    data=json.dumps(payload),
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims=VAPID_CLAIMS
                )
            except Exception:
                pass
    except Exception:
        pass

# =====================================================================
# 1. ユーザー情報・通知・ブロック
# =====================================================================
@router.get("/me")
async def get_my_dm_info(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    res = await client.table("profiles").select("id, dm_id, nickname, avatar_drive_id, role").eq("id", user.id).execute()
    return res.data[0] if res.data else {"id": user.id, "dm_id": None, "nickname": "名無し国民", "avatar_drive_id": None, "role": "user"}

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
    if str(user.id) == data.target_user_id:
        raise HTTPException(status_code=400, detail="自分自身をブロックすることはできません。")

    exist = await client.table("dm_blocks").select("*").eq("blocker_user_id", user.id).eq("blocked_user_id", data.target_user_id).execute()
    if exist.data:
        await client.table("dm_blocks").delete().eq("blocker_user_id", user.id).eq("blocked_user_id", data.target_user_id).execute()
        return {"message": "ブロックを解除しました。", "is_blocked": False}
    else:
        await client.table("dm_blocks").insert({"blocker_user_id": user.id, "blocked_user_id": data.target_user_id}).execute()
        return {"message": "この国民をブロックしました。交信が制限されます。", "is_blocked": True}

# =====================================================================
# 2. 会話スレッド一覧（過去ログ1v1 ＋ グループ密談部屋）
# =====================================================================
@router.get("/conversations")
async def get_conversations(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()

    # A. 過去1v1ログ (room_id が NULL のもの)
    filter_str = f"sender_id.eq.{user.id},receiver_id.eq.{user.id}"
    msgs_1v1 = await client.table("direct_messages").select("id, sender_id, receiver_id, content, is_deleted, is_read, created_at").is_("room_id", "null").or_(filter_str).order("created_at", desc=True).execute()

    # B. 自分が参加しているグループ部屋
    my_memberships = await client.table("dm_room_members").select("room_id").eq("user_id", user.id).execute()
    room_ids = [m["room_id"] for m in (my_memberships.data or [])]
    my_groups = []
    if room_ids:
        r_res = await client.table("dm_rooms").select("id, room_name, updated_at").in_("id", room_ids).execute()
        my_groups = r_res.data or []

    # C. ブロック関係の取得
    blocks_res = await client.table("dm_blocks").select("*").or_(f"blocker_user_id.eq.{user.id},blocked_user_id.eq.{user.id}").execute()
    blocks = blocks_res.data or []

    convs = []

    # 1v1会話の集約
    if msgs_1v1.data:
        partner_ids = {m["receiver_id"] if m["sender_id"] == user.id else m["sender_id"] for m in msgs_1v1.data}
        profiles_res = await client.table("profiles").select("id, nickname, dm_id, avatar_drive_id").in_("id", list(partner_ids)).execute()
        prof_map = {p["id"]: p for p in (profiles_res.data or [])}

        unread_map = {}
        for m in msgs_1v1.data:
            if m["sender_id"] != user.id and not m.get("is_read", False):
                unread_map[m["sender_id"]] = unread_map.get(m["sender_id"], 0) + 1

        added_1v1 = set()
        for msg in msgs_1v1.data:
            p_id = msg["receiver_id"] if msg["sender_id"] == user.id else msg["sender_id"]
            if p_id in added_1v1:
                continue
            added_1v1.add(p_id)
            p_info = prof_map.get(p_id, {})
            if not p_info.get("dm_id"):
                continue

            is_b_by = any(b["blocker_user_id"] == p_id and b["blocked_user_id"] == user.id for b in blocks)
            is_b_ing = any(b["blocker_user_id"] == user.id and b["blocked_user_id"] == p_id for b in blocks)

            convs.append({
                "type": "DIRECT",
                "partner_dm_id": p_info.get("dm_id"),
                "partner_user_id": p_id,
                "name": p_info.get("nickname") or "名無し国民",
                "avatar": p_info.get("avatar_drive_id"),
                "latest_message": "送信取り消し済" if msg.get("is_deleted") else msg["content"],
                "latest_time": msg["created_at"],
                "unread_count": unread_map.get(p_id, 0),
                "is_blocked": is_b_by or is_b_ing
            })

    # グループ会話の集約
    for r in my_groups:
        l_msg = await client.table("direct_messages").select("content, is_deleted").eq("room_id", r["id"]).order("created_at", desc=True).limit(1).execute()
        txt = (l_msg.data[0]["content"] if not l_msg.data[0]["is_deleted"] else "送信取り消し済") if l_msg.data else "メッセージはありません"
        convs.append({
            "type": "GROUP",
            "room_id": r["id"],
            "name": r["room_name"],
            "latest_message": txt,
            "latest_time": r["updated_at"],
            "unread_count": 0,
            "is_blocked": False
        })

    convs.sort(key=lambda x: x["latest_time"], reverse=True)
    return {"conversations": convs}

# =====================================================================
# 3. メッセージ取得（JOINを完全廃止し PGRST200 を防止）
# =====================================================================
@router.get("/messages/direct/{partner_dm_id}")
async def get_direct_messages(partner_dm_id: str, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()

    tgt = await client.table("profiles").select("id").eq("dm_id", partner_dm_id).execute()
    if not tgt.data:
        return {"messages": [], "partner_user_id": None}
    partner_id = tgt.data[0]["id"]

    # 既読更新
    await client.table("direct_messages").update({"is_read": True}).eq("sender_id", partner_id).eq("receiver_id", user.id).is_("room_id", "null").eq("is_read", False).execute()

    cond = f"and(sender_id.eq.{user.id},receiver_id.eq.{partner_id}),and(sender_id.eq.{partner_id},receiver_id.eq.{user.id})"
    # 安全な単一テーブルクエリ
    res = await client.table("direct_messages").select(
        "id, sender_id, receiver_id, message_type, content, metadata, is_pinned, is_deleted, is_read, created_at"
    ).is_("room_id", "null").or_(cond).order("created_at", desc=False).limit(200).execute()
    msgs = res.data or []

    # リアクションを別テーブルから取得してPython側でマッピング
    msg_ids = [m["id"] for m in msgs]
    reactions_map = {}
    if msg_ids:
        r_res = await client.table("dm_message_reactions").select("message_id, reaction_type, user_id").in_("message_id", msg_ids).execute()
        for r in (r_res.data or []):
            mid = r["message_id"]
            if mid not in reactions_map:
                reactions_map[mid] = []
            reactions_map[mid].append(r)

    for m in msgs:
        m["reactions"] = reactions_map.get(m["id"], [])

    return {"messages": msgs, "partner_user_id": partner_id}

@router.get("/messages/group/{room_id}")
async def get_group_messages(room_id: int, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()

    # メンバー権限チェック
    mem = await client.table("dm_room_members").select("user_id").eq("room_id", room_id).eq("user_id", user.id).execute()
    if not mem.data and not await is_king_user(user.id):
        raise HTTPException(status_code=403, detail="この部屋を閲覧する権限がありません。")

    res = await client.table("direct_messages").select(
        "id, sender_id, room_id, message_type, content, metadata, is_pinned, is_deleted, created_at"
    ).eq("room_id", room_id).order("created_at", desc=False).limit(200).execute()
    msgs = res.data or []

    # 送信者プロフィールを安全にマッピング
    u_ids = list({m["sender_id"] for m in msgs})
    p_map = {}
    if u_ids:
        profs = await client.table("profiles").select("id, nickname, avatar_drive_id").in_("id", u_ids).execute()
        p_map = {p["id"]: p for p in (profs.data or [])}

    # リアクションをマッピング
    msg_ids = [m["id"] for m in msgs]
    reactions_map = {}
    if msg_ids:
        r_res = await client.table("dm_message_reactions").select("message_id, reaction_type, user_id").in_("message_id", msg_ids).execute()
        for r in (r_res.data or []):
            mid = r["message_id"]
            if mid not in reactions_map:
                reactions_map[mid] = []
            reactions_map[mid].append(r)

    for m in msgs:
        m["sender"] = p_map.get(m["sender_id"], {"nickname": "不明", "avatar_drive_id": None})
        m["reactions"] = reactions_map.get(m["id"], [])

    return {"messages": msgs}

# =====================================================================
# 4. 送信・取消・リアクション・ピン留め・密告
# =====================================================================
@router.post("/send")
async def send_message(data: SendMsgReq, background_tasks: BackgroundTasks, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()

    if data.room_id:
        # グループ送信
        await client.table("direct_messages").insert({
            "sender_id": user.id,
            "room_id": data.room_id,
            "message_type": data.message_type,
            "content": data.content.strip(),
            "metadata": data.metadata
        }).execute()
        await client.table("dm_rooms").update({"updated_at": datetime.utcnow().isoformat()}).eq("id", data.room_id).execute()
        return {"message": "送信完了しました。"}
    else:
        # 1v1送信
        target_res = await client.table("profiles").select("id").eq("dm_id", data.target_dm_id).execute()
        if not target_res.data:
            raise HTTPException(status_code=404, detail="指定されたユーザーは見つかりません。")
        target_id = target_res.data[0]["id"]
        if str(user.id) == str(target_id):
            raise HTTPException(status_code=400, detail="自分自身には送信できません。")

        blk = await client.table("dm_blocks").select("blocker_user_id").eq("blocker_user_id", target_id).eq("blocked_user_id", user.id).execute()
        if blk.data:
            raise HTTPException(status_code=403, detail="この国民には拒絶（ブロック）されているため、密書を送信できません。")

        await client.table("direct_messages").insert({
            "sender_id": user.id,
            "receiver_id": target_id,
            "message_type": data.message_type,
            "content": data.content.strip(),
            "metadata": data.metadata
        }).execute()

        prof = await client.table("profiles").select("nickname").eq("id", user.id).execute()
        s_name = prof.data[0]["nickname"] if prof.data else "密書"
        background_tasks.add_task(send_web_push, target_id, s_name, data.content.strip())
        return {"message": "送信完了しました。"}

@router.delete("/messages/{message_id}")
async def cancel_message(message_id: int, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    try:
        await (await get_supabase()).rpc("cancel_dm_message", {"p_user_id": str(user.id), "p_message_id": message_id}).execute()
        return {"message": "メッセージの送信を取り消しました。"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

@router.post("/messages/{message_id}/react")
async def toggle_reaction(message_id: int, data: ReactReq, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    exist = await client.table("dm_message_reactions").select("id").eq("message_id", message_id).eq("user_id", user.id).eq("reaction_type", data.reaction_type).execute()
    if exist.data:
        await client.table("dm_message_reactions").delete().eq("id", exist.data[0]["id"]).execute()
        return {"message": "リアクション解除"}
    else:
        await client.table("dm_message_reactions").insert({"message_id": message_id, "user_id": user.id, "reaction_type": data.reaction_type}).execute()
        return {"message": "リアクション付与"}

@router.post("/messages/{message_id}/pin")
async def toggle_pin(message_id: int, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    m = await client.table("direct_messages").select("is_pinned").eq("id", message_id).execute()
    if not m.data:
        raise HTTPException(status_code=404, detail="メッセージが見つかりません")
    new_state = not m.data[0]["is_pinned"]
    await client.table("direct_messages").update({"is_pinned": new_state}).eq("id", message_id).execute()
    return {"message": "ピン留めを更新しました", "is_pinned": new_state}

@router.post("/reports")
async def report_message(data: ReportReq, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    m = await client.table("direct_messages").select("sender_id").eq("id", data.message_id).execute()
    if not m.data:
        raise HTTPException(status_code=404, detail="対象が見つかりません")
    await client.table("dm_reports").insert({
        "message_id": data.message_id,
        "reporter_id": user.id,
        "reported_user_id": m.data[0]["sender_id"],
        "reason": data.reason.strip()
    }).execute()
    return {"message": "国王へ密告しました。"}

# =====================================================================
# 5. グループ結成・送金・画像アップロード
# =====================================================================
@router.post("/groups")
async def create_group_room(data: GroupReq, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    prof_res = await client.table("profiles").select("id").in_("dm_id", data.member_dm_ids).execute()
    m_ids = [p["id"] for p in (prof_res.data or []) if str(p["id"]) != str(user.id)]

    r = await client.table("dm_rooms").insert({"room_name": data.room_name.strip(), "owner_user_id": user.id}).execute()
    rid = r.data[0]["id"]
    m_data = [{"room_id": rid, "user_id": user.id}] + [{"room_id": rid, "user_id": mid} for mid in m_ids]
    await client.table("dm_room_members").insert(m_data).execute()
    return {"room_id": rid}

@router.post("/transfer-gold")
async def transfer_gold(data: TransferReq, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    try:
        await (await get_supabase()).rpc("execute_dm_transfer", {
            "p_sender_id": str(user.id),
            "p_sender_wallet_id": data.sender_wallet_id,
            "p_target_user_id": data.target_user_id,
            "p_amount": data.amount,
            "p_room_id": data.room_id
        }).execute()
        return {"message": f"{data.amount} Gold を送金しました！"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

@router.post("/upload-image")
async def upload_chat_image(file: UploadFile = File(...), target_dm_id: Optional[str] = None, room_id: Optional[int] = None, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    file.file.seek(0, os.SEEK_END)
    size = file.file.tell()
    file.file.seek(0)
    drive_id = await upload_image_to_drive(file.file, file.filename, file.content_type or "image/jpeg", size)

    client = await get_supabase()
    if room_id:
        await client.table("direct_messages").insert({
            "sender_id": user.id,
            "room_id": room_id,
            "message_type": "IMAGE",
            "content": "🖼️ 画像を受信しました",
            "metadata": {"drive_file_id": drive_id}
        }).execute()
        await client.table("dm_rooms").update({"updated_at": datetime.utcnow().isoformat()}).eq("id", room_id).execute()
    else:
        tgt = await client.table("profiles").select("id").eq("dm_id", target_dm_id).execute()
        if not tgt.data:
            raise HTTPException(status_code=404, detail="宛先国民が見つかりません。")
        await client.table("direct_messages").insert({
            "sender_id": user.id,
            "receiver_id": tgt.data[0]["id"],
            "message_type": "IMAGE",
            "content": "🖼️ 画像を受信しました",
            "metadata": {"drive_file_id": drive_id}
        }).execute()
    return {"message": "画像を送信しました。"}

# =====================================================================
# 6. スタンプ機能（自作パック・追加・一覧）
# =====================================================================
@router.post("/stamps/packs")
async def create_stamp_pack(data: StampPackCreate, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    r = await client.table("dm_stamp_packs").insert({"creator_user_id": user.id, "title": data.title.strip(), "price": data.price}).execute()
    pack_id = r.data[0]["id"]
    await client.table("user_dm_stamps").insert({"user_id": user.id, "pack_id": pack_id}).execute()
    return {"pack_id": pack_id}

@router.post("/stamps/packs/{pack_id}/add-stamp")
async def add_stamp_item(pack_id: int, file: UploadFile = File(...), authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    file.file.seek(0, os.SEEK_END)
    size = file.file.tell()
    file.file.seek(0)
    drive_id = await upload_image_to_drive(file.file, file.filename, "image/png", size)
    await (await get_supabase()).table("dm_stamps").insert({"pack_id": pack_id, "drive_file_id": drive_id}).execute()
    return {"message": "スタンプを追加しました"}

@router.get("/stamps/my-stamps")
async def get_my_stamps(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    owned = await client.table("user_dm_stamps").select("pack_id").eq("user_id", user.id).execute()
    if not owned.data:
        return {"stamps": []}
    p_ids = [o["pack_id"] for o in owned.data]
    stamps = await client.table("dm_stamps").select("id, drive_file_id").in_("pack_id", p_ids).execute()
    return {"stamps": stamps.data or []}

# =====================================================================
# 7. WebRTC 音声・ビデオ通話シグナリング
# =====================================================================
@router.post("/call/signal")
async def send_call_signal(data: CallSignalReq, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    await (await get_supabase()).table("dm_call_signals").insert({
        "sender_id": user.id,
        "target_id": data.target_id,
        "room_id": data.room_id,
        "signal_type": data.signal_type,
        "signal_data": data.signal_data
    }).execute()
    return {"message": "ok"}

@router.get("/call/signals")
async def poll_call_signals(target_user_id: Optional[str] = None, room_id: Optional[int] = None, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    now_30s = datetime.fromtimestamp(datetime.utcnow().timestamp() - 30).isoformat()
    if room_id:
        res = await client.table("dm_call_signals").select("*").eq("room_id", room_id).neq("sender_id", user.id).gt("created_at", now_30s).order("created_at").execute()
    else:
        res = await client.table("dm_call_signals").select("*").eq("target_id", user.id).eq("sender_id", target_user_id).gt("created_at", now_30s).order("created_at").execute()
    return {"signals": res.data or []}

# =====================================================================
# 8. 国王専用 監視ツール（完全温存）
# =====================================================================
@router.get("/admin/threads")
async def admin_get_all_threads(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    if not await is_king_user(user.id):
        raise HTTPException(status_code=403, detail="権限がありません。")
    client = await get_supabase()
    msgs = await client.table("direct_messages").select("id, sender_id, receiver_id, content, is_deleted, created_at").is_("room_id", "null").order("created_at", desc=True).limit(500).execute()
    if not msgs.data:
        return {"threads": []}

    u_ids = list({m["sender_id"] for m in msgs.data} | {m["receiver_id"] for m in msgs.data})
    profs = await client.table("profiles").select("id, nickname, dm_id").in_("id", u_ids).execute()
    prof_map = {p["id"]: p for p in (profs.data or [])}

    threads = {}
    for msg in msgs.data:
        users = sorted([msg["sender_id"], msg["receiver_id"]])
        key = f"{users[0]}_{users[1]}"
        if key not in threads:
            p_a = prof_map.get(msg["sender_id"], {})
            p_b = prof_map.get(msg["receiver_id"], {})
            threads[key] = {
                "user_a_id": msg["sender_id"],
                "user_a_name": p_a.get("nickname", "不明"),
                "user_a_dm_id": p_a.get("dm_id", ""),
                "user_b_id": msg["receiver_id"],
                "user_b_name": p_b.get("nickname", "不明"),
                "user_b_dm_id": p_b.get("dm_id", ""),
                "latest_message": "送信取り消し済" if msg.get("is_deleted") else msg["content"],
                "latest_time": msg["created_at"],
                "msg_count": 1
            }
        else:
            threads[key]["msg_count"] += 1
    return {"threads": list(threads.values())}

@router.get("/admin/messages/{user_a_id}/{user_b_id}")
async def admin_get_thread_messages(user_a_id: str, user_b_id: str, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    if not await is_king_user(user.id):
        raise HTTPException(status_code=403, detail="権限がありません。")
    client = await get_supabase()
    cond = f"and(sender_id.eq.{user_a_id},receiver_id.eq.{user_b_id}),and(sender_id.eq.{user_b_id},receiver_id.eq.{user_a_id})"
    res = await client.table("direct_messages").select("id, sender_id, receiver_id, message_type, content, metadata, is_deleted, created_at").is_("room_id", "null").or_(cond).order("created_at", desc=False).limit(200).execute()
    msgs = res.data or []
    profs = await client.table("profiles").select("id, nickname").in_("id", [user_a_id, user_b_id]).execute()
    p_map = {p["id"]: p["nickname"] for p in (profs.data or [])}
    for m in msgs:
        m["sender"] = {"nickname": p_map.get(m["sender_id"], "不明")}
    return {"messages": msgs}

@router.delete("/admin/messages/{message_id}")
async def admin_delete_message(message_id: int, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    if not await is_king_user(user.id):
        raise HTTPException(status_code=403, detail="権限がありません。")
    await (await get_supabase()).rpc("cancel_dm_message", {"p_user_id": str(user.id), "p_message_id": message_id}).execute()
    return {"message": "国王権限によりメッセージの送信を取り消しました。"}
