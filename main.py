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

DEFAULT_MATCHES_URLS = {
    "Day1": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTPGvQX6sgITTWxbUXDqQzLSmQqU6TBxmZJDt0DS9pKOMNnoK7490Bn1TvNQrFlGdJZIH0Z9YPGTYb6/pub?gid=186915705&single=true&output=csv",
    "Day2": "https://docs.google.com/spreadsheets/d/e/2PACX-1vRqzlySvoK19S0Maw_xLSlUMmGcOPx6eNqiwKJKCtrHwkDxKuO95ZJKbvyNcXns8TxRe1oYnhZRtlNs/pub?gid=1547895490&single=true&output=csv",
}

# الجداول القديمة بتاعة المجموعات/الترتيب من Google Sheets.
# دي هتكون المصدر الأساسي للمجموعات، والجروبات اليدوية هتفضل fallback لو الشيت فاضي/مش متاح.
DEFAULT_SHEET_URLS = {
    "Football": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTPGvQX6sgITTWxbUXDqQzLSmQqU6TBxmZJDt0DS9pKOMNnoK7490Bn1TvNQrFlGdJZIH0Z9YPGTYb6/pub?gid=621025358&single=true&output=csv",
    "Dodgeball": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTPGvQX6sgITTWxbUXDqQzLSmQqU6TBxmZJDt0DS9pKOMNnoK7490Bn1TvNQrFlGdJZIH0Z9YPGTYb6/pub?gid=863642824&single=true&output=csv",
    "Volleyball": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTPGvQX6sgITTWxbUXDqQzLSmQqU6TBxmZJDt0DS9pKOMNnoK7490Bn1TvNQrFlGdJZIH0Z9YPGTYb6/pub?gid=1033302345&single=true&output=csv",
    "Ultimate Ball": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTPGvQX6sgITTWxbUXDqQzLSmQqU6TBxmZJDt0DS9pKOMNnoK7490Bn1TvNQrFlGdJZIH0Z9YPGTYb6/pub?gid=2017169226&single=true&output=csv",
    "Football2": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTPGvQX6sgITTWxbUXDqQzLSmQqU6TBxmZJDt0DS9pKOMNnoK7490Bn1TvNQrFlGdJZIH0Z9YPGTYb6/pub?gid=907297379&single=true&output=csv",
    "Dodgeball2": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTPGvQX6sgITTWxbUXDqQzLSmQqU6TBxmZJDt0DS9pKOMNnoK7490Bn1TvNQrFlGdJZIH0Z9YPGTYb6/pub?gid=402610111&single=true&output=csv",
    "Volleyball2": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTPGvQX6sgITTWxbUXDqQzLSmQqU6TBxmZJDt0DS9pKOMNnoK7490Bn1TvNQrFlGdJZIH0Z9YPGTYb6/pub?gid=42182221&single=true&output=csv",
    "Ultimate Ball2": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTPGvQX6sgITTWxbUXDqQzLSmQqU6TBxmZJDt0DS9pKOMNnoK7490Bn1TvNQrFlGdJZIH0Z9YPGTYb6/pub?gid=1116838793&single=true&output=csv"
}

# Runtime keys remain the same, but URLs can be changed from /sheets and stored in warzone_data.json.
MATCHES_URLS = DEFAULT_MATCHES_URLS
SHEET_URLS = DEFAULT_SHEET_URLS

all_matches_data: Dict[str, List[Dict[str, Any]]] = {k: [] for k in DEFAULT_MATCHES_URLS}
all_standings_sheet_data: Dict[str, List[Dict[str, Any]]] = {k: [] for k in DEFAULT_SHEET_URLS}

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


class TeamNameOverridePayload(BaseModel):
    old_name: str
    new_name: str


class GroupOverridePayload(BaseModel):
    sport: str
    version: str
    action: str  # add_team / hide_team / move_team / hide_group
    group: str = ""
    team: str = ""
    from_group: str = ""
    to_group: str = ""


class SheetLinksPayload(BaseModel):
    standings: Dict[str, str] = {}
    matches: Dict[str, str] = {}



# =========================
# Data helpers
# =========================
def default_sheet_links() -> Dict[str, Dict[str, str]]:
    return {
        "standings": DEFAULT_SHEET_URLS.copy(),
        "matches": DEFAULT_MATCHES_URLS.copy(),
    }


