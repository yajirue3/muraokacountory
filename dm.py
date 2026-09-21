import os
import json
import re
import traceback
from datetime import datetime
from fastapi import APIRouter, Header, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from pywebpush import webpush, WebPushException
from postgrest.exceptions import APIError
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
        print("[Auth Error] Authorizationヘッダーがないか、Formatが不正です。")
        raise HTTPException(status_code=401, detail="認証トークンがありません")
    token = authorization.split(" ")[1]
    client = await get_supabase()
    try:
        res = await client.auth.get_user(token)
        return res.user
    except Exception as e:
        print(f"[Auth Error] get_user 認証失敗: {e}")
        raise HTTPException(status_code=401, detail="無効なトークンです")

async def is_king_user(user_id: str) -> bool:
    try:
        client = await get_supabase()
        res = await client.table("profiles").select("role").eq("id", user_id).execute()
        return bool(res.data and res.data[0].get("role") == "king")
    except Exception as e:
        print(f"[Role Check Error] 国王権限確認エラー: {e}")
        return False

# --- Background Task: 通知送信 (デバッグログ強化版) ---
async def send_web_push(receiver_id: str, sender_name: str, message_text: str):
    print(f"\n================ [WebPush Task Start] ================")
    print(f"[WebPush] Receiver User ID: {receiver_id}")
    print(f"[WebPush] Sender Name: {sender_name}")
    print(f"[WebPush] Message Preview: {message_text[:20]}")

    if VAPID_PRIVATE_KEY in ["YOUR_PRIVATE_KEY_HERE", "YOUR_PUBLIC_KEY_HERE", ""]:
        print("[WebPush ERROR] VAPID_PRIVATE_KEY が環境変数に設定されていないかデフォルト値のままです。")
        print("======================================================\n")
        return

    try:
        client = await get_supabase()
        print(f"[WebPush] DBから receiver_id ({receiver_id}) の購読情報を検索中...")
        subs_res = await client.table("push_subscriptions").select("*").eq("user_id", receiver_id).execute()
        
        if not subs_res.data:
            print(f"[WebPush ERROR] 受信者 ({receiver_id}) の通知購読データ (push_subscriptions) がDBに存在しません。")
            print("======================================================\n")
            return 
            
        print(f"[WebPush] 取得された購読データ件数: {len(subs_res.data)}件")

        payload = {
            "title": f"村岡王国: {sender_name} からの密書",
            "body": message_text[:50] + ("..." if len(message_text) > 50 else ""),
            "icon": "/templates/icon.png", 
            "url": "/dm" 
        }

        for idx, sub in enumerate(subs_res.data):
            print(f"\n[WebPush Send Target #{idx+1}] ID: {sub.get('id')}")
            print(f"  Endpoint: {sub['endpoint'][:40]}...")
            print(f"  p256dh  : {sub['p256dh'][:20]}...")
            print(f"  auth    : {sub['auth'][:10]}...")

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
                print(f"[WebPush SUCCESS] Target #{idx+1} への通知送信が完了しました！")

            except WebPushException as e:
                print(f"[WebPush EXCEPTION] WebPushException 発生 (Target #{idx+1}): {e}")
                if e.response is not None:
                    print(f"  HTTP Status Code: {e.response.status_code}")
                    print(f"  Response Text   : {e.response.text}")
                    if e.response.status_code in [404, 410]:
                        print(f"  -> 無効なEndpointのためDBから削除します (ID: {sub['id']})")
                        await client.table("push_subscriptions").delete().eq("id", sub["id"]).execute()
                else:
                    print("  e.response は None です")

            except Exception as e:
                print(f"[WebPush EXCEPTION] 想定外のエラー (Target #{idx+1}): {e}")
                print(traceback.format_exc())

    except Exception as e:
        print(f"[WebPush CRITICAL ERROR] send_web_push 全体エラー: {e}")
        print(traceback.format_exc())
        
    print("================ [WebPush Task End] ==================\n")

