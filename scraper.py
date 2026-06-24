#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
경남 8개 시군 고시공고 모니터링 — GitHub Actions용 메인 스크립트

기존 로컬 Cowork 스케줄 작업(gyeongnam-gosi-monitor SKILL.md)을 클라우드(GitHub Actions)로
이전한 버전. 8개 시군 게시판을 requests+BeautifulSoup으로 직접 스크래핑하고(브라우저/JS 불필요),
9개 키워드로 필터링한 뒤, 신규 매칭 항목만 카카오톡 "나에게 메모"로 발송하고 대시보드
(index.html)를 갱신한다. 중복 방지 상태(data/gosi_state.json)는 이 저장소 안에 커밋되어
영속된다(로컬 PC 상태 불필요).

이 스크립트는 파일을 로컬(체크아웃된 저장소)에 쓰기만 한다. git commit/push와 GitHub Secrets
주입은 .github/workflows/update.yml 이 담당한다.

v1 범위: 첨부파일 다운로드는 생략(원문 링크만 제공) — 사용자 결정.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------
# 설정
# --------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
TODAY_STR = NOW.strftime("%Y-%m-%d")
WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"][NOW.weekday()]
COLLECT_DATE = f"{NOW.year}년 {NOW.month}월 {NOW.day}일 ({WEEKDAY_KR})"

KEYWORDS = ["도시계획", "개발행위", "건축", "인허가", "환경", "산림", "조성", "사업실시", "개발사업"]

UA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# 일부 .go.kr 사이트는 인증서 체인에 Mozilla(certifi) 기본 신뢰목록에 없는 루트가 섞여 있어
# requests 기본 verify=True(certifi)로는 SSL 검증이 실패할 수 있다. SSL 검증 자체를 끄는
# 대신(verify=False는 보안상 피함), OS가 제공하는 더 넓은 CA 묶음이 있으면 그것을 사용한다.
_OS_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
CA_BUNDLE = _OS_CA_BUNDLE if os.path.exists(_OS_CA_BUNDLE) else True

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(REPO_ROOT, "data", "gosi_state.json")
KAKAO_META_PATH = os.path.join(REPO_ROOT, "data", "kakao_meta.json")
DASHBOARD_PATH = os.path.join(REPO_ROOT, "index.html")
DASHBOARD_URL = "https://eun2024.github.io/gosi-dashboard/"

# 대시보드 DATA.sigun 인덱스 매핑 (기존 대시보드와 동일하게 유지해야 함)
SIGUN_INDEX = {
    "진주시": 0, "사천시": 1, "산청군": 2, "함양군": 3,
    "거창군": 4, "합천군": 5, "남해군": 6, "하동군": 7,
}

SITES = {
    "진주시": {"url": "https://www.jinju.go.kr/05586.web", "format": "saeol"},
    "사천시": {"url": "https://www.sacheon.go.kr/news/00009/00014.web", "format": "saeol_excerpt"},
    "하동군": {"url": "https://www.hadong.go.kr/media/00012.web", "format": "saeol"},
    "합천군": {"url": "https://www.hc.go.kr/04923/04924/04948.web", "format": "saeol"},
    "거창군": {"url": "https://www.geochang.go.kr/00445/00451.web", "format": "saeol"},
    "남해군": {"url": "https://www.namhae.go.kr/modules/saeol/gosi.do?pageCd=SM010110000&siteGubun=socialm", "format": "saeol_namhae"},
    "산청군": {"url": "https://www.sancheong.go.kr/www/selectBbsNttList.do?bbsNo=118&key=158", "format": "egovframe"},
    "함양군": {"url": "https://www.hygn.go.kr/00429/00543/00549.web", "format": "hygn_post"},
}

KAKAO_TOKEN_LIFETIME_DAYS = 60
KAKAO_WARN_AFTER_DAYS = 50  # 60일 만료 전 50일째부터 경고