def ensure_sheet_links(data: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    links = data.setdefault("sheet_links", default_sheet_links())
    links.setdefault("standings", {})
    links.setdefault("matches", {})
    for key, url in DEFAULT_SHEET_URLS.items():
        links["standings"].setdefault(key, url)
    for key, url in DEFAULT_MATCHES_URLS.items():
        links["matches"].setdefault(key, url)
    # Keep only known keys so accidental inputs do not break the app.
    links["standings"] = {k: str(links["standings"].get(k, DEFAULT_SHEET_URLS[k])).strip() or DEFAULT_SHEET_URLS[k] for k in DEFAULT_SHEET_URLS}
    links["matches"] = {k: str(links["matches"].get(k, DEFAULT_MATCHES_URLS[k])).strip() or DEFAULT_MATCHES_URLS[k] for k in DEFAULT_MATCHES_URLS}
    data["sheet_links"] = links
    return links


def get_current_sheet_links() -> Dict[str, Dict[str, str]]:
    return ensure_sheet_links(load_data())


def get_current_standings_urls() -> Dict[str, str]:
    return get_current_sheet_links()["standings"]


def get_current_matches_urls() -> Dict[str, str]:
    return get_current_sheet_links()["matches"]


def validate_sheet_url(url: str) -> str:
    clean = str(url or "").strip()
    if not clean:
        raise HTTPException(status_code=400, detail="لينك الشيت لا يمكن يكون فاضي")
    if not clean.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="لينك الشيت لازم يبدأ بـ http أو https")
    return clean


def default_finals_for_sport(sport: str) -> Dict[str, Dict[str, str]]:
    return {
        "sport": sport,
        "semi1": {"team1": "X1", "team2": "Y2", "score1": "-", "score2": "-"},
        "semi2": {"team1": "X2", "team2": "Y1", "score1": "-", "score2": "-"},
        "final": {"team1": "الفائز 1", "team2": "الفائز 2", "score1": "-", "score2": "-"},
    }


def default_group_overrides() -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    return {sport: {"1": [], "2": []} for sport in SPORTS}


