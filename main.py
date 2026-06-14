from fastapi import FastAPI, Body, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path
from typing import Dict, List, Optional, Any
from itertools import combinations
from datetime import datetime
import hashlib
import json

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
VERSION_LABELS = {
    "1": "المجموعات",
    "2": "المجموعات 2",
}

DATA_FILE = Path("warzone_data.json")

# =========================
# Push Notifications
# =========================
VAPID_PUBLIC_KEY = "BNzit0AtKjV98NKB0QTVt8wpzvpEmxpmCq6PGIbxafoJUwjy7oODmFKoMSjNykAu6vp2ZHXhD4xeLunAD5AkIdo"
VAPID_PRIVATE_KEY = "EovBlK04jq_suYT2t2ULH-gmM_d6smFSoTihYi9roPs"
VAPID_CLAIMS = {"sub": "mailto:admin@warzone.com"}
subscribers = set()


class NotificationPayload(BaseModel):
    title: str
    body: str


class GroupPayload(BaseModel):
    sport: str
    version: str
    group: str
    teams: List[str]


class ResultPayload(BaseModel):
    match_id: str
    score1: int
    score2: int
    notify: bool = False


# =========================
# Data helpers
# =========================
def blank_data() -> Dict[str, Any]:
    return {
        "groups": {sport: {"1": {}, "2": {}} for sport in SPORTS},
        "results": {},
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
    for sport in SPORTS:
        data["groups"].setdefault(sport, {})
        data["groups"][sport].setdefault("1", {})
        data["groups"][sport].setdefault("2", {})
    return data


def save_data(data: Dict[str, Any]) -> None:
    with DATA_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_sport_and_version(sport_name: str) -> tuple[str, str]:
    """Supports /standings/Football and /standings/Football2."""
    sport_name = sport_name.strip()
    if sport_name.endswith("2"):
        maybe_sport = sport_name[:-1]
        if maybe_sport in SPORTS:
            return maybe_sport, "2"
    if sport_name in SPORTS:
        return sport_name, "1"
    raise HTTPException(status_code=404, detail="Sport not found")


def clean_team_name(name: str) -> str:
    return str(name).strip()


def make_match_id(sport: str, version: str, group: str, team1: str, team2: str) -> str:
    raw = f"{sport}|{version}|{group}|{team1}|{team2}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def generate_matches(
    data: Dict[str, Any],
    sport_filter: Optional[str] = None,
    version_filter: Optional[str] = None,
    group_filter: Optional[str] = None,
    only_unplayed: bool = False,
) -> List[Dict[str, Any]]:
    results = data.get("results", {})
    matches: List[Dict[str, Any]] = []

    for sport in SPORTS:
        if sport_filter and sport != sport_filter:
            continue
        for version in ["1", "2"]:
            if version_filter and version != str(version_filter):
                continue
            groups = data["groups"].get(sport, {}).get(version, {})
            for group_name in sorted(groups.keys()):
                if group_filter and group_name != group_filter:
                    continue
                teams = [clean_team_name(t) for t in groups[group_name] if clean_team_name(t)]
                for team1, team2 in combinations(teams, 2):
                    match_id = make_match_id(sport, version, group_name, team1, team2)
                    result = results.get(match_id)
                    played = result is not None
                    if only_unplayed and played:
                        continue
                    match = {
                        "id": match_id,
                        "sport": sport,
                        "sport_label": SPORT_LABELS.get(sport, sport),
                        "version": version,
                        "version_label": VERSION_LABELS.get(version, version),
                        "group": group_name,
                        "team1": team1,
                        "team2": team2,
                        "played": played,
                        "score1": None,
                        "score2": None,
                        "status": "تم اللعب" if played else "لم تُلعب",
                    }
                    if result:
                        match["score1"] = result.get("score1")
                        match["score2"] = result.get("score2")
                        match["played_at"] = result.get("played_at")
                    matches.append(match)
    return matches


def cleanup_orphan_results(data: Dict[str, Any]) -> None:
    valid_ids = {m["id"] for m in generate_matches(data)}
    data["results"] = {
        match_id: result
        for match_id, result in data.get("results", {}).items()
        if match_id in valid_ids
    }


def calculate_standings(data: Dict[str, Any], sport: str, version: str) -> Dict[str, List[Dict[str, Any]]]:
    groups = data["groups"].get(sport, {}).get(version, {})
    all_matches = generate_matches(data, sport_filter=sport, version_filter=version)
    result_by_id = data.get("results", {})
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

        for match in all_matches:
            if match["group"] != group_name:
                continue
            result = result_by_id.get(match["id"])
            if not result:
                continue

            t1, t2 = match["team1"], match["team2"]
            if t1 not in table or t2 not in table:
                continue
            s1, s2 = int(result["score1"]), int(result["score2"])

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
            if ex.response and ex.response.status_code in [404, 410]:
                inactive_subs.append(sub_str)
        except Exception as ex:
            print(f"Push error: {ex}")

    for sub in inactive_subs:
        subscribers.discard(sub)

    return len(subscribers)


# =========================
# Static pages
# =========================
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
# Notification routes
# =========================
@app.post("/subscribe")
async def subscribe(subscription: dict = Body(...)):
    subscribers.add(json.dumps(subscription, sort_keys=True))
    return {"status": "success", "total": len(subscribers)}


