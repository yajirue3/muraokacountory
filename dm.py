import os
import json
from typing import Optional, List, Dict, Any
from datetime import datetime
from fastapi import APIRouter, Header, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from pywebpush import webpush, WebPushException
from db import get_supabase

router = APIRouter()

# --- VAPIDキー設定 ---
# 本番環境では環境変数に固定のキーを設定してください。
# キーペア生成はPython環境で `vapid --gen` を実行するか、オンラインジェネレータを使用します。
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "YOUR_PUBLIC_KEY_HERE")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "YOUR_PRIVATE_KEY_HERE")
VAPID_CLAIMS = {"sub": "mailto:admin@example.com"}

# --- Models ---
class DMRequest(BaseModel):
    receiver_id: str
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
    """Web Push通知を送信（ブラウザを閉じていても届く）"""
    if VAPID_PRIVATE_KEY == "YOUR_PRIVATE_KEY_HERE":
        print(f"[{datetime.now()}] 警告: VAPID_PRIVATE_KEY が初期値のため、プッシュ通知をスキップします。")
        return

    client = await get_supabase()
    subs_res = await client.table("push_subscriptions").select("*").eq("user_id", receiver_id).execute()
    
    if not subs_res.data:
        return # 相手が通知を許可していない
        
    payload = {
        "title": f"村岡王国: {sender_name} からの密書",
        "body": message_text[:50] + ("..." if len(message_text) > 50 else ""),
        "icon": "/templates/icon.png", # 任意のアイコンURL
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
            # 410 Gone: ユーザーが通知の許可を取り消したなどの場合はDBから削除してクリーンアップ
            if e.response is not None and e.response.status_code == 410:
                await client.table("push_subscriptions").delete().eq("id", sub["id"]).execute()
            print(f"[{datetime.now()}] WebPush送信エラー: {e}")
        except Exception as e:
            print(f"[{datetime.now()}] 予期せぬPushエラー: {e}")

# =====================================================================
# API: 端末登録・通知設定
# =====================================================================
@router.get("/vapid-public-key")
async def get_vapid_public_key():
    """フロントエンドに公開鍵を渡す"""
    return {"public_key": VAPID_PUBLIC_KEY}

@router.post("/subscribe")
async def subscribe_push(sub: PushSubscription, authorization: str = Header(None)):
    """ブラウザからのプッシュ購読情報（トークン）を保存"""
    user = await get_user_auth(authorization)
    client = await get_supabase()
    
    exist = await client.table("push_subscriptions").select("id").eq("endpoint", sub.endpoint).execute()
    if exist.data:
        await client.table("push_subscriptions").update({
            "user_id": user.id,
            "p256dh": sub.p256dh,
            "auth": sub.auth
        }).eq("id", exist.data[0]["id"]).execute()
    else:
        await client.table("push_subscriptions").insert({
            "user_id": user.id,
            "endpoint": sub.endpoint,
            "p256dh": sub.p256dh,
            "auth": sub.auth
        }).execute()
        
    return {"message": "通知を受信する設定が完了しました。"}

# =====================================================================
# API: メッセージ操作（一般国民 兼 国王）
# =====================================================================
@router.post("/send")
async def send_dm(data: DMRequest, background_tasks: BackgroundTasks, authorization: str = Header(None)):
    """ダイレクトメッセージを送信"""
    user = await get_user_auth(authorization)
    client = await get_supabase()
    
    if user.id == data.receiver_id:
        raise HTTPException(status_code=400, detail="自分自身にメッセージは送れません。")

    res = await client.table("direct_messages").insert({
        "sender_id": user.id,
        "receiver_id": data.receiver_id,
        "content": data.content.strip()
    }).execute()
    
    prof = await client.table("profiles").select("nickname").eq("id", user.id)
