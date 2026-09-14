import os
import random
import string
import asyncio
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from casino import router as casino_router
from inventory import router as inventory_router
from policy import router as policy_router
from fastapi.staticfiles import StaticFiles
from factory import router as factory_router
from card import router as card_router
from db import get_supabase

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

# 毎日0時(UTC) = 日本時間 9時に自動没収を実行するタスク
async def daily_confiscation_task():
    while True:
        now = datetime.now(timezone.utc)
        next_run = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run = next_run + timedelta(days=1)
        sleep_seconds = (next_run - now).total_seconds()
        
        await asyncio.sleep(sleep_seconds)
        
        try:
            client = await get_supabase()
            await client.rpc("execute_daily_confiscation").execute()
            print(f"[{datetime.now()}] 日次の借金強制没収バッチが完了しました。")
        except Exception as e:
            print(f"[{datetime.now()}] 日次バッチエラー: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    if SUPABASE_URL and SUPABASE_KEY:
        await get_supabase()
    # 自動没収タスクの起動
    task = asyncio.create_task(daily_confiscation_task())
    yield
    task.cancel()

app = FastAPI(title="村岡王国 ポータル", lifespan=lifespan)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

class UserAuth(BaseModel):
    email: str
    password: str

class ProfileUpdate(BaseModel):
    nickname: str
    real_name: str

class ReportCreate(BaseModel):
    content: str

class WalletCreate(BaseModel):
    wallet_name: str

class PayTaxRequest(BaseModel):
    wallet_id: str

class TransferRequest(BaseModel):
    sender_wallet_id: str
    receiver_wallet_id: str
    amount: int

class ContractCreate(BaseModel):
    title: str
    description: str
    amount: int
    creator_wallet_id: str

class ContractAccept(BaseModel):
    acceptor_wallet_id: str

class ReviewCreate(BaseModel):
    rating: int  # 1 〜 5
    comment: str = ""

# --- 追加: 融資・借金システム用モデル (Zero Trust 堅牢化) ---
class LoanOfferCreate(BaseModel):
    lender_wallet_id: str
    max_amount: int = Field(..., gt=0, description="出品額は1以上でなければなりません")
    interest_rate: int = Field(..., ge=0, description="金利はマイナスにできません")

class LoanBorrow(BaseModel):
    offer_id: int
    borrow_amount: int = Field(..., gt=0, description="借入額は1以上でなければなりません")
    borrower_wallet_id: str

class LoanRepay(BaseModel):
    loan_id: int
    repay_amount: int = Field(..., gt=0, description="返済額は1以上でなければなりません")
# -------------------------------------

def generate_wallet_id():
    return "MW-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

async def get_user_from_token(authorization: str):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="認証トークンがありません")
    token = authorization.split(" ")[1]
    try:
        client = await get_supabase()
        user_res = await client.auth.get_user(token)
        return user_res.user
    except Exception:
        raise HTTPException(status_code=401, detail="無効なトークンです")

async def is_king(user_id: str) -> bool:
    try:
        client = await get_supabase()
        res = await client.table("profiles").select("role").eq("id", user_id).execute()
        if res.data and res.data[0].get("role") == "king":
            return True
    except Exception:
        pass
    return False

async def ensure_default_wallet(user_id: str):
    try:
        client = await get_supabase()
        res = await client.table("wallets").select("id").eq("user_id", user_id).execute()
        if not res.data:
            w_id = generate_wallet_id()
            await client.table("wallets").insert({
                "wallet_id": w_id,
                "user_id": user_id,
                "wallet_name": "メイン口座",
                "balance": 0
            }).execute()
    except Exception as e:
        print(f"Default wallet creation error: {e}")

# --------------------------------------------------
# 画面配信
# --------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def get_index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/dashboard", response_class=HTMLResponse)
def get_dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="dashboard.html")

@app.get("/board", response_class=HTMLResponse)
def get_board(request: Request):
    return templates.TemplateResponse(request=request, name="board.html")

@app.get("/wallet", response_class=HTMLResponse)
def get_wallet(request: Request):
    return templates.TemplateResponse(request=request, name="wallet.html")

@app.get("/market", response_class=HTMLResponse)
def get_market(request: Request):
    return templates.TemplateResponse(request=request, name="market.html")

@app.get("/casino", response_class=HTMLResponse)
def get_casino(request: Request):
    return templates.TemplateResponse(request=request, name="casino.html")

