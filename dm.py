import os
import json
import re
import traceback
from typing import Optional, List, Dict, Any
from datetime import datetime
from fastapi import APIRouter, Header, HTTPException, BackgroundTasks, UploadFile, File, Form
from pydantic import BaseModel, Field
import httpx
from pywebpush import webpush, WebPushException
from postgrest.exceptions import APIError
from db import get_supabase

router = APIRouter()

# --- Google Drive & VAPID 設定 ---
CLIENT_ID = os.environ.get("GDRIVE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GDRIVE_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("GDRIVE_REFRESH_TOKEN", "")
FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "")

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "YOUR_PUBLIC_KEY_HERE")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "YOUR_PRIVATE_KEY_HERE")
VAPID_CLAIMS = {"sub": "mailto:admin@example.com"}

http_client = httpx.AsyncClient(
    timeout=60.0,
    limits=httpx.Limits(max_keepalive_connections=50, max_connections=200)
)

# --- Pydantic リクエストモデル ---
class DMIDUpdate(BaseModel):
    dm_id: str = Field(..., min_length=3, max_length=20, description="3〜20文字")

class PushSubscription(BaseModel):
    endpoint: str
    p256dh: str
    auth: str

class DirectRoomRequest(BaseModel):
    target_dm_id: str

class GroupRoomRequest(BaseModel):
    room_name: str = Field(..., min_length=1, max_length=30)
    member_dm_ids: List[str] = Field(..., min_items=1, max_items=20)

class MessageSendRequest(BaseModel):
    room_id: int
    message_type: str = Field("TEXT", pattern="^(TEXT|IMAGE|STAMP|TRANSFER|CALL_LOG)$")
    content: str = Field(..., min_length=1, max_length=1000)
    metadata: Dict[str, Any] = Field(default_factory=dict)

class ReactionToggleRequest(BaseModel):
    reaction_type: str = Field(..., pattern="^(👍|❤️|😂|🙏|👀|🔥)$")

class TransferGoldRequest(BaseModel):
    room_id: int
    target_user_id: str
    sender_wallet_id: str
    amount: int = Field(..., gt=0, le=100_000_000)

class BlockToggleRequest(BaseModel):
    target_user_id: str

class ReportRequest(BaseModel):
    message_id: int
    reason: str = Field("", max_length=200)

class CallSignalRequest(BaseModel):
    room_id: int
    target_id: Optional[str] = None
    signal_type: str = Field(..., pattern="^(OFFER|ANSWER|ICE_CANDIDATE|JOIN_CALL|LEAVE_CALL|CALL_REJECT)$")
    signal_data: Dict[str, Any]

class StampPackCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=50)
    description: str = Field("", max_length=200)
    price: int = Field(0, ge=0, le=100_000_000)

class StampBuyRequest(BaseModel):
    pack_id: int
    buyer_wallet_id: str


# --- Google Drive & Auth ヘルパー ---
async def get_gdrive_access_token() -> str:
    if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
        raise HTTPException(status_code=500, detail="Google Driveの認証情報が未設定です")
    url = "https://oauth2.googleapis.com/token"
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "grant_type": "refresh_token"
    }
    res = await http_client.post(url, data=data)
    if res.status_code != 200:
        raise HTTPException(status_code=500, detail=f"Googleトークン取得エラー: {res.status_code}")
    return res.json().get("access_token")

async def upload_image_to_drive(file_obj, filename: str, mime_type: str, file_size: int) -> str:
    if file_size > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="画像は10MB以下にしてください")
    token = await get_gdrive_access_token()
    init_url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": mime_type,
        "X-Upload-Content-Length": str(file_size)
    }
    metadata = {"name": filename}
    if FOLDER_ID:
        metadata["parents"] = [FOLDER_ID]

    init_res = await http_client.post(init_url, headers=headers, json=metadata)
    if init_res.status_code != 200:
        raise HTTPException(status_code=500, detail="Google Drive アップロード初期化失敗")
    
    session_url = init_res.headers.get("Location")
    chunk_size = 5 * 1024 * 1024
    file_id = None
    start = 0
    file_obj.seek(0)
    
    while start < file_size:
        chunk = file_obj.read(chunk_size)
        if not chunk: break
        end = start + len(chunk) - 1
        chunk_headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(len(chunk))
        }
        upload_res = await http_client.put(session_url, headers=chunk_headers, content=chunk)
        if upload_res.status_code in (200, 201):
            file_id = upload_res.json().get("id")
            break
        start = end + 1

    if not file_id:
        raise HTTPException(status_code=500, detail="ファイルID取得失敗")

    perm_url = f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions"
    await http_client.post(perm_url, headers={"Authorization": f"Bearer {token}"}, json={"role": "reader", "type": "anyone"})
    return file_id

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

