import os
from typing import List, Literal
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

from db import get_supabase

router = APIRouter(prefix="/api/ai", tags=["ai"])

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL_NAME = "gemini-2.5-flash-lite"

# クライアント初期化
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
class ChatMessage(BaseModel):
    role: Literal["user", "model"]
    content: str = Field(..., min_length=1, max_length=255)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=255)
    history: List[ChatMessage] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str


# --------------------------------------------------
# チャットAPIエンドポイント
# --------------------------------------------------
@router.post("/chat", response_model=ChatResponse)
async def chat_with_gemini(data: ChatRequest, authorization: str = Header(None)):
    await get_user_from_token(authorization)

    if not ai_client:
        raise HTTPException(
            status_code=500,
            detail="GEMINI_API_KEY が設定されていません。"
        )

    clean_message = data.message.strip()
    if not clean_message:
        raise HTTPException(status_code=400, detail="メッセージを入力してください。")

    # 直近最大10件の履歴を抽出
    recent_history = data.history[-10:] if data.history else []

    # Gemini 向けコンテンツ構築
    contents = []
    for item in recent_history:
        text = item.content.strip()
        if text:
            contents.append(
                types.Content(
                    role=item.role,
                    parts=[types.Part.from_text(text=text)]
                )
            )

    # 現在の入力を追加
    contents.append(
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=clean_message)]
        )
    )

    try:
        # SDK標準の非同期クライアント (aio) を使用
        response = await ai_client.aio.models.generate_content(
            model=MODEL_NAME,
            contents=contents
        )

        reply_text = getattr(response, "text", None)
        if not reply_text:
            reply_text = "（応答が空または安全フィルターによりブロックされました）"

        return ChatResponse(reply=reply_text)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI応答エラー: {str(e)}")