def log(msg):
    print(f"[{datetime.now(KST).strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------
# 사이트별 파서
# --------------------------------------------------------------------------

def _norm_date(raw):
    """다양한 날짜 표기를 YYYY-MM-DD로 정규화."""
    if not raw:
        return ""
    raw = raw.strip().rstrip(".")
    m = re.search(r"(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})", raw)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    return raw


def parse_saeol_generic(html_text, base_url):
    """진주시/하동군/합천군/거창군 공통 새올전자민원 목록 구조."""
    soup = BeautifulSoup(html_text, "html.parser")
    items = []
    for li in soup.select("ul.lst1 > li"):
        a = li.find("a", class_="a1")
        if not a:
            continue
        href = a.get("href", "")
        title_tag = a.find("strong", class_="t1")
        if not title_tag:
            continue
        title = title_tag.get_text(strip=True)
        gosi_id, date, dept, period = None, "", "", ""
        for sp in a.find_all("span", class_="t3"):
            t = sp.get_text(strip=True)
            if t.startswith("고시번호 :") or t.startswith("고시번호:"):
                gosi_id = t.split(":", 1)[1].strip()
            elif t.startswith("등록일 :") or t.startswith("등록일:"):
                date = _norm_date(t.split(":", 1)[1].strip())
            elif t.startswith("담당부서 :") or t.startswith("담당부서:"):
                dept = t.split(":", 1)[1].strip()
            elif t.startswith("공고기간 :") or t.startswith("공고기간:"):
                period = t.split(":", 1)[1].strip()
        if not gosi_id:
            continue
        url = requests.compat.urljoin(base_url, href)
        items.append({"id": gosi_id, "title": title, "date": date, "dept": dept, "period": period, "url": url})
    return items


def parse_saeol_namhae(html_text, base_url):
    """남해군 — saeol과 동일 구조지만 상세링크가 amode=_view(밑줄) 변형."""
    return parse_saeol_generic(html_text, base_url)


def parse_saeol_excerpt(html_text, base_url):
    """사천시 — 고시공고번호가 제목 앞에 [브래킷]으로 포함되는 요약형 변형."""
    soup = BeautifulSoup(html_text, "html.parser")
    items = []
    for li in soup.select("ul.lst1 > li"):
        a = li.find("a", class_="a1")
        if not a:
            continue
        href = a.get("href", "")
        strong = a.find("strong", class_="t1")
        if not strong:
            continue
        strong_copy = BeautifulSoup(str(strong), "html.parser").find("strong")
        for unwanted in strong_copy.find_all(["i", "span"]):
            unwanted.decompose()
        raw = strong_copy.get_text(" ", strip=True)
        m = re.match(r"^\[(.+?)\]\s*(.+)$", raw)
        if m:
            gosi_id, title = m.group(1).strip(), m.group(2).strip()
        else:
            gosi_id, title = None, raw
        if not gosi_id:
            continue
        spans = a.find_all("span", class_="t3")
        date = _norm_date(spans[0].get_text(strip=True)) if len(spans) > 0 else ""
        dept = spans[1].get_text(strip=True) if len(spans) > 1 else ""
        url = requests.compat.urljoin(base_url, href)
        items.append({"id": gosi_id, "title": title, "date": date, "dept": dept, "period": "", "url": url})
    return items


def parse_egovframe(html_text, base_url):
    """산청군 — eGovFrame 표준 게시판 표(table) 구조."""
    soup = BeautifulSoup(html_text, "html.parser")
    items = []
    table = soup.select_one("table.bbs_default_list")
    if not table:
        return items
    body = table.find("tbody") or table
    for tr in body.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        gosi_id = tds[1].get_text(strip=True)
        a = tds[2].find("a")
        if not a or not gosi_id:
            continue
        title = a.get_text(strip=True)
        dept = tds[3].get_text(strip=True)
        date = _norm_date(tds[4].get_text(strip=True))
        url = requests.compat.urljoin(base_url, a.get("href", ""))
        items.append({"id": gosi_id, "title": title, "date": date, "dept": dept, "period": "", "url": url})
    return items


HYGN_JSP_URL = "https://eminwon.hygn.go.kr/emwp/jsp/ofr/OfrNotAncmtLSub.jsp?not_ancmt_se_code=01,02,03,04,07"
HYGN_ACTION_URL = "https://eminwon.hygn.go.kr/emwp/gov/mogaha/ntis/web/ofr/action/OfrAction.do"


def fetch_hygn_post():
    """함양군 — 화면은 cross-origin iframe이지만 내부적으로 POST OfrAction.do 호출로 목록을 받아옴.
    브라우저/JS 없이 동일 POST를 직접 재현한다."""
    data = {
        "pageIndex": "1",
        "jndinm": "OfrNotAncmtEJB",
        "context": "NTIS",
        "method": "selectListOfrNotAncmt",
        "methodnm": "selectListOfrNotAncmtHomepage",
        "not_ancmt_mgt_no": "",
        "homepage_pbs_yn": "Y",
        "subCheck": "Y",
        "ofr_pageSize": "10",
        "not_ancmt_se_code": "01,02,03,04,07",
        "title": "고시공고",
        "cha_dep_code_nm": "",
        "initValue": "",
        "countYn": "Y",
        "list_gubun": "",
        "not_ancmt_sj": "",
    }
    headers = dict(UA_HEADERS)
    headers["Referer"] = HYGN_JSP_URL
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    r = requests.post(HYGN_ACTION_URL, data=data, headers=headers, timeout=20, verify=CA_BUNDLE)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    rows = soup.find_all("tr")
    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) != 6:
            continue
        gosi_id = tds[1].get_text(strip=True)
        a = tds[2].find("a")
        if not a or not gosi_id:
            continue
        title = a.get_text(strip=True)
        dept = tds[3].get_text(strip=True)
        date = _norm_date(tds[4].get_text(strip=True))
        # 상세 페이지는 POST 전용(SPA)이라 직접 링크 불가 — 목록 페이지로 연결
        items.append({
            "id": gosi_id, "title": title, "date": date, "dept": dept, "period": "",
            "url": "https://www.hygn.go.kr/00429/00543/00549.web",
        })
    return items


