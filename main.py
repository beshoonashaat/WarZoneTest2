import pandas as pd
import requests
from io import StringIO
from fastapi import FastAPI, Body, HTTPException
import asyncio
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import json  # 👈 مهم جداً لعمليات الـ JSON
import os
from copy import deepcopy
from pywebpush import webpush, WebPushException # 👈 المكتبة اللي بتبعت الإشعارات

# 1. تعريف موديلات البيانات
class NotificationPayload(BaseModel):
    title: str
    body: str

class MatchResultPayload(BaseModel):
    sport_name: str
    group_name: str
    team1: str
    team2: str
    score1: int = Field(..., ge=0)
    score2: int = Field(..., ge=0)
    send_push: bool = True

app = FastAPI()

# 2. إعدادات الـ CORS والـ VAPID
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

VAPID_PUBLIC_KEY = "BNzit0AtKjV98NKB0QTVt8wpzvpEmxpmCq6PGIbxafoJUwjy7oODmFKoMSjNykAu6vp2ZHXhD4xeLunAD5AkIdo"
VAPID_PRIVATE_KEY = "EovBlK04jq_suYT2t2ULH-gmM_d6smFSoTihYi9roPs"
VAPID_CLAIMS = {"sub": "mailto:admin@warzone.com"}

# 3. مخزن المشتركين (لازم يتعرف هنا عشان السيرفر يشوفه)
subscribers = set()

RESULTS_FILE = "match_results.json"

# --- أدوات مساعدة للنتائج والترتيب ---

def normalize_text(value):
    return " ".join(str(value or "").strip().split()).casefold()

def get_team_name(row):
    for col in ["الفريق", "الفريق ", "Team", "team"]:
        if col in row and str(row.get(col, "")).strip():
            return str(row.get(col, "")).strip()
    return ""

def to_int(value):
    try:
        if value == "":
            return 0
        return int(float(value))
    except Exception:
        return 0

def add_stat(row, column, amount):
    row[column] = to_int(row.get(column, 0)) + amount

def add_first_existing_stat(row, possible_columns, amount):
    for col in possible_columns:
        if col in row:
            add_stat(row, col, amount)
            return

def get_points_column(row):
    if "نقاط" in row:
        return "نقاط"
    if "النقاط" in row:
        return "النقاط"
    row["نقاط"] = 0
    return "نقاط"

def get_stat_value(row, possible_columns):
    for col in possible_columns:
        if col in row:
            return to_int(row.get(col, 0))
    return 0

def make_match_key(sport_name, group_name, team1, team2):
    teams = sorted([normalize_text(team1), normalize_text(team2)])
    return f"{normalize_text(sport_name)}|{normalize_text(group_name)}|{teams[0]}|{teams[1]}"

def load_match_results():
    if not os.path.exists(RESULTS_FILE):
        return {}
    try:
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"❌ Error loading {RESULTS_FILE}: {e}")
        return {}

def save_match_results():
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(match_results, f, ensure_ascii=False, indent=2)

def sort_standings_rows(rows):
    def sort_key(row):
        group_name = normalize_text(row.get("المجموعة", "A"))
        points = get_stat_value(row, ["نقاط", "النقاط"])
        goal_diff = get_stat_value(row, ["فرق", "فارق", "فرق الأهداف", "Goal Difference", "GD"])
        wins = get_stat_value(row, ["فوز", "Wins", "W"])
        goals_for = get_stat_value(row, ["له", "أهداف له", "اهداف له", "Goals For", "GF"])
        return (group_name, -points, -goal_diff, -wins, -goals_for, normalize_text(get_team_name(row)))
    return sorted(rows, key=sort_key)

