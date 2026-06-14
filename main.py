from fastapi import FastAPI, Body, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
from io import StringIO
import asyncio
import hashlib
import json
import requests
import pandas as pd

try:
    from pywebpush import webpush, WebPushException
except Exception:
    webpush = None
    class WebPushException(Exception):
        response = None

app = FastAPI(title="War Zone Control")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# ثابت الألعاب والملفات
# =========================
SPORTS = ["Football", "Dodgeball", "Volleyball", "Ultimate Ball"]
SPORT_LABELS = {
    "Football": "Football ⚽",
    "Dodgeball": "Dodgeball 🤾🏻",
    "Volleyball": "Volleyball 🏐",
    "Ultimate Ball": "Ultimate Ball 🥏",
}
VERSION_LABELS = {"1": "المجموعات", "2": "المجموعات 2"}
DAY_LABELS = {"Day1": "اليوم الأول", "Day2": "اليوم الثاني"}

DEFAULT_VISIBILITY = {
    "groups": True,
    "groups2": True,
    "finals": True,
    "matches_day1": True,
    "matches_day2": True,
    "results_day1": True,
    "results_day2": True,
}

DATA_FILE = Path("warzone_data.json")

MATCHES_URLS = {
    "Day1": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTPGvQX6sgITTWxbUXDqQzLSmQqU6TBxmZJDt0DS9pKOMNnoK7490Bn1TvNQrFlGdJZIH0Z9YPGTYb6/pub?gid=186915705&single=true&output=csv",
    "Day2": "https://docs.google.com/spreadsheets/d/e/2PACX-1vRqzlySvoK19S0Maw_xLSlUMmGcOPx6eNqiwKJKCtrHwkDxKuO95ZJKbvyNcXns8TxRe1oYnhZRtlNs/pub?gid=1547895490&single=true&output=csv",
}

all_matches_data: Dict[str, List[Dict[str, Any]]] = {k: [] for k in MATCHES_URLS}

# =========================
# Push Notifications
# =========================
VAPID_PUBLIC_KEY = "BNzit0AtKjV98NKB0QTVt8wpzvpEmxpmCq6PGIbxafoJUwjy7oODmFKoMSjNykAu6vp2ZHXhD4xeLunAD5AkIdo"
VAPID_PRIVATE_KEY = "EovBlK04jq_suYT2t2ULH-gmM_d6smFSoTihYi9roPs"
VAPID_CLAIMS = {"sub": "mailto:admin@warzone.com"}
subscribers = set()

# =========================
# Admin Login
# =========================
ADMIN_PASSWORD = "BeshooWarZone"
ADMIN_COOKIE_NAME = "warzone_admin_auth"
ADMIN_TOKEN = hashlib.sha256(f"warzone-admin:{ADMIN_PASSWORD}".encode("utf-8")).hexdigest()


class LoginPayload(BaseModel):
    password: str


def require_admin(request: Request) -> None:
    if request.cookies.get(ADMIN_COOKIE_NAME) != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="محتاج تسجل دخول للأدمن")


class NotificationPayload(BaseModel):
    title: str
    body: str


class GroupPayload(BaseModel):
    sport: str
    version: str
    group: str
    teams: List[str]


class ResultPayload(BaseModel):
    schedule_key: str
    day_name: str
    sport: str
    version: str
    group: str
    team1: str
    team2: str
    score1: int = Field(..., ge=0)
    score2: int = Field(..., ge=0)
    match_time: str = ""
    match_text: str = ""
    notify: bool = False


class FinalMatch(BaseModel):
    team1: str = ""
    team2: str = ""
    score1: str = "-"
    score2: str = "-"


class FinalsPayload(BaseModel):
    sport: str
    semi1: FinalMatch
    semi2: FinalMatch
    final: FinalMatch


class VisibilityPayload(BaseModel):
    visibility: Dict[str, bool]