PARSERS = {
    "saeol": parse_saeol_generic,
    "saeol_namhae": parse_saeol_namhae,
    "saeol_excerpt": parse_saeol_excerpt,
    "egovframe": parse_egovframe,
}


# 해외 IP(GitHub Actions 러너) 접속이 차단되는 사이트 → 한국 리전(Cloud Function) 경유.
# 원인: connect timeout/DNS 실패가 5개 사이트에서만 재현 — 비한국 IP 차단으로 확인됨.
PROXY_SITES = {"진주시", "합천군", "남해군", "산청군", "함양군"}
PROXY_URL = os.environ.get("PROXY_URL", "")
PROXY_KEY = os.environ.get("PROXY_KEY", "")


def fetch_site_via_proxy(name):
    """한국 리전 Cloud Function을 통해 수집 (해외 IP 차단 우회).
    함수 쪽은 수집(파싱)만 하고, 중복판단/대시보드/카카오 발송은 그대로 이 스크립트가 담당."""
    resp = requests.post(
        PROXY_URL,
        json={"site": name},
        headers={"X-Proxy-Key": PROXY_KEY},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return data["items"]


def fetch_site(name, conf):
    if name in PROXY_SITES and PROXY_URL:
        return fetch_site_via_proxy(name)
    fmt = conf["format"]
    if fmt == "hygn_post":
        return fetch_hygn_post()
    r = requests.get(conf["url"], headers=UA_HEADERS, timeout=20, verify=CA_BUNDLE)
    r.raise_for_status()
    return PARSERS[fmt](r.text, conf["url"])


# --------------------------------------------------------------------------
# 상태 파일 (중복 방지)
# --------------------------------------------------------------------------

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_updated": "", "sites": {name: {"seen_ids": []} for name in SITES}}


def save_state(state):
    state["last_updated"] = TODAY_STR
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def update_seen_ids(state, site_name, all_ids):
    site_state = state["sites"].setdefault(site_name, {"seen_ids": []})
    seen = site_state.get("seen_ids", [])
    merged = list(dict.fromkeys(list(all_ids) + seen))  # 신규를 앞에, 중복 제거(순서 보존)
    site_state["seen_ids"] = merged[:200]


# --------------------------------------------------------------------------
# 키워드 필터링
# --------------------------------------------------------------------------

def match_keyword(title):
    for kw in KEYWORDS:
        if kw in title:
            return kw
    return None


# --------------------------------------------------------------------------
# 대시보드(index.html) 갱신 — "일일 수집 섹션" IIFE의 COLLECT_DATE/DATA만 교체
# --------------------------------------------------------------------------

DAILY_BLOCK_RE = re.compile(
    r"(/\* ================= 일일 수집 섹션 ================= \*/\s*\(function\(\)\{.*?"
    r"const COLLECT_DATE = )\"(?:[^\"\\]|\\.)*\"(;\s*const DATA = )\[.*?\](;)",
    re.DOTALL,
)


def js_escape(s):
    return (
        str(s)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", " ")
    )


def build_summary(kw, gosi_id, period):
    parts = [f"키워드 '{kw}' 매칭"]
    if gosi_id:
        parts.append(gosi_id)
    if period:
        parts.append(f"공고기간 {period}")
    return " · ".join(parts)


def update_dashboard(targets):
    if not os.path.exists(DASHBOARD_PATH):
        log("index.html이 없습니다 — 대시보드 갱신을 건너뜁니다.")
        return False
    with open(DASHBOARD_PATH, "r", encoding="utf-8") as f:
        html_text = f.read()

    data_js_items = []
    for t in targets:
        mmdd = t["date"][5:] if len(t["date"]) >= 10 else t["date"]
        item = (
            "{sigun:%d, title:\"%s\", date:\"%s\", summary:\"%s\", dept:\"%s\", url:\"%s\"}"
            % (
                SIGUN_INDEX[t["sigun_name"]],
                js_escape(t["title"]),
                js_escape(mmdd),
                js_escape(t["summary"]),
                js_escape(t["dept"]),
                js_escape(t["url"]),
            )
        )
        data_js_items.append(item)
    data_js = "[\n    " + ",\n    ".join(data_js_items) + "\n  ]" if data_js_items else "[]"

    def _repl(m):
        return f'{m.group(1)}"{js_escape(COLLECT_DATE)}"{m.group(2)}{data_js}{m.group(3)}'

    new_html, n = DAILY_BLOCK_RE.subn(_repl, html_text, count=1)
    if n == 0:
        log("경고: 대시보드의 '일일 수집 섹션' 블록을 찾지 못해 갱신하지 못했습니다.")
        return False

    with open(DASHBOARD_PATH, "w", encoding="utf-8") as f:
        f.write(new_html)
    log(f"대시보드 갱신 완료 (신규 표시 {len(targets)}건).")
    return True


# --------------------------------------------------------------------------
# 카카오톡 발송 ("나에게 메모" — 친구 발송이 아니므로 권한 심사/비즈앱 불필요)
# --------------------------------------------------------------------------

KAKAO_SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"


def kakao_refresh_access_token(rest_api_key, refresh_token):
    resp = requests.post(
        KAKAO_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": rest_api_key,
            "refresh_token": refresh_token,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()  # {"access_token": ..., "expires_in": ..., maybe "refresh_token": ...}


def kakao_send_text(access_token, text):
    template = {
        "object_type": "text",
        "text": text,
        "link": {"web_url": DASHBOARD_URL, "mobile_web_url": DASHBOARD_URL},
        "button_title": "대시보드 보기",
    }
    resp = requests.post(
        KAKAO_SEND_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=15,
    )
    return resp


def chunk_messages(prefix, lines, limit=190):
    """200자 제한에 맞춰 여러 메시지로 분할."""
    messages = []
    cur = prefix
    first = True
    for line in lines:
        sep = "" if first else " / "
        candidate = cur + sep + line
        if len(candidate) > limit and cur != prefix:
            messages.append(cur)
            cur = prefix + "(계속) " + line
            first = False
            continue
        cur = candidate
        first = False
    if cur.strip() != prefix.strip():
        messages.append(cur)
    return messages


def send_kakao_notifications(targets, warning_line=None):
    rest_api_key = os.environ.get("KAKAO_REST_API_KEY", "")
    refresh_token = os.environ.get("KAKAO_REFRESH_TOKEN", "")
    if not rest_api_key or not refresh_token:
        log("KAKAO_REST_API_KEY/KAKAO_REFRESH_TOKEN이 설정되지 않아 카카오 발송을 건너뜁니다.")
        return

    if not targets and not warning_line:
        log("발송대상 0건 — 카카오 메시지를 보내지 않습니다.")
        return

    try:
        token_resp = kakao_refresh_access_token(rest_api_key, refresh_token)
    except Exception as e:
        log(f"카카오 액세스 토큰 갱신 실패: {e}")
        return
    access_token = token_resp["access_token"]

    mmdd = NOW.strftime("%m/%d")
    prefix = f"[경남고시 {mmdd}] "
    lines = [f"{t['sigun_name']}-{t['title'][:40]}" for t in targets]
    messages = chunk_messages(prefix, lines) if lines else []

    if warning_line:
        messages = [warning_line] + messages

    for i, msg in enumerate(messages):
        try:
            resp = kakao_send_text(access_token, msg[:200])
            if resp.status_code != 200:
                log(f"카카오 발송 실패 (msg {i+1}/{len(messages)}): {resp.status_code} {resp.text[:200]}")
            else:
                log(f"카카오 발송 성공 (msg {i+1}/{len(messages)}).")
        except Exception as e:
            log(f"카카오 발송 중 오류 (msg {i+1}/{len(messages)}): {e}")


def check_kakao_token_warning():
    """refresh_token 발급일 기준 만료 임박 여부 확인 (토큰 값은 절대 로그에 남기지 않음)."""
    if not os.path.exists(KAKAO_META_PATH):
        return None
    try:
        with open(KAKAO_META_PATH, "r", encoding="utf-8") as f:
            meta = json.load(f)
        issued_at = datetime.strptime(meta["refresh_token_issued_at"], "%Y-%m-%d").replace(tzinfo=KST)
    except Exception:
        return None
    days_elapsed = (NOW - issued_at).days
    remaining = KAKAO_TOKEN_LIFETIME_DAYS - days_elapsed
    if remaining <= (KAKAO_TOKEN_LIFETIME_DAYS - KAKAO_WARN_AFTER_DAYS):
        return remaining
    return None


def create_or_update_github_issue(remaining_days):
    gh_token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not gh_token or not repo:
        return
    title = "Kakao 재인증 필요 (refresh_token 만료 임박)"
    api = f"https://api.github.com/repos/{repo}"
    headers = {"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json"}
    try:
        r = requests.get(f"{api}/issues", headers=headers, params={"state": "open", "labels": "kakao-auth"}, timeout=15)
        existing = [i for i in r.json() if isinstance(i, dict) and i.get("title") == title]
        body = (
            f"카카오 refresh_token이 약 {remaining_days}일 후 만료됩니다.\n\n"
            "재인증 절차:\n"
            "1. 로컬에서 `kakao_bootstrap.py`를 다시 실행해 새 refresh_token을 발급받습니다.\n"
            "2. GitHub 저장소 Settings → Secrets and variables → Actions에서 `KAKAO_REFRESH_TOKEN`을 새 값으로 갱신합니다.\n"
            "3. `data/kakao_meta.json`의 `refresh_token_issued_at`을 오늘 날짜로 갱신해 커밋합니다.\n\n"
            f"(자동 생성됨, {TODAY_STR} 기준)"
        )
        if existing:
            issue_number = existing[0]["number"]
            requests.patch(f"{api}/issues/{issue_number}", headers=headers, json={"body": body}, timeout=15)
            log(f"기존 Kakao 재인증 안내 이슈 갱신 (#{issue_number}).")
        else:
            requests.post(
                f"{api}/issues", headers=headers,
                json={"title": title, "body": body, "labels": ["kakao-auth"]}, timeout=15,
            )
            log("Kakao 재인증 안내 이슈 생성.")
    except Exception as e:
        log(f"GitHub 이슈 생성/갱신 실패: {e}")


# --------------------------------------------------------------------------
# 메인
# --------------------------------------------------------------------------

def main():
    dry_run = "--dry-run" in sys.argv
    state = load_state()
    all_targets = []
    errors = []

    for site_name, conf in SITES.items():
        try:
            items = fetch_site(site_name, conf)
            log(f"{site_name}: {len(items)}건 수집")
        except Exception as e:
            log(f"{site_name}: 수집 실패 - {e}")
            errors.append(site_name)
            continue

        seen_ids = set(state["sites"].get(site_name, {}).get("seen_ids", []))
        new_items = [it for it in items if it["id"] not in seen_ids]
        if new_items:
            log(f"{site_name}: 신규 {len(new_items)}건")

        for it in new_items:
            kw = match_keyword(it["title"])
            if kw:
                all_targets.append({
                    "sigun_name": site_name,
                    "title": it["title"],
                    "date": it["date"] or TODAY_STR,
                    "dept": it["dept"],
                    "url": it["url"],
                    "summary": build_summary(kw, it["id"], it.get("period", "")),
                })

        if not dry_run:
            update_seen_ids(state, site_name, [it["id"] for it in items])

    log(f"총 발송대상(키워드 매칭 신규): {len(all_targets)}건")

    if not dry_run:
        save_state(state)
        update_dashboard(all_targets)

        remaining = check_kakao_token_warning()
        warning_line = None
        if remaining is not None:
            warning_line = f"⚠️ 카카오 인증 만료 {max(remaining,0)}일 전입니다. 재인증이 필요합니다."
            create_or_update_github_issue(remaining)

        send_kakao_notifications(all_targets, warning_line)
    else:
        log("(dry-run 모드: state/대시보드 저장, 카카오 발송을 수행하지 않음)")
        for t in all_targets:
            log(f"  - [{t['sigun_name']}] {t['title']} :: {t['summary']}")

    if errors:
        log(f"수집 실패 사이트: {', '.join(errors)} (전체 작업은 계속 진행됨)")

    log("완료.")


if __name__ == "__main__":
    main()