def apply_one_result(rows, result):
    sport_name = result["sport_name"]
    group_name = normalize_text(result["group_name"])
    team1_name = normalize_text(result["team1"])
    team2_name = normalize_text(result["team2"])
    score1 = to_int(result["score1"])
    score2 = to_int(result["score2"])

    team1_row = None
    team2_row = None

    for row in rows:
        row_group = normalize_text(row.get("المجموعة", "A"))
        row_team = normalize_text(get_team_name(row))
        if row_group != group_name:
            continue
        if row_team == team1_name:
            team1_row = row
        elif row_team == team2_name:
            team2_row = row

    if not team1_row or not team2_row:
        available = [get_team_name(r) for r in rows if normalize_text(r.get("المجموعة", "A")) == group_name]
        raise ValueError(
            f"لم أجد الفريقين داخل {sport_name} / المجموعة {result['group_name']}. الفرق المتاحة: {available}"
        )

    # Played
    add_stat(team1_row, "لعب", 1)
    add_stat(team2_row, "لعب", 1)

    # Optional score stats if your sheet has these columns
    add_first_existing_stat(team1_row, ["له", "أهداف له", "اهداف له", "Goals For", "GF"], score1)
    add_first_existing_stat(team1_row, ["عليه", "أهداف عليه", "اهداف عليه", "Goals Against", "GA"], score2)
    add_first_existing_stat(team1_row, ["فرق", "فارق", "فرق الأهداف", "Goal Difference", "GD"], score1 - score2)

    add_first_existing_stat(team2_row, ["له", "أهداف له", "اهداف له", "Goals For", "GF"], score2)
    add_first_existing_stat(team2_row, ["عليه", "أهداف عليه", "اهداف عليه", "Goals Against", "GA"], score1)
    add_first_existing_stat(team2_row, ["فرق", "فارق", "فرق الأهداف", "Goal Difference", "GD"], score2 - score1)

    points_col_1 = get_points_column(team1_row)
    points_col_2 = get_points_column(team2_row)

    if score1 > score2:
        add_stat(team1_row, "فوز", 1)
        add_stat(team2_row, "خسارة", 1)
        add_stat(team1_row, points_col_1, 3)
    elif score2 > score1:
        add_stat(team2_row, "فوز", 1)
        add_stat(team1_row, "خسارة", 1)
        add_stat(team2_row, points_col_2, 3)
    else:
        add_stat(team1_row, "تعادل", 1)
        add_stat(team2_row, "تعادل", 1)
        add_stat(team1_row, points_col_1, 1)
        add_stat(team2_row, points_col_2, 1)

def apply_saved_results_to_sport(sport_name):
    base_rows = deepcopy(all_sports_base_data.get(sport_name, []))
    if not base_rows:
        all_sports_data[sport_name] = []
        return

    for result in match_results.values():
        if result.get("sport_name") == sport_name:
            try:
                apply_one_result(base_rows, result)
            except ValueError as e:
                print(f"⚠️ Result skipped: {e}")

    all_sports_data[sport_name] = sort_standings_rows(base_rows)

def apply_saved_results_to_all_sports():
    for sport_name in SHEET_URLS.keys():
        apply_saved_results_to_sport(sport_name)

async def push_to_subscribers(title, body):
    message_data = json.dumps({"title": title, "body": body})
    inactive_subs = []

    print(f"🚀 محاولة إرسال إشعار لـ {len(subscribers)} جهاز...")

    for sub_str in subscribers:
        sub_data = json.loads(sub_str)
        try:
            webpush(
                subscription_info=sub_data,
                data=message_data,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS
            )
        except WebPushException as ex:
            print(f"❌ خطأ في جهاز: {ex}")
            if ex.response and ex.response.status_code in [404, 410]:
                inactive_subs.append(sub_str)
        except Exception as ex:
            print(f"❌ خطأ غير متوقع: {ex}")

    for sub in inactive_subs:
        subscribers.remove(sub)

    return len(subscribers)

match_results = load_match_results()

# --- المسارات الخاصة بالصفحات والإشعارات ---

@app.get("/admin")
async def serve_admin():
    return FileResponse("admin.html")

@app.get("/sw.js")
async def serve_sw():
    return FileResponse("sw.js", media_type="application/javascript")

@app.post("/subscribe")
async def subscribe(subscription: dict = Body(...)):
    subscribers.add(json.dumps(subscription))
    print(f"✅ مشترك جديد انضم! إجمالي المشتركين: {len(subscribers)}")
    return {"status": "success"}