# =========================
# Data helpers
# =========================
def default_finals_for_sport(sport: str) -> Dict[str, Dict[str, str]]:
    return {
        "sport": sport,
        "semi1": {"team1": "X1", "team2": "Y2", "score1": "-", "score2": "-"},
        "semi2": {"team1": "X2", "team2": "Y1", "score1": "-", "score2": "-"},
        "final": {"team1": "الفائز 1", "team2": "الفائز 2", "score1": "-", "score2": "-"},
    }


def blank_data() -> Dict[str, Any]:
    return {
        "groups": {sport: {"1": {}, "2": {}} for sport in SPORTS},
        "results": {},
        "finals": {sport: default_finals_for_sport(sport) for sport in SPORTS},
        "visibility": DEFAULT_VISIBILITY.copy(),
    }


def load_data() -> Dict[str, Any]:
    if not DATA_FILE.exists():
        data = blank_data()
        save_data(data)
        return data

    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = blank_data()

    # migrations / safety
    data.setdefault("groups", {})
    data.setdefault("results", {})
    data.setdefault("finals", {})
    data.setdefault("visibility", {})
    for sport in SPORTS:
        data["groups"].setdefault(sport, {})
        data["groups"][sport].setdefault("1", {})
        data["groups"][sport].setdefault("2", {})
        data["finals"].setdefault(sport, default_finals_for_sport(sport))
    for key, default_value in DEFAULT_VISIBILITY.items():
        data["visibility"].setdefault(key, default_value)
    return data


def save_data(data: Dict[str, Any]) -> None:
    with DATA_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def clean_team_name(name: Any) -> str:
    return str(name or "").strip()


def normalize_sport_and_version(sport_name: str) -> tuple[str, str]:
    sport_name = sport_name.strip()
    if sport_name.endswith("2"):
        maybe_sport = sport_name[:-1]
        if maybe_sport in SPORTS:
            return maybe_sport, "2"
    if sport_name in SPORTS:
        return sport_name, "1"
    raise HTTPException(status_code=404, detail="Sport not found")


def get_schedule_sport_column(sport: str) -> str:
    clean = str(sport or "").replace("2", "").strip()
    if clean in ["Ultimate", "Ultimate Ball"]:
        return "Ultimate Ball"
    return clean


def make_schedule_key(day_name: str, sport: str, row_index: int, match_time: str, match_text: str) -> str:
    raw = f"{day_name}|{sport}|{row_index}|{match_time}|{match_text}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def parse_match_text(match_text: Any) -> tuple[str, str]:
    text = " ".join(str(match_text or "").replace("\n", " ").split()).strip()
    if not text or text == "-":
        return "", ""

    separators = [
        " ضد ", " VS ", " vs ", " Vs ", " v ", " V ",
        " × ", " x ", " X ", " - ", " – ", " — ", " / ", " | ", ":",
    ]
    for sep in separators:
        if sep in text:
            parts = [p.strip() for p in text.split(sep, 1)]
            if len(parts) == 2 and parts[0] and parts[1]:
                return parts[0], parts[1]

    # fallback للماتشات المكتوبة بدون مسافات: TeamA-TeamB أو 3-4
    for sep in ["-", "–", "—", "×", "x", "X", "/", "|"]:
        if sep in text:
            parts = [p.strip() for p in text.split(sep, 1)]
            if len(parts) == 2 and parts[0] and parts[1]:
                return parts[0], parts[1]

    return "", ""


def fetch_matches_once(day_name: str) -> List[Dict[str, Any]]:
    if day_name not in MATCHES_URLS:
        raise HTTPException(status_code=404, detail="اليوم غير موجود")
    response = requests.get(MATCHES_URLS[day_name], timeout=20)
    response.raise_for_status()
    response.encoding = "utf-8"
    df = pd.read_csv(StringIO(response.text))
    df.columns = df.columns.str.strip()
    df = df.fillna("")
    rows = df.to_dict(orient="records")
    all_matches_data[day_name] = rows
    return rows


def get_schedule_rows(day_name: str) -> List[Dict[str, Any]]:
    if day_name not in MATCHES_URLS:
        raise HTTPException(status_code=404, detail="اليوم غير موجود")
    if not all_matches_data.get(day_name):
        try:
            return fetch_matches_once(day_name)
        except Exception as e:
            print(f"❌ Error loading matches {day_name}: {e}")
            return []
    return all_matches_data.get(day_name, [])