async def send_web_push(receiver_id: str, sender_name: str, message_text: str):
    if VAPID_PRIVATE_KEY in ["YOUR_PRIVATE_KEY_HERE", "YOUR_PUBLIC_KEY_HERE", ""]:
        return
    try:
        client = await get_supabase()
        subs_res = await client.table("push_subscriptions").select("*").eq("user_id", receiver_id).execute()
        if not subs_res.data:
            return

        payload = {
            "title": f"村岡王国 密書: {sender_name}",
            "body": message_text[:50] + ("..." if len(message_text) > 50 else ""),
            "icon": "/templates/icon.png",
            "url": "/dm"
        }

        for sub in subs_res.data:
            try:
                webpush(
                    subscription_info={
                        "endpoint": sub["endpoint"],
                        "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}
                    },
                    data=json.dumps(payload),
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims=VAPID_CLAIMS
                )
            except WebPushException as e:
                if e.response is not None and e.response.status_code in [404, 410]:
                    await client.table("push_subscriptions").delete().eq("id", sub["id"]).execute()
            except Exception:
                pass
    except Exception:
        pass


# =====================================================================
# 1. ユーザー情報・通知設定・ブロック管理
# =====================================================================
@router.get("/me")
async def get_my_dm_info(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    res = await client.table("profiles").select("id, dm_id, nickname, avatar_drive_id, role").eq("id", user.id).execute()
    if res.data:
        return res.data[0]
    return {"id": user.id, "dm_id": None, "nickname": "名無し国民", "avatar_drive_id": None, "role": "user"}

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
        await client.table("push_subscriptions").update({
            "user_id": user.id, "p256dh": sub.p256dh, "auth": sub.auth
        }).eq("id", exist.data[0]["id"]).execute()
    else:
        await client.table("push_subscriptions").insert({
            "user_id": user.id, "endpoint": sub.endpoint, "p256dh": sub.p256dh, "auth": sub.auth
        }).execute()
    return {"message": "通知設定を有効化しました。"}

# ブロック・ブロック解除
@router.post("/blocks/toggle")
async def toggle_block_user(data: BlockToggleRequest, authorization: str = Header(None)):
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
# 2. 部屋（ルーム）管理: 個人DM & グループ密談部屋
# =====================================================================
@router.get("/rooms")
async def get_my_rooms(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()

    # 自分が参加している部屋を取得
    my_memberships = await client.table("dm_room_members").select("room_id, last_read_at").eq("user_id", user.id).execute()
    if not my_memberships.data:
        return {"rooms": []}

    room_ids = [m["room_id"] for m in my_memberships.data]
    rooms_res = await client.table("dm_rooms").select("*").in_("id", room_ids).order("updated_at", desc=True).execute()
    rooms = rooms_res.data or []

    # 全参加メンバー取得
    all_members_res = await client.table("dm_room_members").select(
        "room_id, user_id, profiles(id, nickname, dm_id, avatar_drive_id)"
    ).in_("room_id", room_ids).execute()
    
    members_by_room = {}
    for m in (all_members_res.data or []):
        r_id = m["room_id"]
        if r_id not in members_by_room:
            members_by_room[r_id] = []
        if m.get("profiles"):
            members_by_room[r_id].append(m["profiles"])

    # 最新メッセージおよび未読件数の計算
    result_rooms = []
    my_read_map = {m["room_id"]: m["last_read_at"] for m in my_memberships.data}

    for r in rooms:
        r_id = r["id"]
        members = members_by_room.get(r_id, [])
        last_read = my_read_map.get(r_id, "1970-01-01T00:00:00Z")

        latest_msg_res = await client.table("dm_messages").select(
            "id, content, message_type, created_at, sender_id, is_deleted"
        ).eq("room_id", r_id).order("created_at", desc=True).limit(1).execute()
        
        latest_msg = latest_msg_res.data[0] if latest_msg_res.data else None

        # 未読数カウント
        unread_res = await client.table("dm_messages").select("id", count="exact").eq("room_id", r_id).gt("created_at", last_read).neq("sender_id", user.id).execute()
        unread_count = unread_res.count if unread_res.count is not None else 0

        # 表示名とアイコンの決定
        display_name = r.get("room_name") or "グループ密談"
        display_avatar = None
        target_user = None
        is_blocked_by_partner = False
        is_blocking_partner = False

        if r["room_type"] == "DIRECT":
            other = next((m for m in members if str(m["id"]) != str(user.id)), None)
            if other:
                display_name = other.get("nickname") or "不明な国民"
                display_avatar = other.get("avatar_drive_id")
                target_user = other

                # ブロック状態チェック
                blk1 = await client.table("dm_blocks").select("blocker_user_id").eq("blocker_user_id", other["id"]).eq("blocked_user_id", user.id).execute()
                is_blocked_by_partner = bool(blk1.data)
                blk2 = await client.table("dm_blocks").select("blocker_user_id").eq("blocker_user_id", user.id).eq("blocked_user_id", other["id"]).execute()
                is_blocking_partner = bool(blk2.data)

        result_rooms.append({
            "id": r_id,
            "room_type": r["room_type"],
            "room_name": display_name,
            "avatar_drive_id": display_avatar,
            "members": members,
            "target_user": target_user,
            "latest_message": latest_msg["content"] if latest_msg else "まだ密書はありません",
            "latest_time": latest_msg["created_at"] if latest_msg else r["created_at"],
            "unread_count": unread_count,
            "is_blocked_by_partner": is_blocked_by_partner,
            "is_blocking_partner": is_blocking_partner
        })

    return {"rooms": result_rooms}

# 1対1 DM部屋の取得または作成
@router.post("/rooms/direct")
async def open_direct_room(data: DirectRoomRequest, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()

    target_res = await client.table("profiles").select("id").eq("dm_id", data.target_dm_id).execute()
    if not target_res.data:
        raise HTTPException(status_code=404, detail="指定されたDM IDの国民は見つかりません。")

    target_id = target_res.data[0]["id"]
    if str(user.id) == str(target_id):
        raise HTTPException(status_code=400, detail="自分自身との密書は開けません。")

    # ブロックされているか確認
    block_chk = await client.table("dm_blocks").select("blocker_user_id").eq("blocker_user_id", target_id).eq("blocked_user_id", user.id).execute()
    if block_chk.data:
        raise HTTPException(status_code=403, detail="この国民には拒絶（ブロック）されているため、密書を開始できません。")

    res = await client.rpc("get_or_create_direct_room", {
        "p_user_a": str(user.id),
        "p_user_b": str(target_id)
    }).execute()

    return {"room_id": res.data}

# グループ密談部屋の新規作成
@router.post("/rooms/group")
async def create_group_room(data: GroupRoomRequest, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()

    # メンバーDM IDからUUIDを取得
    prof_res = await client.table("profiles").select("id").in_("dm_id", data.member_dm_ids).execute()
    member_ids = list(set([p["id"] for p in (prof_res.data or []) if str(p["id"]) != str(user.id)]))

    if not member_ids:
        raise HTTPException(status_code=400, detail="有効な招待メンバーを指定してください。")

    room_ins = await client.table("dm_rooms").insert({
        "room_type": "GROUP",
        "room_name": data.room_name.strip(),
        "owner_user_id": str(user.id)
    }).execute()

    room_id = room_ins.data[0]["id"]
    members_data = [{"room_id": room_id, "user_id": str(user.id)}]
    for mid in member_ids:
        members_data.append({"room_id": room_id, "user_id": str(mid)})

    await client.table("dm_room_members").insert(members_data).execute()
    return {"room_id": room_id, "message": f"密談部屋「{data.room_name}」を結成しました。"}


# =====================================================================
# 3. メッセージ送受信・送信取消・リアクション・ピン留め
# =====================================================================
@router.get("/rooms/{room_id}/messages")
async def get_room_messages(room_id: int, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()

    # 部屋参加権限チェック
    mem_chk = await client.table("dm_room_members").select("user_id").eq("room_id", room_id).eq("user_id", user.id).execute()
    if not mem_chk.data and not await is_king_user(user.id):
        raise HTTPException(status_code=403, detail="この部屋を閲覧する権限がありません。")

    # 既読時刻を更新
    await client.table("dm_room_members").update({"last_read_at": datetime.utcnow().isoformat()}).eq("room_id", room_id).eq("user_id", user.id).execute()

    # メッセージ取得
    msgs_res = await client.table("dm_messages").select(
        "id, room_id, sender_id, message_type, content, metadata, is_pinned, is_deleted, created_at"
    ).eq("room_id", room_id).order("created_at", desc=False).limit(200).execute()
    
    msgs = msgs_res.data or []
    msg_ids = [m["id"] for m in msgs]

    # リアクション取得
    reactions_map = {}
    if msg_ids:
        react_res = await client.table("dm_message_reactions").select("message_id, reaction_type, user_id").in_("message_id", msg_ids).execute()
        for r in (react_res.data or []):
            m_id = r["message_id"]
            if m_id not in reactions_map:
                reactions_map[m_id] = {}
            rtype = r["reaction_type"]
            reactions_map[m_id][rtype] = reactions_map[m_id].get(rtype, 0) + 1

    # 各メッセージの既読（1対1 DMの場合、相手の last_read_at と照合）
    other_member_res = await client.table("dm_room_members").select("user_id, last_read_at").eq("room_id", room_id).neq("user_id", user.id).execute()
    other_read_times = [m["last_read_at"] for m in (other_member_res.data or []) if m.get("last_read_at")]

    # 送信者プロフィール紐付け
    sender_ids = list(set([m["sender_id"] for m in msgs]))
    prof_map = {}
    if sender_ids:
        profs = await client.table("profiles").select("id, nickname, dm_id, avatar_drive_id").in_("id", sender_ids).execute()
        prof_map = {p["id"]: p for p in (profs.data or [])}

    for m in msgs:
        m["sender"] = prof_map.get(m["sender_id"], {"nickname": "不明", "avatar_drive_id": None})
        m["reactions"] = reactions_map.get(m["id"], {})
        # 既読判定（自分以外のメンバーの既読時刻がメッセージ作成時刻以降であればTrue）
        m["is_read"] = any(read_time >= m["created_at"] for read_time in other_read_times) if other_read_times else False

    return {"messages": msgs}

# メッセージ送信（テキスト、スタンプ、画像、通話ログ）
@router.post("/rooms/messages/send")
async def send_message(data: MessageSendRequest, background_tasks: BackgroundTasks, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()

    try:
        res = await client.rpc("send_dm_message", {
            "p_sender_id": str(user.id),
            "p_room_id": data.room_id,
            "p_message_type": data.message_type,
            "p_content": data.content.strip(),
            "p_metadata": data.metadata
        }).execute()

        # バックグラウンド通知
        other_members = await client.table("dm_room_members").select("user_id").eq("room_id", data.room_id).neq("user_id", user.id).execute()
        sender_prof = await client.table("profiles").select("nickname").eq("id", user.id).execute()
        s_name = sender_prof.data[0]["nickname"] if sender_prof.data else "密書相手"

        for m in (other_members.data or []):
            background_tasks.add_task(send_web_push, m["user_id"], s_name, data.content.strip())

        return {"message": "密書を送信しました。", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

# 画像のアップロード送信
@router.post("/rooms/{room_id}/upload-image")
async def upload_chat_image(room_id: int, file: UploadFile = File(...), authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    file.file.seek(0, os.SEEK_END)
    file_size = file.file.tell()
    file.file.seek(0)
    mime_type = file.content_type or "image/jpeg"

    drive_id = await upload_image_to_drive(file.file, file.filename, mime_type, file_size)
    client = await get_supabase()

    res = await client.rpc("send_dm_message", {
        "p_sender_id": str(user.id),
        "p_room_id": room_id,
        "p_message_type": "IMAGE",
        "p_content": "🖼️ 画像を受信しました",
        "p_metadata": {"drive_file_id": drive_id}
    }).execute()

    return {"message": "画像を送信しました。", "drive_file_id": drive_id}

# 送信取り消し（修復版: 本人または国王のみ取消可能）
@router.delete("/messages/{message_id}")
async def cancel_message(message_id: int, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    try:
        res = await client.rpc("cancel_dm_message", {
            "p_user_id": str(user.id),
            "p_message_id": message_id
        }).execute()
        return {"message": "メッセージの送信を取り消しました。"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

# リアクション切替（👍 ❤️ 😂 🙏 👀 🔥）
@router.post("/messages/{message_id}/react")
async def toggle_reaction(message_id: int, data: ReactionToggleRequest, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    exist = await client.table("dm_message_reactions").select("*").eq("message_id", message_id).eq("user_id", user.id).eq("reaction_type", data.reaction_type).execute()
    if exist.data:
        await client.table("dm_message_reactions").delete().eq("message_id", message_id).eq("user_id", user.id).eq("reaction_type", data.reaction_type).execute()
        return {"message": "リアクションを解除しました。"}
    else:
        await client.table("dm_message_reactions").insert({"message_id": message_id, "user_id": user.id, "reaction_type": data.reaction_type}).execute()
        return {"message": "リアクションしました。"}

# メッセージピン留め / 解除
@router.post("/messages/{message_id}/pin")
async def toggle_pin_message(message_id: int, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    msg_res = await client.table("dm_messages").select("is_pinned").eq("id", message_id).execute()
    if not msg_res.data:
        raise HTTPException(status_code=404, detail="メッセージが見つかりません。")
    new_state = not msg_res.data[0]["is_pinned"]
    await client.table("dm_messages").update({
        "is_pinned": new_state,
        "pinned_by": str(user.id) if new_state else None,
        "pinned_at": datetime.utcnow().isoformat() if new_state else None
    }).eq("id", message_id).execute()
    return {"message": "ピン留めを更新しました。", "is_pinned": new_state}

# 国王への密告（通報）
@router.post("/reports")
async def report_dm_message(data: ReportRequest, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    msg_res = await client.table("dm_messages").select("sender_id").eq("id", data.message_id).execute()
    if not msg_res.data:
        raise HTTPException(status_code=404, detail="対象メッセージが見つかりません。")
    
    await client.table("dm_reports").insert({
        "message_id": data.message_id,
        "reporter_id": str(user.id),
        "reported_user_id": msg_res.data[0]["sender_id"],
        "reason": data.reason.strip()
    }).execute()
    return {"message": "国王へ密告しました。調査が行われます。"}


# =====================================================================
# 4. チャット内 Gold 送金
# =====================================================================
@router.post("/transfer-gold")
async def transfer_gold_in_chat(data: TransferGoldRequest, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    try:
        res = await client.rpc("execute_dm_transfer", {
            "p_sender_id": str(user.id),
            "p_sender_wallet_id": data.sender_wallet_id,
            "p_target_user_id": data.target_user_id,
            "p_room_id": data.room_id,
            "p_amount": data.amount
        }).execute()
        return {"message": f"{data.amount} Gold を送金しました！", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))


# =====================================================================
# 5. スタンプ自作・販売・所持
# =====================================================================
# スタンプパック作成
@router.post("/stamps/packs")
async def create_stamp_pack(data: StampPackCreate, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    pack_res = await client.table("dm_stamp_packs").insert({
        "creator_user_id": str(user.id),
        "title": data.title.strip(),
        "description": data.description.strip(),
        "price": data.price
    }).execute()
    pack_id = pack_res.data[0]["id"]
    # 作成者は自動所持
    await client.table("user_dm_stamps").insert({"user_id": str(user.id), "pack_id": pack_id}).execute()
    return {"message": "スタンプパックを作成しました！画像を追加してください。", "pack_id": pack_id}

# スタンプ画像の追加アップロード（正方形クロップ済み画像を受信）
@router.post("/stamps/packs/{pack_id}/add-stamp")
async def add_stamp_image(pack_id: int, file: UploadFile = File(...), authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    pack_res = await client.table("dm_stamp_packs").select("creator_user_id").eq("id", pack_id).execute()
    if not pack_res.data or pack_res.data[0]["creator_user_id"] != str(user.id):
        raise HTTPException(status_code=403, detail="このパックを編集する権限がありません。")

    file.file.seek(0, os.SEEK_END)
    file_size = file.file.tell()
    file.file.seek(0)
    mime_type = file.content_type or "image/png"

    drive_id = await upload_image_to_drive(file.file, file.filename, mime_type, file_size)
    await client.table("dm_stamps").insert({"pack_id": pack_id, "drive_file_id": drive_id}).execute()
    return {"message": "スタンプ画像を追加しました！", "drive_file_id": drive_id}

# 販売中のスタンプ一覧取得
@router.get("/stamps/market")
async def get_stamp_market(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    packs_res = await client.table("dm_stamp_packs").select("*, dm_stamps(drive_file_id)").eq("is_public", True).order("created_at", desc=True).execute()
    owned_res = await client.table("user_dm_stamps").select("pack_id").eq("user_id", user.id).execute()
    owned_pack_ids = set([o["pack_id"] for o in (owned_res.data or [])])

    packs = packs_res.data or []
    for p in packs:
        p["is_owned"] = p["id"] in owned_pack_ids
    return {"packs": packs}

# 自分が所持しているスタンプ一覧（ピッカー用）
@router.get("/stamps/my-stamps")
async def get_my_stamps(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    owned = await client.table("user_dm_stamps").select("pack_id").eq("user_id", user.id).execute()
    if not owned.data:
        return {"packs": []}

    pack_ids = [o["pack_id"] for o in owned.data]
    packs_res = await client.table("dm_stamp_packs").select("id, title, dm_stamps(id, drive_file_id)").in_("id", pack_ids).execute()
    return {"packs": packs_res.data or []}

# スタンプパック購入
@router.post("/stamps/buy")
async def buy_stamp_pack(data: StampBuyRequest, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    try:
        res = await client.rpc("buy_dm_stamp_pack", {
            "p_buyer_id": str(user.id),
            "p_buyer_wallet_id": data.buyer_wallet_id,
            "p_pack_id": data.pack_id
        }).execute()
        return {"message": res.data.get("message", "スタンプを購入しました！")}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))


# =====================================================================
# 6. WebRTC 音声・ビデオ通話シグナリング
# =====================================================================
@router.post("/call/signal")
async def send_call_signal(data: CallSignalRequest, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    
    # 参加メンバーかチェック
    mem_chk = await client.table("dm_room_members").select("user_id").eq("room_id", data.room_id).eq("user_id", user.id).execute()
    if not mem_chk.data:
        raise HTTPException(status_code=403, detail="この部屋で通話を開始・送受信する権限がありません。")

    await client.table("dm_call_signals").insert({
        "room_id": data.room_id,
        "sender_id": str(user.id),
        "target_id": data.target_id,
        "signal_type": data.signal_type,
        "signal_data": data.signal_data
    }).execute()
    return {"message": "シグナルを中継しました。"}

@router.get("/call/signals/{room_id}")
async def poll_call_signals(room_id: int, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()

    # 直近30秒以内の、自分宛てまたは全員宛てのシグナルを取得
    now_minus_30s = datetime.fromtimestamp(datetime.utcnow().timestamp() - 30).isoformat()
    cond = f"target_id.eq.{user.id},target_id.is.null"

    res = await client.table("dm_call_signals").select("*") \
        .eq("room_id", room_id) \
        .neq("sender_id", user.id) \
        .or_(cond) \
        .gt("created_at", now_minus_30s) \
        .order("created_at", desc=False).execute()

    return {"signals": res.data or []}