@app.post("/send-notification")
async def send_notification(payload: NotificationPayload):
    sent_to = await push_to_subscribers(payload.title, payload.body)
    return {"status": "success", "sent_to": sent_to}

# --- كود مزامنة البيانات ---

SHEET_URLS = {
    "Football": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTPGvQX6sgITTWxbUXDqQzLSmQqU6TBxmZJDt0DS9pKOMNnoK7490Bn1TvNQrFlGdJZIH0Z9YPGTYb6/pub?gid=621025358&single=true&output=csv",
    "Dodgeball": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTPGvQX6sgITTWxbUXDqQzLSmQqU6TBxmZJDt0DS9pKOMNnoK7490Bn1TvNQrFlGdJZIH0Z9YPGTYb6/pub?gid=863642824&single=true&output=csv",
    "Volleyball": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTPGvQX6sgITTWxbUXDqQzLSmQqU6TBxmZJDt0DS9pKOMNnoK7490Bn1TvNQrFlGdJZIH0Z9YPGTYb6/pub?gid=1033302345&single=true&output=csv",
    "Ultimate Ball": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTPGvQX6sgITTWxbUXDqQzLSmQqU6TBxmZJDt0DS9pKOMNnoK7490Bn1TvNQrFlGdJZIH0Z9YPGTYb6/pub?gid=2017169226&single=true&output=csv",
    "Football2": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTPGvQX6sgITTWxbUXDqQzLSmQqU6TBxmZJDt0DS9pKOMNnoK7490Bn1TvNQrFlGdJZIH0Z9YPGTYb6/pub?gid=907297379&single=true&output=csv",
    "Dodgeball2": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTPGvQX6sgITTWxbUXDqQzLSmQqU6TBxmZJDt0DS9pKOMNnoK7490Bn1TvNQrFlGdJZIH0Z9YPGTYb6/pub?gid=402610111&single=true&output=csv",
    "Volleyball2": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTPGvQX6sgITTWxbUXDqQzLSmQqU6TBxmZJDt0DS9pKOMNnoK7490Bn1TvNQrFlGdJZIH0Z9YPGTYb6/pub?gid=42182221&single=true&output=csv",
    "Ultimate Ball2": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTPGvQX6sgITTWxbUXDqQzLSmQqU6TBxmZJDt0DS9pKOMNnoK7490Bn1TvNQrFlGdJZIH0Z9YPGTYb6/pub?gid=1116838793&single=true&output=csv"
}

MATCHES_URLS = {
    "Day1": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTPGvQX6sgITTWxbUXDqQzLSmQqU6TBxmZJDt0DS9pKOMNnoK7490Bn1TvNQrFlGdJZIH0Z9YPGTYb6/pub?gid=186915705&single=true&output=csv",
    "Day2": "https://docs.google.com/spreadsheets/d/e/2PACX-1vRqzlySvoK19S0Maw_xLSlUMmGcOPx6eNqiwKJKCtrHwkDxKuO95ZJKbvyNcXns8TxRe1oYnhZRtlNs/pub?gid=1547895490&single=true&output=csv",
}

# base = البيانات الأصلية من Google Sheets، data = البيانات بعد تطبيق نتائج الأدمن
all_sports_base_data = {k: [] for k in SHEET_URLS.keys()}
all_sports_data = {k: [] for k in SHEET_URLS.keys()}
all_matches_data = {k: [] for k in MATCHES_URLS.keys()}