def find_group_for_match(data: Dict[str, Any], sport: str, team1: str, team2: str) -> Dict[str, str]:
    t1 = normalize_text(team1)
    t2 = normalize_text(team2)
    if not t1 or not t2:
        return {"version": "", "group": ""}

    for version in ["1", "2"]:
        groups = data.get("groups", {}).get(sport, {}).get(version, {})
        for group_name, teams in groups.items():
            team_norms = {normalize_text(t) for t in teams}
            if t1 in team_norms and t2 in team_norms:
                return {"version": version, "group": group_name}
    return {"version": "", "group": ""}


def build_available_schedule_matches(day_name: str, sport: str) -> List[Dict[str, Any]]:
    if sport not in SPORTS:
        raise HTTPException(status_code=400, detail="اللعبة غير صحيحة")
    data = load_data()
    rows = get_schedule_rows(day_name)
    column = get_schedule_sport_column(sport)
    available: List[Dict[str, Any]] = []

    for index, row in enumerate(rows):
        match_text = str(row.get(column, "") or "").strip()
        if not match_text or match_text == "-":
            continue
        match_time = str(row.get("التوقيت", row.get("time", row.get("Time", ""))) or "").strip()
        schedule_key = make_schedule_key(day_name, sport, index, match_time, match_text)
        if schedule_key in data.get("results", {}):
            continue

        team1, team2 = parse_match_text(match_text)
        group_info = find_group_for_match(data, sport, team1, team2)
        label_parts = []
        if match_time:
            label_parts.append(match_time)
        label_parts.append(match_text)
        if group_info.get("group"):
            label_parts.append(f"{VERSION_LABELS.get(group_info['version'], '')} / المجموعة {group_info['group']}")

        available.append({
            "id": schedule_key,
            "schedule_key": schedule_key,
            "day_name": day_name,
            "day_label": DAY_LABELS.get(day_name, day_name),
            "sport": sport,
            "sport_label": SPORT_LABELS.get(sport, sport),
            "row_index": index,
            "time": match_time,
            "match_time": match_time,
            "match_text": match_text,
            "team1": team1,
            "team2": team2,
            "version": group_info.get("version", ""),
            "group": group_info.get("group", ""),
            "can_parse": bool(team1 and team2),
            "label": " | ".join(label_parts),
        })
    return available




def canonical_team_name(data: Dict[str, Any], sport: str, version: str, group: str, input_name: str) -> str:
    teams = data.get("groups", {}).get(sport, {}).get(version, {}).get(group, [])
    wanted = normalize_text(input_name)
    for team in teams:
        if normalize_text(team) == wanted:
            return clean_team_name(team)
    return clean_team_name(input_name)


def ensure_result_valid(data: Dict[str, Any], payload: ResultPayload) -> None:
    if payload.sport not in SPORTS:
        raise HTTPException(status_code=400, detail="اللعبة غير صحيحة")
    if payload.version not in ["1", "2"]:
        raise HTTPException(status_code=400, detail="اختار المجموعات أو المجموعات 2")
    if payload.day_name not in ["Day1", "Day2"]:
        raise HTTPException(status_code=400, detail="اليوم غير صحيح")
    if normalize_text(payload.team1) == normalize_text(payload.team2):
        raise HTTPException(status_code=400, detail="لا يمكن اختيار نفس الفريق مرتين")

    groups = data.get("groups", {}).get(payload.sport, {}).get(payload.version, {})
    teams = groups.get(payload.group)
    if teams is None:
        raise HTTPException(status_code=404, detail="المجموعة غير موجودة في الأدمن")

    team_norms = {normalize_text(t) for t in teams}
    if normalize_text(payload.team1) not in team_norms or normalize_text(payload.team2) not in team_norms:
        raise HTTPException(status_code=404, detail="الفريقين لازم يكونوا موجودين في نفس المجموعة المختارة")