# =====================================================================
# API: ユーザー情報・通知設定
# =====================================================================
@router.get("/me")
async def get_my_dm_info(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    try:
        res = await client.table("profiles").select("id, dm_id, role").eq("id", user.id).execute()
        if res.data:
            return {"id": res.data[0].get("id"), "dm_id": res.data[0].get("dm_id"), "role": res.data[0].get("role")}
        return {"id": user.id, "dm_id": None, "role": "user"}
    except APIError as e:
        print(f"[API Error /me]: {e.message}")
        raise HTTPException(status_code=400, detail=f"DBエラー: {e.message}")

@router.post("/set-id")
async def set_dm_id(data: DMIDUpdate, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    
    if not re.match(r"^[a-zA-Z0-9_]+$", data.dm_id):
        raise HTTPException(status_code=400, detail="DM IDは半角英数字とアンダースコア(_)のみ使用可能です。")
        
    client = await get_supabase()
    try:
        exist = await client.table("profiles").select("id").eq("dm_id", data.dm_id).neq("id", user.id).execute()
        if exist.data:
            raise HTTPException(status_code=400, detail="そのIDは既に他の国民が使用しています。")
            
        await client.table("profiles").update({"dm_id": data.dm_id}).eq("id", user.id).execute()
        return {"message": f"DM IDを @{data.dm_id} に設定しました！"}
    except APIError as e:
        print(f"[API Error /set-id]: {e.message}")
        raise HTTPException(status_code=400, detail=f"DBエラー: {e.message}")

@router.get("/vapid-public-key")
async def get_vapid_public_key():
    print(f"[VAPID Key Request] Public Key: {VAPID_PUBLIC_KEY[:15]}...")
    return {"public_key": VAPID_PUBLIC_KEY}

@router.post("/push-subscribe")
async def subscribe_push(sub: PushSubscription, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    
    print(f"[Push Subscribe Request] User ID: {user.id}")
    print(f"  Endpoint: {sub.endpoint[:40]}...")

    try:
        exist = await client.table("push_subscriptions").select("id").eq("endpoint", sub.endpoint).execute()
        if exist.data:
            print(f"  既存のEndpointが見つかりました(ID: {exist.data[0]['id']})。情報を更新します。")
            await client.table("push_subscriptions").update({
                "user_id": user.id, "p256dh": sub.p256dh, "auth": sub.auth
            }).eq("id", exist.data[0]["id"]).execute()
        else:
            print("  新規のEndpointです。新規保存します。")
            await client.table("push_subscriptions").insert({
                "user_id": user.id, "endpoint": sub.endpoint, "p256dh": sub.p256dh, "auth": sub.auth
            }).execute()
            
        return {"message": "通知設定を有効化しました。"}
    except APIError as e:
        print(f"[API Error /push-subscribe]: {e.message}")
        raise HTTPException(status_code=400, detail=f"DBエラー: {e.message}")
    except Exception as e:
        print(f"[Unexpected Error /push-subscribe]: {e}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"サーバーエラー: {str(e)}")

# =====================================================================
# API: メッセージ操作（一般）
# =====================================================================
@router.post("/send")
async def send_dm(data: DMRequest, background_tasks: BackgroundTasks, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    
    print(f"[DM Send Request] Sender User ID: {user.id} -> Target DM ID: @{data.target_dm_id}")

    try:
        target_res = await client.table("profiles").select("id").eq("dm_id", data.target_dm_id).execute()
        if not target_res.data:
            print(f"  送信失敗: 対象の DM ID (@{data.target_dm_id}) が見つかりません。")
            raise HTTPException(status_code=404, detail="指定されたDM IDのユーザーは見つかりません。")
            
        target_id = target_res.data[0]["id"]
        if str(user.id) == str(target_id):
            print("  送信失敗: 自分自身への送信です。")
            raise HTTPException(status_code=400, detail="自分自身には送信できません。")

        # メッセージのDB保存
        await client.table("direct_messages").insert({
            "sender_id": user.id,
            "receiver_id": target_id,
            "content": data.content.strip()
        }).execute()
        print("  Direct Message DB保存成功！")

        prof = await client.table("profiles").select("nickname").eq("id", user.id).execute()
        sender_name = prof.data[0]["nickname"] if (prof.data and prof.data[0].get("nickname")) else "不明な国民"
        
        # バックグラウンドタスク追加
        print("  WebPush 送信タスクを BackgroundTasks に追加します...")
        background_tasks.add_task(send_web_push, target_id, sender_name, data.content.strip())
        
        return {"message": "送信完了しました。"}
    except APIError as e:
        print(f"PostgREST Error in /send: {e.message}")
        raise HTTPException(status_code=400, detail=f"DBエラー: {e.message}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"Unexpected Error in /send: {e}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"サーバー内部エラー: {str(e)}")

@router.get("/conversations")
async def get_conversations(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    
    try:
        filter_str = f"sender_id.eq.{user.id},receiver_id.eq.{user.id}"
        msgs = await client.table("direct_messages").select(
            "id, sender_id, receiver_id, content, is_read, created_at"
        ).or_(filter_str).order("created_at", desc=True).execute()

        if not msgs.data:
            return {"conversations": []}

        partner_ids = set()
        for m in msgs.data:
            p_id = m["receiver_id"] if m["sender_id"] == user.id else m["sender_id"]
            partner_ids.add(p_id)

        if not partner_ids:
            return {"conversations": []}

        profiles_res = await client.table("profiles").select("id, nickname, dm_id").in_("id", list(partner_ids)).execute()
        prof_map = {p["id"]: p for p in (profiles_res.data or [])}

        convs = {}
        for msg in msgs.data:
            is_sender = (msg["sender_id"] == user.id)
            partner_id = msg["receiver_id"] if is_sender else msg["sender_id"]
            
            p_info = prof_map.get(partner_id)
            if not p_info or not p_info.get("dm_id"):
                continue

            if partner_id not in convs:
                convs[partner_id] = {
                    "partner_dm_id": p_info.get("dm_id"),
                    "partner_name": p_info.get("nickname") or "名無し国民",
                    "latest_message": msg["content"],
                    "latest_time": msg["created_at"],
                    "unread_count": 0
                }
            
            if not is_sender and not msg.get("is_read", False):
                convs[partner_id]["unread_count"] += 1

        return {"conversations": list(convs.values())}
    except APIError as e:
        print(f"[API Error /conversations]: {e.message}")
        raise HTTPException(status_code=400, detail=f"DBエラー: {e.message}")

@router.get("/messages/{partner_dm_id}")
async def get_messages(partner_dm_id: str, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    client = await get_supabase()
    
    try:
        target_res = await client.table("profiles").select("id").eq("dm_id", partner_dm_id).execute()
        if not target_res.data:
            return {"messages": [], "partner_user_id": None}
        partner_id = target_res.data[0]["id"]
        
        # 未読メッセージを既読に更新
        await client.table("direct_messages").update({"is_read": True}).eq("sender_id", partner_id).eq("receiver_id", user.id).eq("is_read", False).execute()

        cond = f"and(sender_id.eq.{user.id},receiver_id.eq.{partner_id}),and(sender_id.eq.{partner_id},receiver_id.eq.{user.id})"
        
        res = await client.table("direct_messages").select(
            "id, sender_id, receiver_id, content, is_read, created_at"
        ).or_(cond).order("created_at", desc=False).limit(200).execute()
        
        return {"messages": res.data or [], "partner_user_id": partner_id}
    except APIError as e:
        print(f"[API Error /messages/{partner_dm_id}]: {e.message}")
        raise HTTPException(status_code=400, detail=f"DBエラー: {e.message}")

# =====================================================================
# API: 国王専用 監視ツール
# =====================================================================
@router.get("/admin/threads")
async def admin_get_all_threads(authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    if not await is_king_user(user.id):
        raise HTTPException(status_code=403, detail="権限がありません。")
        
    client = await get_supabase()
    try:
        msgs = await client.table("direct_messages").select(
            "id, sender_id, receiver_id, content, created_at"
        ).order("created_at", desc=True).limit(500).execute()

        if not msgs.data:
            return {"threads": []}

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
                    "user_a_id": msg["sender_id"],
                    "user_a_name": p_a.get("nickname", "不明"),
                    "user_a_dm_id": p_a.get("dm_id", ""),
                    "user_b_id": msg["receiver_id"],
                    "user_b_name": p_b.get("nickname", "不明"),
                    "user_b_dm_id": p_b.get("dm_id", ""),
                    "latest_message": msg["content"],
                    "latest_time": msg["created_at"],
                    "msg_count": 1
                }
            else:
                threads[thread_key]["msg_count"] += 1

        return {"threads": list(threads.values())}
    except APIError as e:
        print(f"[API Error /admin/threads]: {e.message}")
        raise HTTPException(status_code=400, detail=f"DBエラー: {e.message}")

@router.get("/admin/messages/{user_a_id}/{user_b_id}")
async def admin_get_thread_messages(user_a_id: str, user_b_id: str, authorization: str = Header(None)):
    user = await get_user_auth(authorization)
    if not await is_king_user(user.id):
        raise HTTPException(status_code=403, detail="権限がありません。")
        
    client = await get_supabase()
    try:
        cond = f"and(sender_id.eq.{user_a_id},receiver_id.eq.{user_b_id}),and(sender_id.eq.{user_b_id},receiver_id.eq.{user_a_id})"
        
        res = await client.table("direct_messages").select(
            "id, sender_id, receiver_id, content, created_at"
        ).or_(cond).order("created_at", desc=False).limit(200).execute()
        
        msgs = res.data or []
        
        profs = await client.table("profiles").select("id, nickname").in_("id", [user_a_id, user_b_id]).execute()
        p_map = {p["id"]: p["nickname"] for p in (profs.data or [])}
        
        for m in msgs:
            m["sender"] = {"nickname": p_map.get(m["sender_id"], "不明")}

        return {"messages": msgs}
    except APIError as e:
        print(f"[API Error /admin/messages]: {e.message}")
        raise HTTPException(status_code=400, detail=f"DBエラー: {e.message}")