@app.post("/send-notification")
async def send_notification(payload: NotificationPayload):
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
    data = load_data()
    version = "1" if day_name == "Day1" else "2"
    return generate_matches(data, version_filter=version)


@app.get("/finals/{sport_name}")
def get_finals(sport_name: str, version: str = "1"):
    data = load_data()
    sport, default_version = normalize_sport_and_version(sport_name)
    version = version if version in ["1", "2"] else default_version
    standings = calculate_standings(data, sport, version)
    group_names = sorted(standings.keys())

    if len(group_names) < 2:
        return {
            "sport": sport,
            "version": version,
            "semi1": {"team1": "أول مجموعة A", "team2": "ثاني مجموعة B", "score1": "-", "score2": "-"},
            "semi2": {"team1": "أول مجموعة B", "team2": "ثاني مجموعة A", "score1": "-", "score2": "-"},
            "final": {"team1": "الفائز 1", "team2": "الفائز 2", "score1": "-", "score2": "-"},
        }

    g1, g2 = group_names[0], group_names[1]
    group1 = standings[g1]
    group2 = standings[g2]

    def name(rows, index, placeholder):
        return rows[index]["الفريق"] if len(rows) > index else placeholder

    return {
        "sport": sport,
        "version": version,
        "semi1": {"team1": name(group1, 0, f"{g1}1"), "team2": name(group2, 1, f"{g2}2"), "score1": "-", "score2": "-"},
        "semi2": {"team1": name(group2, 0, f"{g2}1"), "team2": name(group1, 1, f"{g1}2"), "score1": "-", "score2": "-"},
        "final": {"team1": "الفائز 1", "team2": "الفائز 2", "score1": "-", "score2": "-"},
    }


# =========================
# Admin data routes
# =========================
@app.get("/admin-data")
def get_admin_data():
    data = load_data()
    return {
        "sports": SPORTS,
        "sport_labels": SPORT_LABELS,
        "version_labels": VERSION_LABELS,
        "groups": data["groups"],
        "matches": generate_matches(data),
        "results": data.get("results", {}),
    }


@app.post("/admin/group")
def save_group(payload: GroupPayload):
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
        if clean and clean not in seen:
            teams.append(clean)
            seen.add(clean)

    if len(teams) < 2:
        raise HTTPException(status_code=400, detail="لازم تضيف فريقين على الأقل")

    data = load_data()
    data["groups"][sport][version][group] = teams
    cleanup_orphan_results(data)
    save_data(data)

    generated_count = len(list(combinations(teams, 2)))
    return {
        "status": "success",
        "message": f"تم حفظ المجموعة وتوليد {generated_count} ماتش تلقائيًا",
        "generated_matches": generated_count,
    }


@app.delete("/admin/group")
def delete_group(
    sport: str = Query(...),
    version: str = Query(...),
    group: str = Query(...),
):
    if sport not in SPORTS or version not in ["1", "2"]:
        raise HTTPException(status_code=400, detail="بيانات غير صحيحة")

    data = load_data()
    groups = data["groups"].get(sport, {}).get(version, {})
    if group not in groups:
        raise HTTPException(status_code=404, detail="المجموعة غير موجودة")

    del groups[group]
    cleanup_orphan_results(data)
    save_data(data)
    return {"status": "success", "message": "تم حذف المجموعة ونتائجها"}


@app.get("/admin/matches")
def admin_matches(
    sport: Optional[str] = Query(None),
    version: Optional[str] = Query(None),
    group: Optional[str] = Query(None),
    only_unplayed: bool = Query(False),
):
    data = load_data()
    if sport == "":
        sport = None
    if version == "":
        version = None
    if group == "":
        group = None
    return generate_matches(data, sport_filter=sport, version_filter=version, group_filter=group, only_unplayed=only_unplayed)


@app.post("/admin/result")
def save_result(payload: ResultPayload):
    if payload.score1 < 0 or payload.score2 < 0:
        raise HTTPException(status_code=400, detail="النتيجة لا يمكن تكون بالسالب")

    data = load_data()
    all_matches = generate_matches(data)
    match = next((m for m in all_matches if m["id"] == payload.match_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="الماتش غير موجود أو المجموعة اتغيرت")

    data.setdefault("results", {})
    data["results"][payload.match_id] = {
        "sport": match["sport"],
        "version": match["version"],
        "group": match["group"],
        "team1": match["team1"],
        "team2": match["team2"],
        "score1": payload.score1,
        "score2": payload.score2,
        "played_at": datetime.utcnow().isoformat() + "Z",
    }
    save_data(data)

    sent_to = 0
    if payload.notify:
        title = f"نتيجة {match['sport_label']} 🏆"
        body = f"{match['team1']} {payload.score1} - {payload.score2} {match['team2']} | المجموعة {match['group']}"
        sent_to = send_push_to_all(title, body)

    return {
        "status": "success",
        "message": "تم حفظ النتيجة وتحديث المجموعة",
        "match": {**match, "score1": payload.score1, "score2": payload.score2, "played": True},
        "sent_to": sent_to,
    }


@app.delete("/admin/result/{match_id}")
def delete_result(match_id: str):
    data = load_data()
    if match_id not in data.get("results", {}):
        raise HTTPException(status_code=404, detail="النتيجة غير موجودة")
    del data["results"][match_id]
    save_data(data)
    return {"status": "success", "message": "تم حذف النتيجة والماتش رجع للقائمة"}