def calculate_standings(data: Dict[str, Any], sport: str, version: str) -> Dict[str, List[Dict[str, Any]]]:
    groups = data.get("groups", {}).get(sport, {}).get(version, {})
    results = data.get("results", {})
    standings: Dict[str, List[Dict[str, Any]]] = {}

    for group_name in sorted(groups.keys()):
        teams = [clean_team_name(t) for t in groups[group_name] if clean_team_name(t)]
        table: Dict[str, Dict[str, Any]] = {}
        for team in teams:
            table[team] = {
                "الفريق": team,
                "لعب": 0,
                "فوز": 0,
                "تعادل": 0,
                "خسارة": 0,
                "له": 0,
                "عليه": 0,
                "فرق": 0,
                "نقاط": 0,
            }

        for result in results.values():
            if result.get("sport") != sport or result.get("version") != version or result.get("group") != group_name:
                continue
            t1, t2 = result.get("team1", ""), result.get("team2", "")
            if t1 not in table or t2 not in table:
                continue
            s1, s2 = int(result.get("score1", 0)), int(result.get("score2", 0))

            table[t1]["لعب"] += 1
            table[t2]["لعب"] += 1
            table[t1]["له"] += s1
            table[t1]["عليه"] += s2
            table[t2]["له"] += s2
            table[t2]["عليه"] += s1

            if s1 > s2:
                table[t1]["فوز"] += 1
                table[t2]["خسارة"] += 1
                table[t1]["نقاط"] += 3
            elif s2 > s1:
                table[t2]["فوز"] += 1
                table[t1]["خسارة"] += 1
                table[t2]["نقاط"] += 3
            else:
                table[t1]["تعادل"] += 1
                table[t2]["تعادل"] += 1
                table[t1]["نقاط"] += 1
                table[t2]["نقاط"] += 1

        rows = []
        for row in table.values():
            row["فرق"] = row["له"] - row["عليه"]
            rows.append(row)
        rows.sort(key=lambda r: (-r["نقاط"], -r["فرق"], -r["له"], r["عليه"], r["الفريق"]))
        standings[group_name] = rows

    return standings


def get_day_results_list(data: Dict[str, Any], day_name: str, sport: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = [r for r in data.get("results", {}).values() if r.get("day_name") == day_name]
    if sport:
        if sport not in SPORTS:
            raise HTTPException(status_code=400, detail="اللعبة غير صحيحة")
        rows = [r for r in rows if r.get("sport") == sport]
    rows.sort(key=lambda r: (str(r.get("sport", "")), str(r.get("match_time", "")), str(r.get("group", ""))))
    return rows


def send_push_to_all(title: str, body: str) -> int:
    if webpush is None:
        print("pywebpush is not installed; notification skipped.")
        return 0
    message_data = json.dumps({"title": title, "body": body}, ensure_ascii=False)
    inactive_subs = []
    for sub_str in list(subscribers):
        sub_data = json.loads(sub_str)
        try:
            webpush(
                subscription_info=sub_data,
                data=message_data,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS,
            )
        except WebPushException as ex:
            if getattr(ex, "response", None) and ex.response.status_code in [404, 410]:
                inactive_subs.append(sub_str)
        except Exception as ex:
            print(f"Push error: {ex}")
    for sub in inactive_subs:
        subscribers.discard(sub)
    return len(subscribers)


async def sync_matches_loop():
    while True:
        for day in MATCHES_URLS:
            try:
                rows = await asyncio.to_thread(fetch_matches_once, day)
                print(f"✅ Updated sheet matches: {day} ({len(rows)} rows)")
            except Exception as e:
                print(f"❌ Error syncing {day}: {e}")
        await asyncio.sleep(120)


# =========================
# Static pages
# =========================
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(sync_matches_loop())


@app.get("/")
async def serve_home():
    return FileResponse("index.html")


@app.get("/admin")
async def serve_admin():
    return FileResponse("admin.html")


@app.get("/sw.js")
async def serve_sw():
    return FileResponse("sw.js", media_type="application/javascript")


# =========================
# Admin login routes
# =========================
@app.post("/admin/login")
async def admin_login(payload: LoginPayload, response: Response):
    if payload.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="كلمة السر غير صحيحة")
    response.set_cookie(
        key=ADMIN_COOKIE_NAME,
        value=ADMIN_TOKEN,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=60 * 60 * 12,
    )
    return {"status": "success", "message": "تم تسجيل الدخول"}