@app.get("/dice", response_class=HTMLResponse)
def get_dice(request: Request):
    return templates.TemplateResponse(request=request, name="dice.html")

@app.get("/inventory", response_class=HTMLResponse)
def get_inventory(request: Request):
    return templates.TemplateResponse(request=request, name="inventory.html")

@app.get("/admin/items", response_class=HTMLResponse)
def admin_items_page(request: Request):
    return templates.TemplateResponse(request=request, name="admin_items.html")

@app.get("/tower", response_class=HTMLResponse)
def get_tower(request: Request):
    return templates.TemplateResponse(request=request, name="tower.html")

@app.get("/factory", response_class=HTMLResponse)
def get_factory(request: Request):
    return templates.TemplateResponse(request=request, name="factory.html")

@app.get("/slot", response_class=HTMLResponse)
def get_slot(request: Request):
    return templates.TemplateResponse(request=request, name="slot.html")

@app.get("/loan", response_class=HTMLResponse)
def get_loan(request: Request):
    return templates.TemplateResponse(request=request, name="loan.html")

@app.get("/mines", response_class=HTMLResponse)
def get_mines(request: Request):
    return templates.TemplateResponse(request=request, name="mines.html")


app.mount("/templates", StaticFiles(directory="templates"), name="templates")

# --------------------------------------------------
# 認証API
# --------------------------------------------------
@app.post("/signup")
async def signup(user: UserAuth):
    client = await get_supabase()
    try:
        res = await client.auth.sign_up({"email": user.email, "password": user.password})
        if res.user:
            await ensure_default_wallet(res.user.id)
        return {"message": "国民登録が完了しました！"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/login")
async def login(user: UserAuth):
    client = await get_supabase()
    try:
        res = await client.auth.sign_in_with_password({"email": user.email, "password": user.password})
        return {
            "message": "入国が許可されました！",
            "access_token": res.session.access_token,
            "email": user.email
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"ログイン失敗: {str(e)}")

# --------------------------------------------------
# 王国API
# --------------------------------------------------
@app.get("/api/profile")
async def get_profile(authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    await ensure_default_wallet(user.id)
    client = await get_supabase()
    res = await client.table("profiles").select("*").eq("id", user.id).execute()
    
    if res.data and len(res.data) > 0:
        return res.data[0]
        
    return {
        "id": user.id, 
        "nickname": "名無しの労働奴隷", 
        "role": "slave",
        "agreed_terms_version": 0
    }

@app.post("/api/profile")
async def update_profile(data: ProfileUpdate, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    try:
        await client.table("profiles").update({
            "nickname": data.nickname,
            "real_name": data.real_name
        }).eq("id", user.id).execute()
        return {"message": "国民情報を更新しました"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"更新失敗: {str(e)}")

@app.post("/api/pay-tax")
async def pay_tax(data: PayTaxRequest, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    today_str = str(date.today())

    client = await get_supabase()

    try:
        await client.rpc("claim_daily_tax", {
            "p_user_id": str(user.id),
            "p_wallet_id": str(data.wallet_id),
            "p_today": today_str
        }).execute()
        return {"message": "納税完了！100Gold獲得しました！"}
    except Exception as e:
        err_msg = getattr(e, "message", str(e))
        if "本日の受け取りは完了しています" in err_msg:
            raise HTTPException(status_code=400, detail="本日の納税は完了しています！")
        if "指定された口座が存在しません" in err_msg:
            raise HTTPException(status_code=400, detail="指定された受取口座が存在しないか、所有権がありません。")
        raise HTTPException(status_code=400, detail=f"納税処理エラー: {err_msg}")

@app.get("/api/wallets")
async def get_wallets(authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    await ensure_default_wallet(user.id)
    client = await get_supabase()
    res = await client.table("wallets").select("*").eq("user_id", user.id).order("created_at").execute()
    return res.data

@app.post("/api/wallets")
async def create_wallet(data: WalletCreate, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    w_id = generate_wallet_id()
    try:
        client = await get_supabase()
        await client.table("wallets").insert({
            "wallet_id": w_id,
            "user_id": user.id,
            "wallet_name": data.wallet_name or "サブ口座",
            "balance": 0
        }).execute()
        return {"message": f"新規口座「{data.wallet_name}」を開設しました（ID: {w_id}）"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"口座開設エラー: {str(e)}")

@app.post("/api/transfer")
async def transfer_gold(data: TransferRequest, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    try:
        client = await get_supabase()
        await client.rpc("transfer_gold_by_wallet", {
            "sender_wallet_id": data.sender_wallet_id,
            "receiver_wallet_id": data.receiver_wallet_id,
            "amount": data.amount,
            "auth_user_id": user.id
        }).execute()

        await client.table("transfer_logs").insert({
            "sender_wallet_id": data.sender_wallet_id,
            "receiver_wallet_id": data.receiver_wallet_id,
            "amount": data.amount
        }).execute()

        return {"message": f"口座 {data.receiver_wallet_id} へ {data.amount} Gold 送金しました！"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/transfer-logs")
async def get_transfer_logs(authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    my_wallets_res = await client.table("wallets").select("wallet_id").eq("user_id", user.id).execute()
    my_wallet_ids = [w["wallet_id"] for w in my_wallets_res.data] if my_wallets_res.data else []

    if not my_wallet_ids:
        return {"logs": [], "my_wallets": []}

    filter_str = f"sender_wallet_id.in.({','.join(my_wallet_ids)}),receiver_wallet_id.in.({','.join(my_wallet_ids)})"
    res = await client.table("transfer_logs").select("*").or_(filter_str).order("created_at", desc=True).limit(20).execute()
    
    return {"logs": res.data, "my_wallets": my_wallet_ids}

# ==========================================
# 8. 市場・オークション関連 API (Market APIs)
# ==========================================

@app.get("/api/market/listings", response_model=List[dict])
def get_market_listings(db: Session = Depends(get_db)):
    listings = db.query(MarketListing).filter(MarketListing.is_active == True).all()
    results = []
    for l in listings:
        seller = db.query(User).filter(User.id == l.seller_id).first()
        results.append({
            "id": l.id,
            "seller_id": l.seller_id,
            "seller_username": seller.username if seller else "不明",
            "item_name": l.item_name,
            "description": l.description,
            "price": l.price,
            "listing_type": l.listing_type,
            "end_time": l.end_time.isoformat() if l.end_time else None,
            "created_at": l.created_at.isoformat() if l.created_at else None
        })
    return results

@app.post("/api/market/sell")
def create_market_listing(
    request: MarketSellRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    seller_wallet = db.query(Wallet).filter(Wallet.wallet_id == request.seller_wallet_id, Wallet.user_id == current_user.id).first()
    if not seller_wallet:
        raise HTTPException(status_code=400, detail="無効な出品用口座です。")

    if request.listing_type == "AUCTION":
        if not request.duration_hours or request.duration_hours <= 0:
            raise HTTPException(status_code=400, detail="オークション期間を正しく設定してください。")
        end_time = datetime.datetime.utcnow() + datetime.timedelta(hours=request.duration_hours)
    else:
        end_time = None

    listing = MarketListing(
        seller_id=current_user.id,
        seller_wallet_id=seller_wallet.wallet_id,
        item_name=request.item_name,
        description=request.description,
        price=request.price,
        listing_type=request.listing_type,
        end_time=end_time,
        is_active=True
    )
    db.add(listing)
    db.commit()
    db.refresh(listing)

    return {"message": "出品が完了しました。", "listing_id": listing.id}

@app.post("/api/market/buy")
def buy_market_item(
    request: MarketBuyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    listing = db.query(MarketListing).filter(MarketListing.id == request.listing_id, MarketListing.is_active == True).first()
    if not listing:
        raise HTTPException(status_code=404, detail="指定された出品商品は存在しないか、既に終了しています。")

    if listing.seller_id == current_user.id:
        raise HTTPException(status_code=400, detail="自分の出品した商品は購入できません。")

    buyer_wallet = db.query(Wallet).filter(Wallet.wallet_id == request.buyer_wallet_id, Wallet.user_id == current_user.id).first()
    if not buyer_wallet:
        raise HTTPException(status_code=400, detail="無効な支払用口座です。")

    seller_wallet = db.query(Wallet).filter(Wallet.wallet_id == listing.seller_wallet_id).first()
    if not seller_wallet:
        raise HTTPException(status_code=400, detail="出品者の受取口座が存在しません。")

    if listing.listing_type == "FIXED":
        if buyer_wallet.balance < listing.price:
            raise HTTPException(status_code=400, detail="残高が不足しています。")

        buyer_wallet.balance -= listing.price
        seller_wallet.balance += listing.price
        listing.is_active = False

        tx_buyer = Transaction(wallet_id=buyer_wallet.wallet_id, amount=-listing.price, tx_type="PURCHASE", note=f"商品購入: {listing.item_name}")
        tx_seller = Transaction(wallet_id=seller_wallet.wallet_id, amount=listing.price, tx_type="SALE", note=f"商品売却: {listing.item_name}")
        db.add_all([tx_buyer, tx_seller])
        db.commit()

        return {"message": "購入が完了しました。"}

    elif listing.listing_type == "AUCTION":
        if datetime.datetime.utcnow() > listing.end_time:
            raise HTTPException(status_code=400, detail="このオークションは既に終了しています。")

        min_bid = listing.current_bid if listing.current_bid > 0 else listing.price
        if request.bid_amount <= min_bid:
            raise HTTPException(status_code=400, detail=f"入札額は現在の価格 ({min_bid} G) より高く設定してください。")

        if buyer_wallet.balance < request.bid_amount:
            raise HTTPException(status_code=400, detail="入札用の残高が不足しています。")

        # 前回の最高入札者に返金
        if listing.highest_bidder_id and listing.highest_bidder_wallet_id:
            prev_wallet = db.query(Wallet).filter(Wallet.wallet_id == listing.highest_bidder_wallet_id).first()
            if prev_wallet:
                prev_wallet.balance += listing.current_bid
                tx_refund = Transaction(wallet_id=prev_wallet.wallet_id, amount=listing.current_bid, tx_type="AUCTION_REFUND", note=f"オークション上書き返金: {listing.item_name}")
                db.add(tx_refund)

        # 新しい入札者の資金をロック（引き落とし）
        buyer_wallet.balance -= request.bid_amount
        listing.current_bid = request.bid_amount
        listing.highest_bidder_id = current_user.id
        listing.highest_bidder_wallet_id = buyer_wallet.wallet_id

        tx_bid = Transaction(wallet_id=buyer_wallet.wallet_id, amount=-request.bid_amount, tx_type="AUCTION_BID", note=f"オークション入札: {listing.item_name}")
        db.add(tx_bid)
        db.commit()

        return {"message": "入札が完了しました。"}

    raise HTTPException(status_code=400, detail="不正な出品タイプです。")

# --------------------------------------------------
# 掲示板API（高機能版・ゼロトラスト対応）
# --------------------------------------------------
import time
from collections import defaultdict
from pydantic import BaseModel, Field

# --- メモリベースのスパム連打防止機構 ---
_post_history = defaultdict(list)
_banned_until = {}

def enforce_rate_limit(user_id: str):
    now = time.time()
    if user_id in _banned_until:
        if now < _banned_until[user_id]:
            remain = int(_banned_until[user_id] - now)
            raise HTTPException(status_code=429, detail=f"連投制限中です。残り {remain} 秒お待ちください。")
        else:
            del _banned_until[user_id]

    history = _post_history[user_id]
    history = [t for t in history if now - t <= 10]
    
    if len(history) >= 8:
        _banned_until[user_id] = now + 60
        raise HTTPException(status_code=429, detail="スパム行為を検知したため、1分間投稿を禁止します。")
    
    if history and now - history[-1] < 1.0:
        raise HTTPException(status_code=429, detail="送信が早すぎます。1秒お待ちください。")
    
    history.append(now)
    _post_history[user_id] = history

# --- 厳格なバリデーションモデル ---
class BoardPostCreate(BaseModel):
    content: str
    user_title: str = "奴隷"
    wallet_id: Optional[str] = None
    # マイナス値による不正引き出しやバグをAPI層で完全ブロック
    airdrop_amount: int = Field(0, ge=0, description="配布額は0以上")
    airdrop_total: int = Field(0, ge=0, description="配布枠は0以上")

class AirdropClaim(BaseModel):
    wallet_id: str

class TipRequest(BaseModel):
    wallet_id: str
    # チップは必ず1G以上（マイナス値による残高泥棒を防止）
    amount: int = Field(..., gt=0, description="チップは1G以上必要です")

@app.get("/api/board/posts")
async def get_board_posts(page: int = 1, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    # マイナス・ゼロページへのアクセスを防止
    if page < 1:
        page = 1
        
    client = await get_supabase()
    limit = 20
    offset = (page - 1) * limit

    pinned_res = await client.table("board_posts").select("*").eq("is_pinned", True).order("created_at", desc=True).execute()
    normal_res = await client.table("board_posts").select("*").eq("is_pinned", False).order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    
    posts = (pinned_res.data or []) + (normal_res.data or [])
    post_ids = [p["id"] for p in posts]

    if not post_ids:
        return {"posts": [], "is_king": await is_king(user.id)}

    react_res = await client.table("board_reactions").select("*").in_("post_id", post_ids).execute()
    claims_res = await client.table("board_airdrop_claims").select("*").in_("post_id", post_ids).eq("user_id", user.id).execute()

    reacts = react_res.data or []
    claimed_set = {c["post_id"] for c in (claims_res.data or [])}

    for p in posts:
        p_reacts = [r for r in reacts if r["post_id"] == p["id"]]
        p["reactions"] = {
            "👍": len([r for r in p_reacts if r["reaction_type"] == "👍"]),
            "⛏️": len([r for r in p_reacts if r["reaction_type"] == "⛏️"]),
            "👑": len([r for r in p_reacts if r["reaction_type"] == "👑"]),
            "my_reacts": [r["reaction_type"] for r in p_reacts if r["user_id"] == user.id]
        }
        p["is_mine"] = (p["user_id"] == user.id)
        p["my_claim"] = (p["id"] in claimed_set)

    return {"posts": posts, "is_king": await is_king(user.id)}

@app.post("/api/board/posts")
async def create_board_post(data: BoardPostCreate, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    enforce_rate_limit(user.id)
    
    # 空白のみの投稿と、文字数超過をブロック
    clean_content = data.content.strip()
    if not clean_content:
        raise HTTPException(status_code=400, detail="本文が入力されていません。")
    if len(clean_content) > 400:
        raise HTTPException(status_code=400, detail="本文は400文字以内で入力してください。")

    client = await get_supabase()
    prof_res = await client.table("profiles").select("nickname").eq("id", user.id).execute()
    nickname = prof_res.data[0]["nickname"] if prof_res.data else "不明"

    # ばらまき設定の整合性チェック
    if data.airdrop_amount > 0 and data.airdrop_total > 0:
        if data.airdrop_amount < 100:
            raise HTTPException(status_code=400, detail="1人あたりのばらまき額は最低100G必要です。")
        if not data.wallet_id:
            raise HTTPException(status_code=400, detail="ばらまきを行う口座を指定してください。")
            
        total_cost = data.airdrop_amount * data.airdrop_total
        w_res = await client.table("wallets").select("*").eq("wallet_id", data.wallet_id).eq("user_id", user.id).execute()
        if not w_res.data or w_res.data[0]["balance"] < total_cost:
            raise HTTPException(status_code=400, detail="口座の残高が不足しています。")
            
        await client.table("wallets").update({"balance": w_res.data[0]["balance"] - total_cost}).eq("id", w_res.data[0]["id"]).execute()

    await client.table("board_posts").insert({
        "user_id": user.id,
        "nickname": nickname,
        "user_title": data.user_title,
        "content": clean_content,
        "airdrop_amount": data.airdrop_amount,
        "airdrop_total": data.airdrop_total
    }).execute()
    return {"message": "回覧板に布告しました。"}

@app.delete("/api/board/posts/{post_id}")
async def delete_board_post(post_id: int, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    
    post_res = await client.table("board_posts").select("user_id").eq("id", post_id).execute()
    if not post_res.data:
        raise HTTPException(status_code=404, detail="投稿が見つかりません。")
        
    if post_res.data[0]["user_id"] != user.id and not await is_king(user.id):
        raise HTTPException(status_code=403, detail="削除権限がありません。")

    await client.table("board_posts").delete().eq("id", post_id).execute()
    return {"message": "投稿を消し去りました。"}

@app.post("/api/board/posts/{post_id}/react")
async def toggle_reaction(post_id: int, type_data: dict, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    rtype = type_data.get("type")
    if rtype not in ["👍", "⛏️", "👑"]:
        raise HTTPException(status_code=400, detail="無効なスタンプです")

    exist = await client.table("board_reactions").select("*").eq("post_id", post_id).eq("user_id", user.id).eq("reaction_type", rtype).execute()
    if exist.data:
        await client.table("board_reactions").delete().eq("post_id", post_id).eq("user_id", user.id).eq("reaction_type", rtype).execute()
    else:
        await client.table("board_reactions").insert({"post_id": post_id, "user_id": user.id, "reaction_type": rtype}).execute()
    return {"message": "反応を示しました。"}

@app.post("/api/board/posts/{post_id}/claim")
async def claim_airdrop(post_id: int, data: AirdropClaim, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    try:
        res = await client.rpc("claim_board_airdrop", {
            "p_post_id": post_id, "p_user_id": str(user.id), "p_wallet_id": data.wallet_id
        }).execute()
        return {"message": f"{res.data['amount']}G の施しを受け取りました！"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

@app.post("/api/board/posts/{post_id}/tip")
async def tip_post(post_id: int, data: TipRequest, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    try:
        await client.rpc("tip_board_post", {
            "p_post_id": post_id, "p_sender_user_id": str(user.id), 
            "p_sender_wallet_id": data.wallet_id, "p_amount": data.amount
        }).execute()
        return {"message": f"投稿者に {data.amount}G を投げました！"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

@app.post("/api/board/posts/{post_id}/report")
async def report_post(post_id: int, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    
    # 複数回通報しても1レコードになるようON CONFLICTを想定（簡易的に存在チェック）
    exist = await client.table("board_reports").select("*").eq("post_id", post_id).eq("reporter_id", user.id).execute()
    if exist.data:
         raise HTTPException(status_code=400, detail="既に通報済みです。")
         
    await client.table("board_reports").insert({"post_id": post_id, "reporter_id": user.id}).execute()
    return {"message": "国王へ密告しました。対応をお待ちください。"}

@app.get("/api/board/admin/reports")
async def get_reports_admin(authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    if not await is_king(user.id):
        raise HTTPException(status_code=403, detail="権限がありません")
    client = await get_supabase()
    res = await client.table("board_reports").select("id, created_at, board_posts(id, nickname, content)").order("created_at", desc=True).execute()
    return res.data

@app.delete("/api/board/admin/reports/{report_id}")
async def dismiss_report(report_id: int, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    if not await is_king(user.id):
        raise HTTPException(status_code=403, detail="権限がありません")
    client = await get_supabase()
    await client.table("board_reports").delete().eq("id", report_id).execute()
    return {"message": "通報をリストから破棄しました。"}

@app.post("/api/board/posts/{post_id}/pin")
async def toggle_pin_post(post_id: int, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    if not await is_king(user.id):
        raise HTTPException(status_code=403, detail="権限がありません")
    client = await get_supabase()
    post = await client.table("board_posts").select("is_pinned").eq("id", post_id).execute()
    new_status = not post.data[0]["is_pinned"]
    await client.table("board_posts").update({"is_pinned": new_status}).eq("id", post_id).execute()
    return {"message": "布告(ピン)状態を切り替えました。"}

# --------------------------------------------------
# 融資・借金（P2Pレンディング）システム API
# --------------------------------------------------

# 1. 募集中の融資枠一覧を取得
@app.get("/api/loans/offers")
async def get_loan_offers(authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    res = await client.table("loan_offers").select("*").eq("status", "OPEN").gt("max_amount", 0).order("interest_rate").execute()
    return res.data

# 2. 新規の融資枠を出品（デポジット）
@app.post("/api/loans/offers")
async def create_loan_offer(data: LoanOfferCreate, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    
    w_res = await client.table("wallets").select("*").eq("wallet_id", data.lender_wallet_id).eq("user_id", user.id).execute()
    if not w_res.data:
        raise HTTPException(status_code=400, detail="指定された口座が存在しないか所有権がありません。")
    
    wallet = w_res.data[0]
    if wallet["balance"] < data.max_amount:
        raise HTTPException(status_code=400, detail="融資枠を作成するための口座残高が不足しています。")
        
    await client.table("wallets").update({"balance": wallet["balance"] - data.max_amount}).eq("id", wallet["id"]).execute()
    
    await client.table("loan_offers").insert({
        "lender_user_id": user.id,
        "lender_wallet_id": data.lender_wallet_id,
        "max_amount": data.max_amount,
        "interest_rate": data.interest_rate,
        "status": "OPEN"
    }).execute()
    
    return {"message": f"金利 {data.interest_rate}%、融資枠 {data.max_amount}G を市場に出品しました！"}

# 3. 融資枠からお金を借りる（DB行ロック RPC を使用）
@app.post("/api/loans/borrow")
async def borrow_loan(data: LoanBorrow, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    
    try:
        res = await client.rpc("execute_loan_borrow", {
            "p_offer_id": data.offer_id,
            "p_borrower_user_id": str(user.id),
            "p_borrower_wallet_id": str(data.borrower_wallet_id),
            "p_borrow_amount": data.borrow_amount
        }).execute()
        
        result = res.data
        return {"message": f"{result['principal']}Gの借入に成功しました。金利を含めた返済義務は {result['total_due']}G です。"}
    except Exception as e:
        err_msg = getattr(e, "message", str(e))
        raise HTTPException(status_code=400, detail=f"借入エラー: {err_msg}")

# 4. 自分の借入状況（負債）を取得
@app.get("/api/loans/my-debts")
async def get_my_debts(authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    res = await client.table("active_loans").select("*, loan_offers(interest_rate)").eq("borrower_user_id", user.id).in_("status", ["ACTIVE", "PAID"]).order("created_at", desc=True).execute()
    return res.data

# 5. 自分の貸付状況（債権）を取得
@app.get("/api/loans/my-receivables")
async def get_my_receivables(authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    # 自身が出品したオファーに紐づく全ての貸し出し（債権）を取得
    res = await client.table("active_loans").select("*, loan_offers!inner(*)").eq("loan_offers.lender_user_id", user.id).order("created_at", desc=True).execute()
    return res.data

# 6. 借金の手動返済（DB行ロック RPC を使用）
@app.post("/api/loans/repay")
async def repay_loan(data: LoanRepay, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    
    try:
        res = await client.rpc("execute_loan_repay", {
            "p_loan_id": data.loan_id,
            "p_borrower_user_id": str(user.id),
            "p_repay_amount": data.repay_amount
        }).execute()
        
        result = res.data
        return {"message": f"{result['repaid']}G を返済しました！ 現在の状態: {result['status']}"}
    except Exception as e:
        err_msg = getattr(e, "message", str(e))
        raise HTTPException(status_code=400, detail=f"返済エラー: {err_msg}")


# --------------------------------------------------
# カジノモジュールの登録
# --------------------------------------------------
app.include_router(casino_router) 
app.include_router(inventory_router)
app.include_router(policy_router)
app.include_router(factory_router)
app.include_router(card_router)

# ==================================================
# 資産保有税（所得税・保管料）処理モジュール (main.py用)
# ==================================================
from fastapi import Header, HTTPException
from db import get_supabase
from casino import get_user_from_token

def calculate_wealth_tax(balance: int) -> int:
    """
    【火金・完全一律 0.5%版】
    ・10万以下: 0%（初心者保護のため無税）
    ・10万超: 所持金全額に対して一律 0.5%
    """
    if balance <= 100_000:
        return 0

    # 一律0.5% (例: 1000万なら5万G、10億なら500万G)
    return int(balance * 0.005)


@app.post("/api/admin/collect-tax")
async def collect_wealth_tax(authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    supabase = await get_supabase()

    # roleカラムで王(king)かチェック
    profile_res = await supabase.table("profiles").select("role").eq("id", user.id).execute()
    if not profile_res.data or profile_res.data[0].get("role") != "king":
        raise HTTPException(status_code=403, detail="管理者（King）権限がありません。")

    wallet_res = await supabase.table("wallets").select("id, wallet_id, balance").gt("balance", 100000).execute()
    wallets = wallet_res.data or []

    taxed_results = []
    total_tax_collected = 0

    for w in wallets:
        current_bal = w["balance"]
        tax_amount = calculate_wealth_tax(current_bal)

        if tax_amount > 0:
            new_bal = current_bal - tax_amount
            await supabase.table("wallets").update({"balance": new_bal}).eq("id", w["id"]).execute()

            total_tax_collected += tax_amount
            taxed_results.append({
                "wallet_id": w["wallet_id"],
                "before": current_bal,
                "tax": tax_amount,
                "after": new_bal
            })

    return {
        "status": "success",
        "processed_wallets": len(taxed_results),
        "total_tax_collected": total_tax_collected,
        "details": taxed_results
    }
