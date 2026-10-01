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
# 2026年最新の主力高速モデル
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
    レスポンス返却をブロックしないようバックグラウンドで履歴をDB保存
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
    Supabaseから履歴を取得し、Geminiの会話ターン要件に適合したContentsリストを生成
    """
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

    # Geminiの制約: 会話は必ず user から開始する
    while raw_history and raw_history[0].get("role") != "user":
        raw_history.pop(0)

    contents: List[types.Content] = []
    last_role = None

    for item in raw_history:
        current_role = item.get("role")
        text_content = item.get("content", "")
        # 同一ロールの連続を防止
        if current_role and text_content and current_role != last_role:
            contents.append(
                types.Content(
                    role=current_role,
                    parts=[types.Part.from_text(text=text_content)]
                )
            )
            last_role = current_role

    # 今回の入力を追加
    contents.append(
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=new_message)]
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
    DB保存をバックグラウンドに回すことで待機時間を短縮
    """
    user = await get_user_from_token(authorization)

    if not ai_client:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY が設定されていません。")

    clean_message = data.message.strip()
    if not clean_message:
        raise HTTPException(status_code=400, detail="メッセージを入力してください。")

    client = await get_supabase()
    contents = await build_conversation_contents(client, user.id, clean_message)

    # 思考レベルをMINIMALにして思考待機時間を抑えつつ上限トークンを適正化
    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(
            thinking_level=types.ThinkingLevel.MINIMAL
        ),
        max_output_tokens=600,
        system_instruction="前後の文脈を汲み取り、簡潔で分かりやすい日本語でテンポよく回答してください。"
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

    # DBへの書き込み完了を待たずに即時レスポンスを返し、保存はバックグラウンドで実行
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
        system_instruction="前後の文脈を汲み取り、自然で親切な日本語で回答してください。"
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

        # 生成完了後に履歴をSupabaseへ保存
        full_reply = "".join(full_reply_chunks)
        asyncio.create_task(save_messages_to_db(user_id, clean_message, full_reply))

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
    超低レイテンシで最初の1文字目から順次返答を返すSSEストリーミングエンドポイント
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
