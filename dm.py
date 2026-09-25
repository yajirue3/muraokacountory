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

class DMRequest(BaseModel):
    target_dm_id: str
    message_type: str = Field("TEXT", pattern="^(TEXT|IMAGE|STAMP|TRANSFER|CALL_LOG)$")
    content: str = Field(..., min_length=1, max_length=1000)
    metadata: Dict[str, Any] = Field(default_factory=dict)

class ReactionToggleRequest(BaseModel):
    reaction_type: str = Field(..., pattern="^(👍|❤️|😂|🙏|👀|🔥)$")

class TransferGoldRequest(BaseModel):
    target_user_id: str
    sender_wallet_id: str
    amount: int = Field(..., gt=0, le=100_000_000)

class BlockToggleRequest(BaseModel):
    target_user_id: str

class ReportRequest(BaseModel):
    message_id: int
    reason: str = Field("", max_length=200)

class CallSignalRequest(BaseModel):
    target_id: str
    signal_type: str = Field(..., pattern="^(OFFER|ANSWER|ICE_CANDIDATE|JOIN_CALL|LEAVE_CALL|CALL_REJECT)$")
    signal_data: Dict[str, Any]


# --- Helpers ---
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
# API: ユーザー情報・通知設定・ブロック
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
# API: メッセージ操作
# =====================================================================
@router.post("/send")
async def send_dm(data: DMRequest, background_tasks: BackgroundTasks, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()

    try:
        target_res = await client.table("profiles").select("id").eq("dm_id", data.target_dm_id).execute()
        if not target_res.data:
            raise HTTPException(status_code=404, detail="指定されたDM IDのユーザーは見つかりません。")
            
        target_id = target_res.data[0]["id"]
        if str(user.id) == str(target_id):
            raise HTTPException(status_code=400, detail="自分自身には送信できません。")

        # ブロック確認
        block_chk = await client.table("dm_blocks").select("blocker_user_id").eq("blocker_user_id", target_id).eq("blocked_user_id", user.id).execute()
        if block_chk.data:
            raise HTTPException(status_code=403, detail="この国民には拒絶（ブロック）されているため、密書を送信できません。")

        await client.table("direct_messages").insert({
            "sender_id": user.id,
            "receiver_id": target_id,
            "message_type": data.message_type,
            "content": data.content.strip(),
            "metadata": data.metadata
        }).execute()

        prof = await client.table("profiles").select("nickname").eq("id", user.id).execute()
        sender_name = prof.data[0]["nickname"] if (prof.data and prof.data[0].get("nickname")) else "不明な国民"
        
        background_tasks.add_task(send_web_push, target_id, sender_name, data.content.strip())
        return {"message": "送信完了しました。"}
    except APIError as e:
        raise HTTPException(status_code=400, detail=f"DBエラー: {e.message}")
    except HTTPException:
        raise

@router.get("/conversations")
async def get_conversations(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    
    filter_str = f"sender_id.eq.{user.id},receiver_id.eq.{user.id}"
    msgs = await client.table("direct_messages").select(
        "id, sender_id, receiver_id, message_type, content, is_read, is_deleted, created_at"
    ).or_(filter_str).order("created_at", desc=True).execute()

    if not msgs.data:
        return {"conversations": []}

    partner_ids = set()
    for m in msgs.data:
        partner_ids.add(m["receiver_id"] if m["sender_id"] == user.id else m["sender_id"])

    profiles_res = await client.table("profiles").select("id, nickname, dm_id, avatar_drive_id").in_("id", list(partner_ids)).execute()
    prof_map = {p["id"]: p for p in (profiles_res.data or [])}

    blocks_res = await client.table("dm_blocks").select("*").or_(f"blocker_user_id.eq.{user.id},blocked_user_id.eq.{user.id}").execute()
    blocks = blocks_res.data or []

    convs = {}
    for msg in msgs.data:
        is_sender = (msg["sender_id"] == user.id)
        partner_id = msg["receiver_id"] if is_sender else msg["sender_id"]
        
        p_info = prof_map.get(partner_id)
        if not p_info or not p_info.get("dm_id"):
            continue

        if partner_id not in convs:
            is_blocked_by_partner = any(b["blocker_user_id"] == partner_id and b["blocked_user_id"] == user.id for b in blocks)
            is_blocking_partner = any(b["blocker_user_id"] == user.id and b["blocked_user_id"] == partner_id for b in blocks)

            display_msg = "送信は取り消されました" if msg["is_deleted"] else msg["content"]
            
            convs[partner_id] = {
                "partner_user_id": partner_id,
                "partner_dm_id": p_info.get("dm_id"),
                "partner_name": p_info.get("nickname") or "名無し国民",
                "partner_avatar": p_info.get("avatar_drive_id"),
                "latest_message": display_msg,
                "latest_time": msg["created_at"],
                "unread_count": 0,
                "is_blocked_by_partner": is_blocked_by_partner,
                "is_blocking_partner": is_blocking_partner
            }
        
        if not is_sender and not msg.get("is_read", False):
            convs[partner_id]["unread_count"] += 1

    return {"conversations": list(convs.values())}

@router.get("/messages/{partner_dm_id}")
async def get_messages(partner_dm_id: str, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    
    target_res = await client.table("profiles").select("id").eq("dm_id", partner_dm_id).execute()
    if not target_res.data:
        return {"messages": [], "partner_user_id": None}
    partner_id = target_res.data[0]["id"]
    
    await client.table("direct_messages").update({"is_read": True}).eq("sender_id", partner_id).eq("receiver_id", user.id).eq("is_read", False).execute()

    cond = f"and(sender_id.eq.{user.id},receiver_id.eq.{partner_id}),and(sender_id.eq.{partner_id},receiver_id.eq.{user.id})"
    res = await client.table("direct_messages").select(
        "id, sender_id, receiver_id, message_type, content, metadata, is_pinned, is_read, is_deleted, created_at"
    ).or_(cond).order("created_at", desc=False).limit(200).execute()

    msgs = res.data or []
    msg_ids = [m["id"] for m in msgs]

    reactions_map = {}
    if msg_ids:
        react_res = await client.table("dm_message_reactions").select("message_id, reaction_type, user_id").in_("message_id", msg_ids).execute()
        for r in (react_res.data or []):
            m_id = r["message_id"]
            if m_id not in reactions_map: reactions_map[m_id] = {}
            rtype = r["reaction_type"]
            reactions_map[m_id][rtype] = reactions_map[m_id].get(rtype, 0) + 1

    for m in msgs:
        m["reactions"] = reactions_map.get(m["id"], {})

    return {"messages": msgs, "partner_user_id": partner_id}

@router.post("/messages/{partner_dm_id}/upload-image")
async def upload_chat_image(partner_dm_id: str, file: UploadFile = File(...), authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    target_res = await client.table("profiles").select("id").eq("dm_id", partner_dm_id).execute()
    if not target_res.data: raise HTTPException(status_code=404, detail="対象が見つかりません。")
    target_id = target_res.data[0]["id"]

    block_chk = await client.table("dm_blocks").select("blocker_user_id").eq("blocker_user_id", target_id).eq("blocked_user_id", user.id).execute()
    if block_chk.data: raise HTTPException(status_code=403, detail="ブロックされています。")

    file.file.seek(0, os.SEEK_END)
    file_size = file.file.tell()
    file.file.seek(0)
    mime_type = file.content_type or "image/jpeg"

    drive_id = await upload_image_to_drive(file.file, file.filename, mime_type, file_size)

    await client.table("direct_messages").insert({
        "sender_id": user.id, "receiver_id": target_id,
        "message_type": "IMAGE", "content": "🖼️ 画像を受信しました",
        "metadata": {"drive_file_id": drive_id}
    }).execute()
    return {"message": "画像を送信しました。", "drive_file_id": drive_id}

@router.delete("/messages/{message_id}")
async def cancel_message(message_id: int, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    try:
        res = await client.rpc("cancel_dm_message", {"p_user_id": str(user.id), "p_message_id": message_id}).execute()
        return {"message": "メッセージの送信を取り消しました。"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

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

@router.post("/messages/{message_id}/pin")
async def toggle_pin_message(message_id: int, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    msg_res = await client.table("direct_messages").select("is_pinned").eq("id", message_id).execute()
    if not msg_res.data: raise HTTPException(status_code=404, detail="メッセージが見つかりません。")
    new_state = not msg_res.data[0]["is_pinned"]
    await client.table("direct_messages").update({"is_pinned": new_state}).eq("id", message_id).execute()
    return {"message": "ピン留めを更新しました。", "is_pinned": new_state}

@router.post("/reports")
async def report_dm_message(data: ReportRequest, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    msg_res = await client.table("direct_messages").select("sender_id").eq("id", data.message_id).execute()
    if not msg_res.data: raise HTTPException(status_code=404, detail="対象メッセージが見つかりません。")
    await client.table("dm_reports").insert({
        "message_id": data.message_id, "reporter_id": str(user.id),
        "reported_user_id": msg_res.data[0]["sender_id"], "reason": data.reason.strip()
    }).execute()
    return {"message": "国王へ密告しました。調査が行われます。"}

@router.post("/transfer-gold")
async def transfer_gold_in_chat(data: TransferGoldRequest, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    try:
        res = await client.rpc("execute_dm_transfer", {
            "p_sender_id": str(user.id), "p_sender_wallet_id": data.sender_wallet_id,
            "p_target_user_id": data.target_user_id, "p_amount": data.amount
        }).execute()
        return {"message": f"{data.amount} Gold を送金しました！", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))


# =====================================================================
# API: 国王専用 監視ツール（そのまま維持）
# =====================================================================
@router.get("/admin/threads")
async def admin_get_all_threads(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    if not await is_king_user(user.id): raise HTTPException(status_code=403, detail="権限がありません。")
    client = await get_supabase()
    
    msgs = await client.table("direct_messages").select("id, sender_id, receiver_id, content, is_deleted, created_at").order("created_at", desc=True).limit(500).execute()
    if not msgs.data: return {"threads": []}

    u_ids = set()
    for m in msgs.data:
        u_ids.add(m["sender_id"])
        u_ids.add(m["receiver_id"])

    profs = await client.table("profiles").select("id, nickname, dm_id").in_("id", list(u_ids)).execute()
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
                "latest_message": "送信取消済" if msg["is_deleted"] else msg["content"],
                "latest_time": msg["created_at"], "msg_count": 1
            }
        else:
            threads[thread_key]["msg_count"] += 1

    return {"threads": list(threads.values())}

@router.get("/admin/messages/{user_a_id}/{user_b_id}")
async def admin_get_thread_messages(user_a_id: str, user_b_id: str, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    if not await is_king_user(user.id): raise HTTPException(status_code=403, detail="権限がありません。")
    client = await get_supabase()
    
    cond = f"and(sender_id.eq.{user_a_id},receiver_id.eq.{user_b_id}),and(sender_id.eq.{user_b_id},receiver_id.eq.{user_a_id})"
    res = await client.table("direct_messages").select("id, sender_id, receiver_id, message_type, content, metadata, is_deleted, created_at").or_(cond).order("created_at", desc=False).limit(200).execute()
    msgs = res.data or []
    
    profs = await client.table("profiles").select("id, nickname").in_("id", [user_a_id, user_b_id]).execute()
    p_map = {p["id"]: p["nickname"] for p in (profs.data or [])}
    for m in msgs: m["sender"] = {"nickname": p_map.get(m["sender_id"], "不明")}

    return {"messages": msgs}

@router.delete("/admin/messages/{message_id}")
async def admin_delete_message(message_id: int, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    if not await is_king_user(user.id): raise HTTPException(status_code=403, detail="権限がありません。")
    client = await get_supabase()
    try:
        await client.rpc("cancel_dm_message", {"p_user_id": str(user.id), "p_message_id": message_id}).execute()
        return {"message": "国王権限によりメッセージの送信を取り消しました。"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))


# =====================================================================
# API: WebRTC シグナリング
# =====================================================================
@router.post("/call/signal")
async def send_call_signal(data: CallSignalRequest, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    await client.table("dm_call_signals").insert({
        "sender_id": str(user.id),
        "target_id": data.target_id,
        "signal_type": data.signal_type,
        "signal_data": data.signal_data
    }).execute()
    return {"message": "シグナルを中継しました。"}

@router.get("/call/signals/{target_user_id}")
async def poll_call_signals(target_user_id: str, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    now_minus_30s = datetime.fromtimestamp(datetime.utcnow().timestamp() - 30).isoformat()
    
    res = await client.table("dm_call_signals").select("*") \
        .eq("target_id", user.id) \
        .eq("sender_id", target_user_id) \
        .gt("created_at", now_minus_30s) \
        .order("created_at", desc=False).execute()

    return {"signals": res.data or []}