@app.post("/admin/logout")
async def admin_logout(response: Response):
    response.delete_cookie(ADMIN_COOKIE_NAME)
    return {"status": "success", "message": "تم تسجيل الخروج"}


# =========================
# Notification routes
# =========================
@app.post("/subscribe")
async def subscribe(subscription: dict = Body(...)):
    subscribers.add(json.dumps(subscription, sort_keys=True))
    return {"status": "success", "total": len(subscribers)}


@app.post("/send-notification")
async def send_notification(payload: NotificationPayload, request: Request):
    require_admin(request)
    sent_to = send_push_to_all(payload.title, payload.body)
    return {"status": "success", "sent_to": sent_to}


# =========================
# Public data routes
# =========================
@app.get("/standings/{sport_name}")
def get_standings(sport_name: str):
    data = load_data()
    sport, version = normalize_sport_and_version(sport_name)
    return calculate_standings(data, sport, version)


@app.get("/matches/{day_name}")
def get_matches(day_name: str):
    return get_schedule_rows(day_name)


@app.get("/day-results/{day_name}")
def get_day_results(day_name: str, sport: Optional[str] = Query(None)):
    if day_name not in ["Day1", "Day2"]:
        raise HTTPException(status_code=404, detail="اليوم غير موجود")
    return get_day_results_list(load_data(), day_name, sport)


@app.get("/finals/{sport_name}")
def get_finals(sport_name: str):
    sport, _ = normalize_sport_and_version(sport_name)
    data = load_data()
    return data.get("finals", {}).get(sport, default_finals_for_sport(sport))


@app.get("/site-settings")
def get_site_settings():
    data = load_data()
    return {"visibility": data.get("visibility", DEFAULT_VISIBILITY.copy())}


# =========================
# Admin data routes
# =========================
@app.get("/admin-data")
def get_admin_data(request: Request):
    require_admin(request)
    data = load_data()
    return {
        "sports": SPORTS,
        "sport_labels": SPORT_LABELS,
        "version_labels": VERSION_LABELS,
        "day_labels": DAY_LABELS,
        "groups": data.get("groups", {}),
        "results": list(data.get("results", {}).values()),
        "finals": data.get("finals", {}),
        "visibility": data.get("visibility", DEFAULT_VISIBILITY.copy()),
    }


@app.post("/admin/group")
def save_group(payload: GroupPayload, request: Request):
    require_admin(request)
    sport = payload.sport.strip()
    version = str(payload.version).strip()
    group = payload.group.strip()
    if sport not in SPORTS:
        raise HTTPException(status_code=400, detail="اللعبة غير صحيحة")
    if version not in ["1", "2"]:
        raise HTTPException(status_code=400, detail="التاب غير صحيح")
    if not group:
        raise HTTPException(status_code=400, detail="اسم المجموعة مطلوب")

    teams = []
    seen = set()
    for team in payload.teams:
        clean = clean_team_name(team)
        key = normalize_text(clean)
        if clean and key not in seen:
            teams.append(clean)
            seen.add(key)
    if len(teams) < 2:
        raise HTTPException(status_code=400, detail="لازم تضيف فريقين على الأقل")

    data = load_data()
    data["groups"][sport][version][group] = teams
    save_data(data)
    return {"status": "success", "message": "تم حفظ المجموعة"}


@app.delete("/admin/group")
def delete_group(request: Request, sport: str = Query(...), version: str = Query(...), group: str = Query(...)):
    require_admin(request)
    data = load_data()
    if sport not in SPORTS or version not in ["1", "2"]:
        raise HTTPException(status_code=400, detail="بيانات غير صحيحة")
    groups = data["groups"].get(sport, {}).get(version, {})
    if group not in groups:
        raise HTTPException(status_code=404, detail="المجموعة غير موجودة")
    del groups[group]
    # سيب النتائج محفوظة للرجوع، لكنها لن تؤثر على الترتيب لو المجموعة اتحذفت
    save_data(data)
    return {"status": "success", "message": "تم حذف المجموعة"}


