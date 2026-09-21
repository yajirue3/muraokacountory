import os
import json
import re
from datetime import datetime
from fastapi import APIRouter, Header, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from pywebpush import webpush, WebPushException
from db import get_supabase

router = APIRouter()

# --- VAPIDキー設定 ---
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "YOUR_PUBLIC_KEY_HERE")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "YOUR_PRIVATE_KEY_HERE")
VAPID_CLAIMS = {"sub": "mailto:admin@example.com"}

# --- Models ---
class DMIDUpdate(BaseModel):
    dm_id: str = Field(..., min_length=3, max_length=20, description="3文字以上20文字以内")

class DMRequest(BaseModel):
    target_dm_id: str
    content: str = Field(..., min_length=1, max_length=500, description="1文字以上500文字以内")

class PushSubscription(BaseModel):
    endpoint: str
    p256dh: str
    auth: str

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
    client = await get_supabase()
    res = await client.table("profiles").select("role").eq("id", user_id).execute()
    return bool(res.data and res.data[0].get("role") == "king")

# --- Background Task: 通知送信 ---
async def send_web_push(receiver_id: str, sender_name: str, message_text: str):
    if VAPID_PRIVATE_KEY == "YOUR_PRIVATE_KEY_HERE":
        print(f"[{datetime.now()}] 警告: VAPID_PRIVATE_KEY が未設定のためPush通知をスキップ")
        return

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
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}
                },
                data=json.dumps(payload),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS
            )
        except WebPushException as e:
            if e.response is not None and e.response.status_code == 410:
                await client.table("push_subscriptions").delete().eq("id", sub["id"]).execute()
            print(f"[{datetime.now()}] WebPush送信エラー: {e}")
        except Exception as e:
            print(f"[{datetime.now()}] 予期せぬPushエラー: {e}")

# =====================================================================
# API: ユーザー情報・通知設定
# =====================================================================
@router.get("/me")
async def get_my_dm_info(authorization: str = Header(None)):
    """自分のDM IDと権限(Role)を取得"""
    user = await get_user_auth(authorization)
    client = await get_supabase()
    res = await client.table("profiles").select("dm_id, role").eq("id", user.id).execute()
    if res.data:
        return {"dm_id": res.data[0].get("dm_id"), "role": res.data[0].get("role")}
    return {"dm_id": None, "role": "user"}

@router.post("/set-id")
async def set_dm_id(data: DMIDUpdate, authorization: str = Header(None)):
    """DM ID (@ユーザー名) を設定"""
    user = await get_user_auth(authorization)
    
    if not re.match(r"^[a-zA-Z0-9_]+$", data.dm_id):
        raise HTTPException(status_code=400, detail="DM IDは半角英数字とアンダースコア(_)のみ使用可能です。")
        
    client = await get_supabase()
    
    # 他人が使っていないか重複チェック
    exist = await client.table("profiles").select("id").eq("dm_id", data.dm_id).neq("id", user.id).execute()
    if exist.data:
        raise HTTPException(status_code=400, detail="そのIDは既に他の国民が使用しています。")
        
    await client.table("profiles").update({"dm_id": data.dm_id}).eq("id", user.id).execute()
    return {"message": f"DM IDを @{data.dm_id} に設定しました！"}

@router.get("/vapid-public-key")
async def get_vapid_public_key():
    return {"public_key": VAPID_PUBLIC_KEY}

@router.post("/subscribe")
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

