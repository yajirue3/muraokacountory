import os
import traceback
from typing import List
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

from db import get_supabase

router = APIRouter(prefix="/api/ai", tags=["ai"])

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL_NAME = "gemini-3.5-flash"

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
# チャットAPIエンドポイント
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

    # DBから直近10件の履歴を取得
    history_res = await (
        client.table("ai_chat_messages")
        .select("role, content")
        .eq("user_id", user.id)
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    )
    raw_history = history_res.data or []
    raw_history.reverse()

    # ターミナルでコンテキスト取得状況を確認
    print(f"[AI Chat Debug] 読み込んだ履歴件数: {len(raw_history)}件")

    # Geminiのターン制約対策: 先頭が 'model' の場合は取り除き、必ず 'user' から開始
    while raw_history and raw_history[0].get("role") != "user":
        raw_history.pop(0)

    contents = []
    last_role = None

    # ロールが重複して連続しないよう整えながら contents を構築
    for item in raw_history:
        current_role = item.get("role")
        text_content = item.get("content", "")
        if current_role and text_content and current_role != last_role:
            contents.append(
                types.Content(
                    role=current_role,
                    parts=[types.Part.from_text(text=text_content)]
                )
            )
            last_role = current_role

    # 最後に今回のユーザー入力を追加
    contents.append(
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=clean_message)]
        )
    )

    # 思考オフ（即時レスポンス）と適正トークン数設定
    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(
            thinking_budget=0
        ),
        max_output_tokens=512,
        system_instruction="これまでの会話履歴を踏まえ、親切かつ簡潔な日本語で短く回答してください。"
    )

    try:
        response = await ai_client.aio.models.generate_content(
            model=MODEL_NAME,
            contents=contents,
            config=config
        )
        reply_text = getattr(response, "text", None)
        if not reply_text:
            reply_text = "（応答を取得できませんでした）"

        db_save_reply = reply_text

    except Exception as e:
        print(f"[AI Generate Error]: {e}")
        raise HTTPException(status_code=500, detail=f"AI応答エラー: {str(e)}")

    # 発言と応答を保存（エラー発生時はトレースバックを出力）
    try:
        await client.table("ai_chat_messages").insert([
            {"user_id": user.id, "role": "user", "content": clean_message},
            {"user_id": user.id, "role": "model", "content": db_save_reply}
        ]).execute()
        print("[AI Chat Debug] メッセージをDBへ正常に保存しました")
    except Exception as e:
        print(f"[AI Chat DB Save Error]: {e}")
        traceback.print_exc()

    return ChatResponse(reply=reply_text)


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


@router.delete("/history")
async def clear_chat_history(authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    await client.table("ai_chat_messages").delete().eq("user_id", user.id).execute()
    return {"message": "AIチャットの履歴を削除しました。"}
