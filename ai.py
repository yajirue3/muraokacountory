import os
from typing import List
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

from db import get_supabase

router = APIRouter(prefix="/api/ai", tags=["ai"])

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL_NAME = "gemini-2.5-flash-lite"

ai_client = None
if GEMINI_API_KEY:
    ai_client = genai.Client(api_key=GEMINI_API_KEY)


# --------------------------------------------------
# 認証ヘルパー
# --------------------------------------------------
async def get_user_from_token(authorization: str):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="認証トークンがありません")
    token = authorization.split(" ")[1]
    try:
        client = await get_supabase()
        user_res = await client.auth.get_user(token)
        if not user_res.user:
            raise HTTPException(status_code=401, detail="無効なトークンです")
        return user_res.user
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="トークンの検証に失敗しました")


# --------------------------------------------------
# リクエスト / レスポンス モデル
# --------------------------------------------------
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=255, description="1〜255文字")


class ChatResponse(BaseModel):
    reply: str


# --------------------------------------------------
# チャットAPIエンドポイント（DB連携版）
# --------------------------------------------------
@router.post("/chat", response_model=ChatResponse)
async def chat_with_gemini(data: ChatRequest, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)

    if not ai_client:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY が設定されていません。")

    clean_message = data.message.strip()
    if not clean_message:
        raise HTTPException(status_code=400, detail="メッセージを入力してください。")

    client = await get_supabase()

    # 1. DBから直近10件の会話履歴を取得（古い順にソートし直す）
    history_res = await (
        client.table("ai_chat_messages")
        .select("role, content")
        .eq("user_id", user.id)
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    )
    raw_history = history_res.data or []
    raw_history.reverse()  # Geminiに渡すため時系列順に戻す

    # 2. Gemini用のコンテンツリストを構築
    contents = []
    for item in raw_history:
        contents.append(
            types.Content(
                role=item["role"],
                parts=[types.Part.from_text(text=item["content"])]
            )
        )

    # 今回の入力を追加
    contents.append(
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=clean_message)]
        )
    )

    # 3. Gemini へリクエスト送信
    try:
        response = await ai_client.aio.models.generate_content(
            model=MODEL_NAME,
            contents=contents
        )
        reply_text = getattr(response, "text", None)
        if not reply_text:
            reply_text = "（応答が空または安全フィルターによりブロックされました）"

        # 255文字制限に合わせて安全にカット（テーブル制約 VARCHAR(255) 対策）
        db_save_reply = reply_text[:255]

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI応答エラー: {str(e)}")

    # 4. 発言と応答を DB に保存
    try:
        await client.table("ai_chat_messages").insert([
            {"user_id": user.id, "role": "user", "content": clean_message},
            {"user_id": user.id, "role": "model", "content": db_save_reply}
        ]).execute()
    except Exception as e:
        print(f"[AI Chat DB Save Error]: {e}")

    return ChatResponse(reply=reply_text)


# --------------------------------------------------
# 補助API: 会話履歴の取得（画面読み込み時用）
# --------------------------------------------------
@router.get("/history")
async def get_chat_history(authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    res = await (
        client.table("ai_chat_messages")
        .select("id, role, content, created_at")
        .eq("user_id", user.id)
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    )
    messages = res.data or []
    messages.reverse()
    return {"messages": messages}


# --------------------------------------------------
# 補助API: 会話履歴のクリア（リセット用）
# --------------------------------------------------
@router.delete("/history")
async def clear_chat_history(authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    await client.table("ai_chat_messages").delete().eq("user_id", user.id).execute()
    return {"message": "AIチャットの履歴を削除しました。"}