@app.get("/admin/available-schedule-matches")
def available_schedule_matches(request: Request, day_name: str = "Day1", sport: str = "Football"):
    require_admin(request)
    return build_available_schedule_matches(day_name, sport)


@app.post("/admin/result")
def save_result(payload: ResultPayload, request: Request):
    require_admin(request)
    data = load_data()
    ensure_result_valid(data, payload)
    key = payload.schedule_key.strip()
    if not key:
        raw = f"{payload.day_name}|{payload.sport}|{payload.version}|{payload.group}|{payload.team1}|{payload.team2}|{payload.match_time}|{payload.match_text}"
        key = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]

    team1_canonical = canonical_team_name(data, payload.sport, payload.version, payload.group, payload.team1)
    team2_canonical = canonical_team_name(data, payload.sport, payload.version, payload.group, payload.team2)

    data.setdefault("results", {})
    data["results"][key] = {
        "id": key,
        "schedule_key": key,
        "day_name": payload.day_name,
        "day_label": DAY_LABELS.get(payload.day_name, payload.day_name),
        "sport": payload.sport,
        "sport_label": SPORT_LABELS.get(payload.sport, payload.sport),
        "version": payload.version,
        "version_label": VERSION_LABELS.get(payload.version, payload.version),
        "group": payload.group,
        "team1": team1_canonical,
        "team2": team2_canonical,
        "score1": payload.score1,
        "score2": payload.score2,
        "match_time": payload.match_time,
        "match_text": payload.match_text,
        "played_at": datetime.utcnow().isoformat() + "Z",
    }
    save_data(data)

    sent_to = 0
    if payload.notify:
        title = f"نتيجة {SPORT_LABELS.get(payload.sport, payload.sport)} 🏆"
        body = f"{team1_canonical} {payload.score1} - {payload.score2} {team2_canonical} | {DAY_LABELS.get(payload.day_name)}"
        sent_to = send_push_to_all(title, body)

    return {
        "status": "success",
        "message": "تم حفظ النتيجة وتحديث الترتيب ونتائج اليوم",
        "sent_to": sent_to,
        "result": data["results"][key],
        "standings": calculate_standings(data, payload.sport, payload.version),
    }


@app.delete("/admin/result/{result_id}")
def delete_result(result_id: str, request: Request):
    require_admin(request)
    data = load_data()
    if result_id not in data.get("results", {}):
        raise HTTPException(status_code=404, detail="النتيجة غير موجودة")
    del data["results"][result_id]
    save_data(data)
    return {"status": "success", "message": "تم حذف النتيجة والماتش رجع لقائمة التسجيل"}


@app.post("/admin/finals")
def save_finals(payload: FinalsPayload, request: Request):
    require_admin(request)
    sport = payload.sport.strip()
    if sport not in SPORTS:
        raise HTTPException(status_code=400, detail="اللعبة غير صحيحة")
    data = load_data()
    data.setdefault("finals", {})
    data["finals"][sport] = {
        "sport": sport,
        "semi1": payload.semi1.dict(),
        "semi2": payload.semi2.dict(),
        "final": payload.final.dict(),
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    save_data(data)
    return {"status": "success", "message": "تم حفظ النهائيات", "finals": data["finals"][sport]}


@app.post("/admin/visibility")
def save_visibility(payload: VisibilityPayload, request: Request):
    require_admin(request)
    data = load_data()
    current = data.get("visibility", DEFAULT_VISIBILITY.copy())
    for key in DEFAULT_VISIBILITY:
        if key in payload.visibility:
            current[key] = bool(payload.visibility[key])
    data["visibility"] = current
    save_data(data)
    return {"status": "success", "message": "تم تحديث إظهار/إخفاء التابات", "visibility": current}