async def sync_all_data_loop():
    while True:
        loop = asyncio.get_event_loop()

        # تحديث جداول الترتيب
        for sport, url in SHEET_URLS.items():
            try:
                response = await loop.run_in_executor(None, requests.get, url)
                if response.status_code == 200:
                    response.encoding = 'utf-8'
                    df = pd.read_csv(StringIO(response.text))
                    df.columns = df.columns.str.strip()
                    df = df.fillna("")

                    all_sports_base_data[sport] = df.to_dict(orient='records')
                    apply_saved_results_to_sport(sport)
                    print(f"✅ Updated standings: {sport} ({len(all_sports_data[sport])} rows)")
            except Exception as e:
                print(f"❌ Error in standings {sport}: {e}")

        # تحديث جداول الماتشات اليومية
        for day, url in MATCHES_URLS.items():
            try:
                response = await loop.run_in_executor(None, requests.get, url)
                if response.status_code == 200:
                    response.encoding = 'utf-8'
                    df = pd.read_csv(StringIO(response.text))
                    df.columns = df.columns.str.strip()
                    df = df.fillna("")

                    all_matches_data[day] = df.to_dict(orient='records')
                    print(f"✅ Updated matches: {day} ({len(all_matches_data[day])} rows)")
                else:
                    print(f"❌ Matches {day} returned status code: {response.status_code}")
            except Exception as e:
                print(f"❌ Error in matches {day}: {e}")

        await asyncio.sleep(120)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(sync_all_data_loop())

@app.get("/")
async def serve_home():
    return FileResponse("index.html")

@app.get("/standings/{sport_name}")
def get_standings(sport_name: str):
    data = all_sports_data.get(sport_name, [])
    groups = {}
    for entry in data:
        grp = str(entry.get('المجموعة', 'A')).strip() or "A"
        if grp not in groups:
            groups[grp] = []
        groups[grp].append(entry)
    return groups

@app.get("/matches/{day_name}")
def get_matches(day_name: str):
    return all_matches_data.get(day_name, [])

@app.get("/teams/{sport_name}")
def get_teams(sport_name: str):
    data = all_sports_data.get(sport_name, [])
    groups = {}
    for entry in data:
        grp = str(entry.get('المجموعة', 'A')).strip() or "A"
        team = get_team_name(entry)
        if team:
            groups.setdefault(grp, []).append(team)
    return groups

@app.get("/results")
def get_results():
    return list(match_results.values())

@app.post("/submit-result")
async def submit_result(payload: MatchResultPayload):
    sport_name = payload.sport_name.strip()
    if sport_name not in SHEET_URLS:
        raise HTTPException(status_code=400, detail="اسم اللعبة غير صحيح")

    if not all_sports_base_data.get(sport_name):
        raise HTTPException(status_code=409, detail="بيانات اللعبة لم تتحمل من Google Sheets بعد. افتح الموقع وانتظر ثواني ثم جرب تاني.")

    if normalize_text(payload.team1) == normalize_text(payload.team2):
        raise HTTPException(status_code=400, detail="لا يمكن اختيار نفس الفريق مرتين")

    result = {
        "sport_name": sport_name,
        "group_name": payload.group_name.strip(),
        "team1": payload.team1.strip(),
        "team2": payload.team2.strip(),
        "score1": payload.score1,
        "score2": payload.score2,
    }

    # نجرب تطبيق النتيجة على نسخة مؤقتة الأول، عشان لو اسم فريق غلط ما نحفظش حاجة
    test_rows = deepcopy(all_sports_base_data.get(sport_name, []))
    for old_result in match_results.values():
        if old_result.get("sport_name") == sport_name and make_match_key(old_result.get("sport_name"), old_result.get("group_name"), old_result.get("team1"), old_result.get("team2")) != make_match_key(result.get("sport_name"), result.get("group_name"), result.get("team1"), result.get("team2")):
            try:
                apply_one_result(test_rows, old_result)
            except ValueError:
                pass
    try:
        apply_one_result(test_rows, result)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    key = make_match_key(result.get("sport_name"), result.get("group_name"), result.get("team1"), result.get("team2"))
    match_results[key] = result
    save_match_results()
    apply_saved_results_to_sport(sport_name)

    if payload.send_push:
        title = "نتيجة ماتش جديدة 🏆"
        body = f"{payload.team1} {payload.score1} - {payload.score2} {payload.team2} | {sport_name}"
        await push_to_subscribers(title, body)

    return {
        "status": "success",
        "message": "تم حفظ النتيجة وتحديث جدول الترتيب",
        "result": result,
        "standings": get_standings(sport_name)
    }