def blank_data() -> Dict[str, Any]:
    return {
        "groups": {sport: {"1": {}, "2": {}} for sport in SPORTS},
        "results": {},
        "finals": {sport: default_finals_for_sport(sport) for sport in SPORTS},
        "visibility": DEFAULT_VISIBILITY.copy(),
        "team_name_overrides": {},
        "group_overrides": default_group_overrides(),
        "sheet_links": default_sheet_links(),
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
    data.setdefault("team_name_overrides", {})
    data.setdefault("group_overrides", {})
    ensure_sheet_links(data)
    for sport in SPORTS:
        data["groups"].setdefault(sport, {})
        data["groups"][sport].setdefault("1", {})
        data["groups"][sport].setdefault("2", {})
        data["finals"].setdefault(sport, default_finals_for_sport(sport))
        data["group_overrides"].setdefault(sport, {})
        data["group_overrides"][sport].setdefault("1", [])
        data["group_overrides"][sport].setdefault("2", [])
    for key, default_value in DEFAULT_VISIBILITY.items():
        data["visibility"].setdefault(key, default_value)
    return data


def save_data(data: Dict[str, Any]) -> None:
    with DATA_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_text(value: Any) -> str:
    text = " ".join(str(value or "").strip().split()).casefold()
    trans = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    return text.translate(trans)


def clean_team_name(name: Any) -> str:
    return str(name or "").strip()


def team_key_variants(name: Any) -> List[str]:
    base = normalize_text(name)
    variants = {base}
    for prefix in ["فريق ", "team ", "team-", "team_", "#"]:
        if base.startswith(prefix):
            variants.add(base[len(prefix):].strip())
    if base:
        variants.add(f"فريق {base}")
        variants.add(f"team {base}")
    return [v for v in variants if v]


def get_team_overrides(data: Dict[str, Any]) -> Dict[str, str]:
    raw = data.get("team_name_overrides", {}) or {}
    if isinstance(raw, list):
        raw = {str(x.get("old_name", "")): str(x.get("new_name", "")) for x in raw if isinstance(x, dict)}
    return {clean_team_name(k): clean_team_name(v) for k, v in raw.items() if clean_team_name(k) and clean_team_name(v)}


def apply_team_override(data: Dict[str, Any], name: Any) -> str:
    clean = clean_team_name(name)
    overrides = get_team_overrides(data)
    lookup: Dict[str, str] = {}
    for old_name, new_name in overrides.items():
        for key in team_key_variants(old_name):
            lookup[key] = new_name
    for key in team_key_variants(clean):
        if key in lookup:
            return lookup[key]
    return clean


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


def fetch_matches_once(day_name: str, url: Optional[str] = None) -> List[Dict[str, Any]]:
    urls = get_current_matches_urls()
    if day_name not in urls:
        raise HTTPException(status_code=404, detail="اليوم غير موجود")
    response = requests.get(url or urls[day_name], timeout=20)
    response.raise_for_status()
    response.encoding = "utf-8"
    df = pd.read_csv(StringIO(response.text))
    df.columns = df.columns.str.strip()
    df = df.fillna("")
    rows = df.to_dict(orient="records")
    all_matches_data[day_name] = rows
    return rows


def get_schedule_rows(day_name: str) -> List[Dict[str, Any]]:
    if day_name not in DEFAULT_MATCHES_URLS:
        raise HTTPException(status_code=404, detail="اليوم غير موجود")
    if not all_matches_data.get(day_name):
        try:
            return fetch_matches_once(day_name)
        except Exception as e:
            print(f"❌ Error loading matches {day_name}: {e}")
            return []
    return all_matches_data.get(day_name, [])


def get_team_name_from_row(row: Dict[str, Any]) -> str:
    for key, value in row.items():
        key_norm = normalize_text(key)
        if key_norm in {"الفريق", "اسم الفريق", "team", "team name"} and clean_team_name(value):
            return clean_team_name(value)
    values = list(row.values())
    if len(values) > 9 and clean_team_name(values[9]):
        return clean_team_name(values[9])
    ignored = {"المجموعة", "group", "لعب", "فوز", "تعادل", "خسارة", "نقاط", "النقاط", "له", "عليه", "فرق"}
    for key, value in row.items():
        if normalize_text(key) in ignored:
            continue
        val = clean_team_name(value)
        if val and val != "-":
            return val
    return ""


def fetch_standings_once(sheet_key: str, url: Optional[str] = None) -> List[Dict[str, Any]]:
    urls = get_current_standings_urls()
    if sheet_key not in urls:
        raise HTTPException(status_code=404, detail="جدول المجموعات غير موجود")
    response = requests.get(url or urls[sheet_key], timeout=20)
    response.raise_for_status()
    response.encoding = "utf-8"
    df = pd.read_csv(StringIO(response.text))
    df.columns = df.columns.str.strip()
    df = df.fillna("")
    rows = df.to_dict(orient="records")
    all_standings_sheet_data[sheet_key] = rows
    return rows


def get_sheet_rows_for_groups(sport: str, version: str) -> List[Dict[str, Any]]:
    sheet_key = sport + ("2" if version == "2" else "")
    if sheet_key not in DEFAULT_SHEET_URLS:
        return []
    if not all_standings_sheet_data.get(sheet_key):
        try:
            return fetch_standings_once(sheet_key)
        except Exception as e:
            print(f"❌ Error loading standings sheet {sheet_key}: {e}")
            return []
    return all_standings_sheet_data.get(sheet_key, [])


def groups_from_sheet(data: Dict[str, Any], sport: str, version: str) -> Dict[str, List[str]]:
    rows = get_sheet_rows_for_groups(sport, version)
    groups: Dict[str, List[str]] = {}
    seen: Dict[str, set] = {}
    for row in rows:
        group_name = clean_team_name(row.get("المجموعة", row.get("Group", "A"))) or "A"
        team = apply_team_override(data, get_team_name_from_row(row))
        if not team:
            continue
        groups.setdefault(group_name, [])
        seen.setdefault(group_name, set())
        key = normalize_text(team)
        if key not in seen[group_name]:
            groups[group_name].append(team)
            seen[group_name].add(key)
    return groups


def get_manual_groups(data: Dict[str, Any], sport: str, version: str) -> Dict[str, List[str]]:
    groups = data.get("groups", {}).get(sport, {}).get(version, {}) or {}
    clean_groups: Dict[str, List[str]] = {}
    for group_name, teams in groups.items():
        cleaned = []
        seen = set()
        for team in teams or []:
            name = apply_team_override(data, team)
            key = normalize_text(name)
            if name and key not in seen:
                cleaned.append(name)
                seen.add(key)
        if cleaned:
            clean_groups[group_name] = cleaned
    return clean_groups


def get_group_overrides(data: Dict[str, Any], sport: str, version: str) -> List[Dict[str, Any]]:
    return data.setdefault("group_overrides", {}).setdefault(sport, {}).setdefault(version, [])


def make_group_override_id(record: Dict[str, Any]) -> str:
    # Deterministic id so the same override is not duplicated if you click twice.
    raw = "|".join(str(record.get(k, "")) for k in ["sport", "version", "action", "group", "team", "from_group", "to_group"])
    return hashlib.sha1(normalize_text(raw).encode("utf-8")).hexdigest()[:20]


def add_team_once(groups: Dict[str, List[str]], group_name: str, team: str) -> None:
    group_name = clean_team_name(group_name)
    team = clean_team_name(team)
    if not group_name or not team:
        return
    groups.setdefault(group_name, [])
    existing = {normalize_text(t) for t in groups[group_name]}
    if normalize_text(team) not in existing:
        groups[group_name].append(team)


def remove_team_from_groups(groups: Dict[str, List[str]], team: str, group_name: str = "") -> None:
    team_norm = normalize_text(team)
    group_norm = normalize_text(group_name)
    if not team_norm:
        return
    for g in list(groups.keys()):
        if group_norm and normalize_text(g) != group_norm:
            continue
        groups[g] = [t for t in groups[g] if normalize_text(t) != team_norm]


def remove_group(groups: Dict[str, List[str]], group_name: str) -> None:
    group_norm = normalize_text(group_name)
    for g in list(groups.keys()):
        if normalize_text(g) == group_norm:
            del groups[g]


def apply_group_overrides(data: Dict[str, Any], sport: str, version: str, base_groups: Dict[str, List[str]]) -> Dict[str, List[str]]:
    # Copy first so sheet data stays untouched.
    groups: Dict[str, List[str]] = {clean_team_name(g): list(teams or []) for g, teams in (base_groups or {}).items()}
    overrides = get_group_overrides(data, sport, version)

    # 1) Move teams. This removes the team from any group first to avoid duplicates.
    for rec in overrides:
        if rec.get("action") != "move_team":
            continue
        team = apply_team_override(data, rec.get("team", ""))
        to_group = clean_team_name(rec.get("to_group", ""))
        if team and to_group:
            remove_team_from_groups(groups, team)
            add_team_once(groups, to_group, team)

    # 2) Add admin-only teams on top of the sheet/manual data.
    for rec in overrides:
        if rec.get("action") != "add_team":
            continue
        add_team_once(groups, rec.get("group", ""), apply_team_override(data, rec.get("team", "")))

    # 3) Hide teams from a specific group.
    for rec in overrides:
        if rec.get("action") != "hide_team":
            continue
        remove_team_from_groups(groups, apply_team_override(data, rec.get("team", "")), rec.get("group", ""))

    # 4) Hide/delete full groups.
    for rec in overrides:
        if rec.get("action") != "hide_group":
            continue
        remove_group(groups, rec.get("group", ""))

    # Clean empty groups and duplicates.
    clean_groups: Dict[str, List[str]] = {}
    for group_name, teams in groups.items():
        cleaned = []
        seen = set()
        for team in teams:
            team = clean_team_name(team)
            key = normalize_text(team)
            if team and key not in seen:
                cleaned.append(team)
                seen.add(key)
        if cleaned:
            clean_groups[group_name] = cleaned
    return clean_groups


def get_effective_groups(data: Dict[str, Any], sport: str, version: str) -> Dict[str, List[str]]:
    sheet_groups = groups_from_sheet(data, sport, version)
    base_groups = sheet_groups if sheet_groups else get_manual_groups(data, sport, version)
    return apply_group_overrides(data, sport, version, base_groups)


def get_all_effective_groups(data: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, List[str]]]]:
    return {sport: {version: get_effective_groups(data, sport, version) for version in ["1", "2"]} for sport in SPORTS}