# =====================================================================
# API: メッセージ操作（一般）
# =====================================================================
@router.post("/send")
async def send_dm(data: DMRequest, background_tasks: BackgroundTasks, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    
    # DM IDから相手のUUIDを特定
    target_res = await client.table("profiles").select("id").eq("dm_id", data.target_dm_id).execute()
    if not target_res.data:
        raise HTTPException(status_code=404, detail="指定されたDM IDのユーザーは見つかりません。")
        
    target_id = target_res.data[0]["id"]
    if user.id == target_id:
        raise HTTPException(status_code=400, detail="自分自身には送信できません。")

    res = await client.table("direct_messages").insert({
        "sender_id": user.id,
        "receiver_id": target_id,
        "content": data.content.strip()
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
    msgs = await client.table("direct_messages").select(
        "id, sender_id, receiver_id, content, is_read, created_at, sender:sender_id(nickname, dm_id), receiver:receiver_id(nickname, dm_id)"
    ).or_(filter_str).order("created_at", desc=True).execute()

    if not msgs.data:
        return {"conversations": []}

    convs = {}
    for msg in msgs.data:
        is_sender = (msg["sender_id"] == user.id)
        partner_id = msg["receiver_id"] if is_sender else msg["sender_id"]
        
        if partner_id not in convs:
            p_data = msg["receiver"] if is_sender else msg["sender"]
            if not p_data.get("dm_id"): continue # ID未設定のユーザーはスキップ
            
            convs[partner_id] = {
                "partner_dm_id": p_data.get("dm_id"),
                "partner_name": p_data.get("nickname"),
                "latest_message": msg["content"],
                "latest_time": msg["created_at"],
                "unread_count": 0
            }
        
        if not is_sender and not msg["is_read"]:
            convs[partner_id]["unread_count"] += 1

    return {"conversations": list(convs.values())}

@router.get("/messages/{partner_dm_id}")
async def get_messages(partner_dm_id: str, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    
    target_res = await client.table("profiles").select("id").eq("dm_id", partner_dm_id).execute()
    if not target_res.data:
        return {"messages": []}
    partner_id = target_res.data[0]["id"]
    
    # 既読化
    await client.table("direct_messages").update({"is_read": True}).eq("sender_id", partner_id).eq("receiver_id", user.id).eq("is_read", False).execute()

    filter_str1 = f"and(sender_id.eq.{user.id},receiver_id.eq.{partner_id})"
    filter_str2 = f"and(sender_id.eq.{partner_id},receiver_id.eq.{user.id})"
    
    res = await client.table("direct_messages").select(
        "id, sender_id, receiver_id, content, is_read, created_at"
    ).or_(f"{filter_str1},{filter_str2}").order("created_at", desc=False).limit(200).execute()
    
    return {"messages": res.data or [], "partner_user_id": partner_id}

# =====================================================================
# API: 国王専用 監視・検閲ツール (Zero Trust)
# =====================================================================
@router.get("/admin/threads")
async def admin_get_all_threads(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    if not await is_king_user(user.id):
        raise HTTPException(status_code=403, detail="権限がありません。")
        
    client = await get_supabase()
    msgs = await client.table("direct_messages").select(
        "id, sender_id, receiver_id, content, created_at, sender:sender_id(nickname, dm_id), receiver:receiver_id(nickname, dm_id)"
    ).order("created_at", desc=True).limit(500).execute()

    threads = {}
    for msg in (msgs.data or []):
        users = sorted([msg["sender_id"], msg["receiver_id"]])
        thread_key = f"{users[0]}_{users[1]}"
        
        if thread_key not in threads:
            threads[thread_key] = {
                "user_a_id": msg["sender_id"],
                "user_a_name": msg["sender"]["nickname"],
                "user_a_dm_id": msg["sender"]["dm_id"],
                "user_b_id": msg["receiver_id"],
                "user_b_name": msg["receiver"]["nickname"],
                "user_b_dm_id": msg["receiver"]["dm_id"],
                "latest_message": msg["content"],
                "latest_time": msg["created_at"],
                "msg_count": 1
            }
        else:
            threads[thread_key]["msg_count"] += 1

    return {"threads": list(threads.values())}

@router.get("/admin/messages/{user_a_id}/{user_b_id}")
async def admin_get_thread_messages(user_a_id: str, user_b_id: str, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    if not await is_king_user(user.id):
        raise HTTPException(status_code=403, detail="権限がありません。")
        
    client = await get_supabase()
    filter_str1 = f"and(sender_id.eq.{user_a_id},receiver_id.eq.{user_b_id})"
    filter_str2 = f"and(sender_id.eq.{user_b_id},receiver_id.eq.{user_a_id})"
    
    res = await client.table("direct_messages").select(
        "id, sender_id, receiver_id, content, created_at, sender:sender_id(nickname)"
    ).or_(f"{filter_str1},{filter_str2}").order("created_at", desc=False).limit(200).execute()
    
    return {"messages": res.data or []}
