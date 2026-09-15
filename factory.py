import random
import time
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from typing import Dict, Any, Optional, List

from db import get_supabase

router = APIRouter(prefix="/api/factory", tags=["factory"])

PROCESS_STEPS = [
    "① コンポーネント選定（規格部品の受入）",
    "② 回路結線（4系統・アモアス方式）",
    "③ クラッチ・トルクの同期（回転角調整）",
    "④ 圧着シリンダーの油圧加圧",
    "⑤ 最終精度検査および出荷シリアル発行"
]

class ProcessAction(BaseModel):
    step: int
    answer: Any
    wallet_id: Optional[str] = None

async def get_user_profile(authorization: str):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="認証トークンがありません")
    token = authorization.split(" ")[1]
    client = await get_supabase()
    user_res = await client.auth.get_user(token)
    user_id = user_res.user.id
    
    # プロフィール取得または初期化
    prof_res = await client.table("factory_profiles").select("*").eq("user_id", user_id).execute()
    if not prof_res.data:
        init_session = generate_step_data(1)
        init_session["serial_number"] = f"MRK-SYS-{int(time.time())%100000}-{random.randint(100,999)}"
        new_prof = {"user_id": user_id, "session_data": init_session, "current_step": 1}
        await client.table("factory_profiles").insert(new_prof).execute()
        return new_prof, client
    return prof_res.data[0], client

def generate_step_data(step: int) -> Dict[str, Any]:
    base = {"current_step": step, "title": PROCESS_STEPS[step - 1]}
    if step == 1:
        parts = ["SKF-6204ベアリング", "SUS304 M12ボルト", "IC-TTL7400回路", "高耐圧シリコンパッキン", "テンパックス耐熱ガラス", "ARM-Cortex-M7"]
        base.update({"target_part": random.choice(parts), "options": random.sample(parts, len(parts))})
    elif step == 2:
        colors = ["赤", "青", "黄", "緑", "紫", "白"]
        target = random.sample(colors, 4)
        base.update({"instruction": "指定された4色のケーブルを順番に結線せよ", "target_colors": target})
    elif step == 3:
        base.update({"instruction": "クラッチ同期：規定トルク範囲（45 - 55 Nm）内でロックせよ"})
    elif step == 4:
        base.update({"instruction": "手動油圧シリンダー：規定圧（10 Bar）まで加圧せよ"})
    elif step == 5:
        base.update({"instruction": "全機械シーケンス正常完了：最終品質検査をパスして出荷転送を実行"})
    return base

def mask_session_data(session: Dict[str, Any]) -> Dict[str, Any]:
    return {k: session[k] for k in ["current_step", "title", "options", "target_colors", "instruction", "serial_number", "target_part"] if k in session}

@router.get("/status")
async def get_factory_status(authorization: str = Header(None)):
    prof, _ = await get_user_profile(authorization)
    return {
        "session": mask_session_data(prof["session_data"]),
        "steps_total": len(PROCESS_STEPS),
        "wear": prof["wear_level"],
        "upgrades": {
            "wear_res": prof["upg_wear_res"],
            "cooling": prof["upg_cooling"],
            "boost": prof["upg_boost"],
            "precision": prof["upg_precision"]
        }
    }

@router.post("/maintain")
async def maintain_system(authorization: str = Header(None)):
    prof, client = await get_user_profile(authorization)
    await client.table("factory_profiles").update({"wear_level": 0}).eq("user_id", prof["user_id"]).execute()
    return {"message": "[SYSTEM] エアパージ・給油完了。稼働を再開します。", "wear": 0}

@router.post("/process")
async def process_step(data: ProcessAction, authorization: str = Header(None)):
    prof, client = await get_user_profile(authorization)
    session = prof["session_data"]
    
    # 限界値の計算（アップグレード補正）
    max_wear = 100 + (prof["upg_cooling"] * 20)
    wear_increase = max(2, random.randint(5, 12) - prof["upg_wear_res"])
    current_wear = prof["wear_level"]
    req_taps = max(4, 10 - (prof["upg_boost"] * 2))

    if current_wear >= max_wear:
        raise HTTPException(status_code=400, detail=f"[ERROR: WEAR LIMIT] 設備が摩耗限界（{max_wear}%）です。手動パージを実行してください。")
    if not session or session["current_step"] != data.step:
        raise HTTPException(status_code=400, detail="[ERROR: DESYNC] 工程が不整合です。ラインを再読み込みします。")

    new_wear = min(max_wear, current_wear + wear_increase)

    # ゼロトラスト検証
    if data.step == 1 and data.answer != session["target_part"]:
        raise HTTPException(status_code=400, detail="[ERROR: MISMATCH] 規格外の不適合パーツです。")
    elif data.step == 2:
        if not isinstance(data.answer, list) or data.answer != session["target_colors"]:
            raise HTTPException(status_code=400, detail="[ERROR: WIRING FAILED] 結線シーケンスが不一致です。")
    elif data.step == 3:
        try: val = int(data.answer)
        except: val = 0
        if not (45 <= val <= 55): 
            raise HTTPException(status_code=400, detail=f"[ERROR: TOLERANCE EXCEEDED] トルク公差外（{val} Nm）。")
    elif data.step == 4:
        try: val = int(data.answer)
        except: val = 0
        if val < req_taps: 
            raise HTTPException(status_code=400, detail=f"[ERROR: PRESSURE LOW] 油圧不足（要 {req_taps} ストローク）。")
    elif data.step == 5:
        if not data.wallet_id: 
            raise HTTPException(status_code=400, detail="[ERROR: NO DESTINATION] 報酬転送用口座がありません。")
        
        # 報酬計算（精度アップグレード補正）
        base_reward = random.randint(15, 25)
        bonus = prof["upg_precision"] * 5
        reward_gold = base_reward + bonus
        
        w_res = await client.table("wallets").select("*").eq("wallet_id", data.wallet_id).eq("user_id", prof["user_id"]).execute()
        if not w_res.data: 
            raise HTTPException(status_code=400, detail="[ERROR: INVALID ACCOUNT] 口座が存在しません。")
        
        await client.table("wallets").update({"balance": int(w_res.data[0]["balance"]) + reward_gold}).eq("id", w_res.data[0]["id"]).execute()
        
        serial = session.get("serial_number", "UNKNOWN")
        new_session = generate_step_data(1)
        new_session["serial_number"] = f"MRK-SYS-{int(time.time())%100000}-{random.randint(100,999)}"
        
        await client.table("factory_profiles").update({"wear_level": new_wear, "current_step": 1, "session_data": new_session}).eq("user_id", prof["user_id"]).execute()
        
        return {
            "completed": True, "reward": reward_gold, "serial": serial, "wear": new_wear,
            "message": f"製品出荷完了。SERIAL: {serial} | ＋{reward_gold}G 獲得",
            "next_step": mask_session_data(new_session)
        }

    # 次のステップへ進行
    new_session = generate_step_data(data.step + 1)
    new_session["serial_number"] = session.get("serial_number")
    await client.table("factory_profiles").update({"wear_level": new_wear, "current_step": data.step + 1, "session_data": new_session}).eq("user_id", prof["user_id"]).execute()
    
    return {"completed": False, "wear": new_wear, "next_step": mask_session_data(new_session)}