def build_groups_list(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for sport in SPORTS:
        for version in ["1", "2"]:
            sheet_groups = groups_from_sheet(data, sport, version)
            source = "sheet" if sheet_groups else "manual"
            groups = get_effective_groups(data, sport, version)
            has_overrides = bool(get_group_overrides(data, sport, version))
            if source == "sheet" and has_overrides:
                source_label = "من الشيت + تعديلات الأدمن"
            elif source == "sheet":
                source_label = "من الشيت"
            elif has_overrides:
                source_label = "يدوي/بديل + تعديلات الأدمن"
            else:
                source_label = "يدوي/بديل"
            for group_name, teams in groups.items():
                items.append({
                    "sport": sport,
                    "sport_label": SPORT_LABELS.get(sport, sport),
                    "version": version,
                    "version_label": VERSION_LABELS.get(version, version),
                    "group": group_name,
                    "teams": teams,
                    "source": source,
                    "source_label": source_label,
                })
    return items


def build_raw_team_name_options(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return original team names from sheets/match schedules before aliases are applied.
    Used by admin alias dropdown so the admin does not have to type the original sheet name manually.
    """
    items: Dict[str, Dict[str, Any]] = {}

    def add_name(name: Any, source: str) -> None:
        clean = clean_team_name(name)
        if not clean or clean == "-":
            return
        key = normalize_text(clean)
        if key not in items:
            items[key] = {"name": clean, "sources": []}
        if source and source not in items[key]["sources"]:
            items[key]["sources"].append(source)

    # Original names from standings/group sheets, before apply_team_override().
    for sport in SPORTS:
        for version in ["1", "2"]:
            for row in get_sheet_rows_for_groups(sport, version):
                group_name = clean_team_name(row.get("المجموعة", row.get("Group", "A"))) or "A"
                add_name(
                    get_team_name_from_row(row),
                    f"{SPORT_LABELS.get(sport, sport)} / {VERSION_LABELS.get(version, version)} / مجموعة {group_name}",
                )

    # Original names from match schedule sheets, before aliases are applied.
    for day_name in ["Day1", "Day2"]:
        for row in get_schedule_rows(day_name):
            for sport in SPORTS:
                column = get_schedule_sport_column(sport)
                raw_match_text = clean_team_name(row.get(column, ""))
                if not raw_match_text or raw_match_text == "-":
                    continue
                team1, team2 = parse_match_text(raw_match_text)
                source = f"{DAY_LABELS.get(day_name, day_name)} / {SPORT_LABELS.get(sport, sport)}"
                if team1 and team2:
                    add_name(team1, source)
                    add_name(team2, source)
                else:
                    # Fallback for unusual cells that contain a single placeholder/team value.
                    add_name(raw_match_text, source)

    # Include existing old names so saved overrides can still be edited/deleted even if the sheet changed.
    for old_name in (data.get("team_name_overrides", {}) or {}).keys():
        add_name(old_name, "استبدال محفوظ")

    out = []
    for item in items.values():
        sources = item.get("sources", [])
        out.append({
            "name": item["name"],
            "sources": sources,
            "label": item["name"] + (" — " + "، ".join(sources[:2]) if sources else ""),
        })
    out.sort(key=lambda x: normalize_text(x["name"]))
    return out


def build_group_overrides_list(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    labels = {
        "add_team": "إضافة فريق فوق الشيت",
        "hide_team": "إخفاء/حذف فريق من العرض",
        "move_team": "نقل فريق لمجموعة أخرى",
        "hide_group": "إخفاء/حذف مجموعة كاملة",
    }
    items: List[Dict[str, Any]] = []
    for sport in SPORTS:
        for version in ["1", "2"]:
            for rec in get_group_overrides(data, sport, version):
                item = dict(rec)
                item.setdefault("id", make_group_override_id(item))
                item["sport"] = sport
                item["version"] = version
                item["sport_label"] = SPORT_LABELS.get(sport, sport)
                item["version_label"] = VERSION_LABELS.get(version, version)
                item["action_label"] = labels.get(item.get("action", ""), item.get("action", ""))
                items.append(item)
    return items


def find_group_for_match(data: Dict[str, Any], sport: str, team1: str, team2: str) -> Dict[str, str]:
    team1 = apply_team_override(data, team1)
    team2 = apply_team_override(data, team2)
    t1 = normalize_text(team1)
    t2 = normalize_text(team2)
    if not t1 or not t2:
        return {"version": "", "group": ""}
    for version in ["1", "2"]:
        groups = get_effective_groups(data, sport, version)
        for group_name, teams in groups.items():
            team_norms = {normalize_text(t) for t in teams}
            if t1 in team_norms and t2 in team_norms:
                return {"version": version, "group": group_name}
    return {"version": "", "group": ""}


def apply_overrides_to_match_text(data: Dict[str, Any], match_text: Any) -> str:
    team1, team2 = parse_match_text(match_text)
    if team1 and team2:
        return f"{apply_team_override(data, team1)} ضد {apply_team_override(data, team2)}"
    return apply_team_override(data, match_text)


def apply_overrides_to_match_rows(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    processed = []
    sport_columns = ["Football", "Dodgeball", "Volleyball", "Ultimate Ball"]
    for row in rows:
        new_row = dict(row)
        for col in sport_columns:
            if col in new_row:
                val = clean_team_name(new_row.get(col, ""))
                if val and val != "-":
                    new_row[col] = apply_overrides_to_match_text(data, val)
        processed.append(new_row)
    return processed


def build_available_schedule_matches(day_name: str, sport: str) -> List[Dict[str, Any]]:
    if sport not in SPORTS:
        raise HTTPException(status_code=400, detail="اللعبة غير صحيحة")
    data = load_data()
    raw_rows = get_schedule_rows(day_name)
    column = get_schedule_sport_column(sport)
    available: List[Dict[str, Any]] = []

    for index, row in enumerate(raw_rows):
        raw_match_text = str(row.get(column, "") or "").strip()
        if not raw_match_text or raw_match_text == "-":
            continue
        match_time = str(row.get("التوقيت", row.get("time", row.get("Time", ""))) or "").strip()
        schedule_key = make_schedule_key(day_name, sport, index, match_time, raw_match_text)
        if schedule_key in data.get("results", {}):
            continue

        raw_team1, raw_team2 = parse_match_text(raw_match_text)
        team1 = apply_team_override(data, raw_team1)
        team2 = apply_team_override(data, raw_team2)
        match_text = apply_overrides_to_match_text(data, raw_match_text)
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
            "raw_match_text": raw_match_text,
            "team1": team1,
            "team2": team2,
            "version": group_info.get("version", ""),
            "group": group_info.get("group", ""),
            "can_parse": bool(team1 and team2),
            "label": " | ".join(label_parts),
        })
    return available


def canonical_team_name(data: Dict[str, Any], sport: str, version: str, group: str, input_name: str) -> str:
    teams = get_effective_groups(data, sport, version).get(group, [])
    wanted = normalize_text(apply_team_override(data, input_name))
    for team in teams:
        if normalize_text(team) == wanted:
            return clean_team_name(team)
    return apply_team_override(data, input_name)


def ensure_result_valid(data: Dict[str, Any], payload: ResultPayload) -> None:
    if payload.sport not in SPORTS:
        raise HTTPException(status_code=400, detail="اللعبة غير صحيحة")
    if payload.version not in ["1", "2"]:
        raise HTTPException(status_code=400, detail="اختار المجموعات أو المجموعات 2")
    if payload.day_name not in ["Day1", "Day2"]:
        raise HTTPException(status_code=400, detail="اليوم غير صحيح")
    team1 = apply_team_override(data, payload.team1)
    team2 = apply_team_override(data, payload.team2)
    if normalize_text(team1) == normalize_text(team2):
        raise HTTPException(status_code=400, detail="لا يمكن اختيار نفس الفريق مرتين")

    groups = get_effective_groups(data, payload.sport, payload.version)
    teams = groups.get(payload.group)
    if teams is None:
        raise HTTPException(status_code=404, detail="المجموعة غير موجودة. تأكد إن الفريقين موجودين في نفس مجموعة الشيت أو في مجموعة يدوية بديلة")

    team_norms = {normalize_text(t) for t in teams}
    if normalize_text(team1) not in team_norms or normalize_text(team2) not in team_norms:
        raise HTTPException(status_code=404, detail="الفريقين لازم يكونوا موجودين في نفس المجموعة المختارة بعد استبدال الأسماء")


def calculate_standings(data: Dict[str, Any], sport: str, version: str) -> Dict[str, List[Dict[str, Any]]]:
    groups = get_effective_groups(data, sport, version)
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
            t1 = canonical_team_name(data, sport, version, group_name, result.get("team1", ""))
            t2 = canonical_team_name(data, sport, version, group_name, result.get("team2", ""))
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
    rows = [dict(r) for r in data.get("results", {}).values() if r.get("day_name") == day_name]
    if sport:
        if sport not in SPORTS:
            raise HTTPException(status_code=400, detail="اللعبة غير صحيحة")
        rows = [r for r in rows if r.get("sport") == sport]
    for r in rows:
        r["team1"] = apply_team_override(data, r.get("team1", ""))
        r["team2"] = apply_team_override(data, r.get("team2", ""))
        r["match_text"] = apply_overrides_to_match_text(data, r.get("match_text", ""))
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


async def sync_google_sheets_loop():
    while True:
        standing_urls = get_current_standings_urls()
        match_urls = get_current_matches_urls()
        for key, url in standing_urls.items():
            try:
                rows = await asyncio.to_thread(fetch_standings_once, key, url)
                print(f"✅ Updated sheet groups/standings: {key} ({len(rows)} rows)")
            except Exception as e:
                print(f"❌ Error syncing standings {key}: {e}")
        for day, url in match_urls.items():
            try:
                rows = await asyncio.to_thread(fetch_matches_once, day, url)
                print(f"✅ Updated sheet matches: {day} ({len(rows)} rows)")
            except Exception as e:
                print(f"❌ Error syncing {day}: {e}")
        await asyncio.sleep(120)


# =========================
# Static pages
# =========================
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(sync_google_sheets_loop())


@app.get("/")
async def serve_home():
    return FileResponse("index.html")


@app.get("/admin")
async def serve_admin():
    return FileResponse("admin.html")


@app.get("/sheets")
async def serve_sheets():
    return FileResponse("sheets.html")


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
    data = load_data()
    return apply_overrides_to_match_rows(data, get_schedule_rows(day_name))


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
        "groups": get_all_effective_groups(data),
        "groups_list": build_groups_list(data),
        "manual_groups": data.get("groups", {}),
        "raw_team_name_options": build_raw_team_name_options(data),
        "team_name_overrides": get_team_overrides(data),
        "group_overrides": data.get("group_overrides", {}),
        "group_overrides_list": build_group_overrides_list(data),
        "results": get_day_results_list(data, "Day1") + get_day_results_list(data, "Day2"),
        "finals": data.get("finals", {}),
        "visibility": data.get("visibility", DEFAULT_VISIBILITY.copy()),
        "sheet_links": ensure_sheet_links(data),
    }


@app.get("/admin/sheet-links")
def get_sheet_links(request: Request):
    require_admin(request)
    data = load_data()
    links = ensure_sheet_links(data)
    return {
        "standings": links["standings"],
        "matches": links["matches"],
        "defaults": default_sheet_links(),
        "sports": SPORTS,
        "sport_labels": SPORT_LABELS,
        "version_labels": VERSION_LABELS,
        "day_labels": DAY_LABELS,
        "standings_labels": {
            key: f"{SPORT_LABELS.get(key[:-1] if key.endswith('2') else key, key)} / {VERSION_LABELS.get('2' if key.endswith('2') else '1')}"
            for key in DEFAULT_SHEET_URLS
        },
        "match_labels": {key: DAY_LABELS.get(key, key) for key in DEFAULT_MATCHES_URLS},
    }


@app.post("/admin/sheet-links")
def save_sheet_links(payload: SheetLinksPayload, request: Request):
    require_admin(request)
    data = load_data()
    links = ensure_sheet_links(data)
    changed_standings = []
    changed_matches = []

    for key in DEFAULT_SHEET_URLS:
        if key in payload.standings:
            new_url = validate_sheet_url(payload.standings[key])
            if links["standings"].get(key) != new_url:
                changed_standings.append(key)
            links["standings"][key] = new_url

    for key in DEFAULT_MATCHES_URLS:
        if key in payload.matches:
            new_url = validate_sheet_url(payload.matches[key])
            if links["matches"].get(key) != new_url:
                changed_matches.append(key)
            links["matches"][key] = new_url

    data["sheet_links"] = links
    save_data(data)

    # امسح الكاش عشان أول Refresh يسحب من الروابط الجديدة فورًا.
    for key in changed_standings:
        all_standings_sheet_data[key] = []
    for key in changed_matches:
        all_matches_data[key] = []

    return {
        "status": "success",
        "message": "تم حفظ لينكات الشيتات. اضغط تحديث بيانات الشيت الآن لو عايز تسحبها فورًا.",
        "changed_standings": changed_standings,
        "changed_matches": changed_matches,
        "sheet_links": links,
    }


@app.post("/admin/sheet-links/reset")
def reset_sheet_links(request: Request):
    require_admin(request)
    data = load_data()
    data["sheet_links"] = default_sheet_links()
    save_data(data)
    for key in DEFAULT_SHEET_URLS:
        all_standings_sheet_data[key] = []
    for key in DEFAULT_MATCHES_URLS:
        all_matches_data[key] = []
    return {"status": "success", "message": "تم إرجاع كل لينكات الشيتات للأصل", "sheet_links": data["sheet_links"]}


@app.post("/admin/test-sheet-link")
def test_sheet_link(payload: Dict[str, str], request: Request):
    require_admin(request)
    url = validate_sheet_url(payload.get("url", ""))
    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        response.encoding = "utf-8"
        df = pd.read_csv(StringIO(response.text))
        df.columns = df.columns.str.strip()
        return {"status": "success", "message": "اللينك شغال وتمت قراءة CSV", "rows": int(len(df)), "columns": list(df.columns)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"اللينك مش شغال أو مش CSV صحيح: {e}")


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


@app.post("/admin/group-override")
def save_group_override(payload: GroupOverridePayload, request: Request):
    require_admin(request)
    sport = clean_team_name(payload.sport)
    version = clean_team_name(payload.version)
    action = clean_team_name(payload.action)
    if sport not in SPORTS:
        raise HTTPException(status_code=400, detail="اللعبة غير صحيحة")
    if version not in ["1", "2"]:
        raise HTTPException(status_code=400, detail="التاب غير صحيح")
    if action not in ["add_team", "hide_team", "move_team", "hide_group"]:
        raise HTTPException(status_code=400, detail="نوع التعديل غير صحيح")

    record: Dict[str, Any] = {
        "sport": sport,
        "version": version,
        "action": action,
        "group": clean_team_name(payload.group),
        "team": clean_team_name(payload.team),
        "from_group": clean_team_name(payload.from_group),
        "to_group": clean_team_name(payload.to_group),
    }

    if action == "add_team" and (not record["group"] or not record["team"]):
        raise HTTPException(status_code=400, detail="اختار المجموعة واكتب اسم الفريق الجديد")
    if action == "hide_team" and (not record["group"] or not record["team"]):
        raise HTTPException(status_code=400, detail="اختار المجموعة والفريق اللي هيتشال")
    if action == "move_team" and (not record["team"] or not record["to_group"]):
        raise HTTPException(status_code=400, detail="اختار الفريق والمجموعة الجديدة")
    if action == "hide_group" and not record["group"]:
        raise HTTPException(status_code=400, detail="اختار المجموعة اللي هتتشال")

    record["id"] = make_group_override_id(record)
    record["created_at"] = datetime.utcnow().isoformat() + "Z"
    data = load_data()
    overrides = get_group_overrides(data, sport, version)
    if not any(str(o.get("id")) == record["id"] for o in overrides):
        overrides.append(record)
    save_data(data)
    return {"status": "success", "message": "تم حفظ تعديل المجموعات فوق الشيت", "override": record, "groups": get_effective_groups(data, sport, version)}


@app.delete("/admin/group-override/{override_id}")
def delete_group_override(override_id: str, request: Request):
    require_admin(request)
    data = load_data()
    removed = False
    for sport in SPORTS:
        for version in ["1", "2"]:
            overrides = get_group_overrides(data, sport, version)
            before = len(overrides)
            data["group_overrides"][sport][version] = [o for o in overrides if str(o.get("id")) != override_id]
            if len(data["group_overrides"][sport][version]) != before:
                removed = True
    if not removed:
        raise HTTPException(status_code=404, detail="التعديل غير موجود")
    save_data(data)
    return {"status": "success", "message": "تم حذف التعديل"}


@app.delete("/admin/group-overrides")
def reset_group_overrides(request: Request, sport: Optional[str] = Query(None), version: Optional[str] = Query(None)):
    require_admin(request)
    data = load_data()
    if sport and sport not in SPORTS:
        raise HTTPException(status_code=400, detail="اللعبة غير صحيحة")
    if version and version not in ["1", "2"]:
        raise HTTPException(status_code=400, detail="التاب غير صحيح")

    sports_to_clear = [sport] if sport else SPORTS
    versions_to_clear = [version] if version else ["1", "2"]
    for s in sports_to_clear:
        for v in versions_to_clear:
            data.setdefault("group_overrides", {}).setdefault(s, {})[v] = []
    save_data(data)
    return {"status": "success", "message": "تم مسح تعديلات المجموعات والرجوع للشيت/البديل"}


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


@app.post("/admin/team-name-override")
def save_team_name_override(payload: TeamNameOverridePayload, request: Request):
    require_admin(request)
    old_name = clean_team_name(payload.old_name)
    new_name = clean_team_name(payload.new_name)
    if not old_name or not new_name:
        raise HTTPException(status_code=400, detail="اكتب الاسم القديم والاسم الجديد")
    data = load_data()
    data.setdefault("team_name_overrides", {})
    data["team_name_overrides"][old_name] = new_name
    save_data(data)
    return {"status": "success", "message": "تم حفظ استبدال الاسم. هيتطبق على المجموعات وجدول الماتشات والنتائج."}


@app.delete("/admin/team-name-override")
def delete_team_name_override(request: Request, old_name: str = Query(...)):
    require_admin(request)
    data = load_data()
    overrides = data.setdefault("team_name_overrides", {})
    found_key = None
    for key in list(overrides.keys()):
        if normalize_text(key) == normalize_text(old_name):
            found_key = key
            break
    if not found_key:
        raise HTTPException(status_code=404, detail="الاستبدال غير موجود")
    del overrides[found_key]
    save_data(data)
    return {"status": "success", "message": "تم حذف استبدال الاسم"}


@app.post("/admin/reload-sheets")
def reload_sheets(request: Request):
    require_admin(request)
    standings_count = 0
    matches_count = 0
    errors = []
    standing_urls = get_current_standings_urls()
    match_urls = get_current_matches_urls()
    for key, url in standing_urls.items():
        try:
            standings_count += len(fetch_standings_once(key, url))
        except Exception as e:
            errors.append(f"{key}: {e}")
    for day, url in match_urls.items():
        try:
            matches_count += len(fetch_matches_once(day, url))
        except Exception as e:
            errors.append(f"{day}: {e}")
    return {"status": "success", "message": "تم تحديث بيانات الشيت", "standings_rows": standings_count, "matches_rows": matches_count, "errors": errors}


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
