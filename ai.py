import os
import json
import asyncio
import traceback
from typing import List, AsyncGenerator
from fastapi import APIRouter, HTTPException, Header, BackgroundTasks
from fastapi.responses import StreamingResponse
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


async def get_user_from_token(authorization: str):
    """ヘッダーのBearerトークンを検証し、ユーザー情報を返却"""
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


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000, description="ユーザー入力メッセージ")


class ChatResponse(BaseModel):
    reply: str


async def save_messages_to_db(user_id: str, user_text: str, reply_text: str):
    """
    メッセージ履歴をDBへ保存
    """
    try:
        client = await get_supabase()
        await client.table("ai_chat_messages").insert([
            {"user_id": user_id, "role": "user", "content": user_text},
            {"user_id": user_id, "role": "model", "content": reply_text}
        ]).execute()
    except Exception as e:
        print(f"[AI Chat DB Save Error]: {e}")
        traceback.print_exc()


async def build_conversation_contents(client, user_id: str, new_message: str) -> List[types.Content]:
    """
    Supabaseから履歴を取得し、Geminiの会話ターン要件（user ↔ model の交互構造）に完全に適合したContentsリストを生成
    """
    # 履歴を取得（直近10件）
    history_res = await (
        client.table("ai_chat_messages")
        .select("role, content")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    )
    raw_history = history_res.data or []
    raw_history.reverse()

    # 1. 有効なデータのみ抽出
    cleaned_history = []
    for item in raw_history:
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if role in ["user", "model"] and content:
            cleaned_history.append({"role": role, "content": content})

    # 2. Geminiの必須制約: 会話は必ず user から開始する
    while cleaned_history and cleaned_history[0]["role"] != "user":
        cleaned_history.pop(0)

    # 3. 隣り合う同一ロールの結合処理（user -> user や model -> model の連続を防止）
    normalized_history = []
    for item in cleaned_history:
        if normalized_history and normalized_history[-1]["role"] == item["role"]:
            normalized_history[-1]["content"] += f"\n{item['content']}"
        else:
            normalized_history.append(item)

    # 4. 今回の新しいメッセージの統合処理
    if normalized_history and normalized_history[-1]["role"] == "user":
        # 過去ログ最後が user で終わっていた場合（例外時）は改行結合
        normalized_history[-1]["content"] += f"\n{new_message}"
    else:
        # 通常時: 直前が model なので今回の user メッセージを新規追加
        normalized_history.append({"role": "user", "content": new_message})

    # 5. Gemini SDK の types.Content リストへ変換
    contents: List[types.Content] = []
    for item in normalized_history:
        contents.append(
            types.Content(
                role=item["role"],
                parts=[types.Part.from_text(text=item["content"])]
            )
        )

    return contents


@router.post("/chat", response_model=ChatResponse)
async def chat_with_gemini(
    data: ChatRequest,
    background_tasks: BackgroundTasks,
    authorization: str = Header(None)
):
    """
    一括返却用チャットエンドポイント
    """
    user = await get_user_from_token(authorization)

    if not ai_client:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY が設定されていません。")

    clean_message = data.message.strip()
    if not clean_message:
        raise HTTPException(status_code=400, detail="メッセージを入力してください。")

    client = await get_supabase()
    contents = await build_conversation_contents(client, user.id, clean_message)

    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(
            thinking_level=types.ThinkingLevel.MINIMAL
        ),
        max_output_tokens=600,
        system_instruction="あなたは親しみやすいAIアシスタントです。過去の会話履歴を踏まえて、自然で分かりやすい日本語で回答してください。"
    )

    try:
        response = await ai_client.aio.models.generate_content(
            model=MODEL_NAME,
            contents=contents,
            config=config
        )
        reply_text = getattr(response, "text", None) or "（応答を取得できませんでした）"
    except Exception as e:
        print(f"[AI Generate Error]: {e}")
        raise HTTPException(status_code=500, detail=f"AI応答エラー: {str(e)}")

    background_tasks.add_task(save_messages_to_db, user.id, clean_message, reply_text)

    return ChatResponse(reply=reply_text)


async def stream_generator(contents: List[types.Content], user_id: str, clean_message: str) -> AsyncGenerator[str, None]:
    """
    トークン生成の都度フロントエンドにSSE形式でストリーミング配信
    """
    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(
            thinking_level=types.ThinkingLevel.MINIMAL
        ),
        max_output_tokens=800,
        system_instruction="あなたは親しみやすいAIアシスタントです。過去の会話履歴を踏まえて、自然で分かりやすい日本語で回答してください。"
    )

    full_reply_chunks = []
    try:
        response_stream = await ai_client.aio.models.generate_content_stream(
            model=MODEL_NAME,
            contents=contents,
            config=config
        )
        async for chunk in response_stream:
            text = chunk.text
            if text:
                full_reply_chunks.append(text)
                payload = json.dumps({"text": text}, ensure_ascii=False)
                yield f"data: {payload}\n\n"

        yield "data: [DONE]\n\n"

        # 生成完了後に確実にSupabaseへ保存（awaitでGC破棄を回避）
        full_reply = "".join(full_reply_chunks)
        if full_reply:
            await save_messages_to_db(user_id, clean_message, full_reply)

    except Exception as e:
        print(f"[AI Stream Error]: {e}")
        err_payload = json.dumps({"error": str(e)}, ensure_ascii=False)
        yield f"data: {err_payload}\n\n"


@router.post("/chat/stream")
async def chat_with_gemini_stream(
    data: ChatRequest,
    authorization: str = Header(None)
):
    """
    SSEストリーミングエンドポイント
    """
    user = await get_user_from_token(authorization)

    if not ai_client:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY が設定されていません。")

    clean_message = data.message.strip()
    if not clean_message:
        raise HTTPException(status_code=400, detail="メッセージを入力してください。")

    client = await get_supabase()
    contents = await build_conversation_contents(client, user.id, clean_message)

    return StreamingResponse(
        stream_generator(contents, user.id, clean_message),
        media_type="text/event-stream"
    )


@router.get("/history")
async def get_chat_history(authorization: str = Header(None)):
    """保存された過去のチャット履歴を取得"""
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
    """チャット履歴の全削除"""
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    await client.table("ai_chat_messages").delete().eq("user_id", user.id).execute()
    return {"message": "AIチャットの履歴を削除しました。"}
