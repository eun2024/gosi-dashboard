#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
경남 고시공고 수집 — 한국 리전 Cloud Function (해외 IP 차단 우회용 수집 프록시)

배경: GitHub Actions 러너는 한국 밖(예: Azure 미국/유럽 리전)에서 실행되는데,
일부 경남 시군 사이트(진주시/합천군/남해군/산청군/함양군)는 해외 IP의 접속을
차단(connect timeout / DNS 실패)한다. 이 함수는 Google Cloud의 서울 리전
(asia-northeast3)에서 실행되어 한국 IP로 해당 사이트에 접속, 목록을 파싱해
JSON으로 돌려준다. 실제 키워드 필터링/중복 판단/대시보드 갱신/카카오 발송은
여전히 GitHub Actions 쪽 scraper.py가 담당한다 — 이 함수는 "수집"만 한다.

보안:
- PROXY_KEY 환경변수와 요청 헤더(X-Proxy-Key)가 일치해야만 응답한다.
  (누구나 호출 가능한 공개 프록시가 되지 않도록 하는 최소한의 보호장치)
- 이 함수는 git/GitHub 자격증명을 전혀 다루지 않는다(필요 없음).
"""

import os
import re

import functions_framework
import requests
from bs4 import BeautifulSoup
from flask import jsonify

UA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

PROXY_KEY = os.environ.get("PROXY_KEY", "")

SITES = {
    "진주시": {"url": "https://www.jinju.go.kr/05586.web", "format": "saeol"},
    "합천군": {"url": "https://www.hc.go.kr/04923/04924/04948.web", "format": "saeol"},
    "남해군": {"url": "https://www.namhae.go.kr/modules/saeol/gosi.do?pageCd=SM010110000&siteGubun=socialm", "format": "saeol_namhae"},
    "산청군": {"url": "https://www.sancheong.go.kr/www/selectBbsNttList.do?bbsNo=118&key=158", "format": "egovframe"},
    "함양군": {"url": "https://www.hygn.go.kr/00429/00543/00549.web", "format": "hygn_post"},
}


def _norm_date(raw):
    if not raw:
        return ""
    raw = raw.strip().rstrip(".")
    m = re.search(r"(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})", raw)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    return raw


def parse_saeol_generic(html_text, base_url):
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
    return parse_saeol_generic(html_text, base_url)


def parse_egovframe(html_text, base_url):
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


HYGN_ACTION_URL = "https://eminwon.hygn.go.kr/emwp/gov/mogaha/ntis/web/ofr/action/OfrAction.do"
HYGN_JSP_URL = "https://eminwon.hygn.go.kr/emwp/jsp/ofr/OfrNotAncmtLSub.jsp?not_ancmt_se_code=01,02,03,04,07"


def fetch_hygn_post():
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
    r = requests.post(HYGN_ACTION_URL, data=data, headers=headers, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for tr in soup.find_all("tr"):
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
        items.append({
            "id": gosi_id, "title": title, "date": date, "dept": dept, "period": "",
            "url": "https://www.hygn.go.kr/00429/00543/00549.web",
        })
    return items


PARSERS = {
    "saeol": parse_saeol_generic,
    "saeol_namhae": parse_saeol_namhae,
    "egovframe": parse_egovframe,
}


def fetch_site(name, conf):
    fmt = conf["format"]
    if fmt == "hygn_post":
        return fetch_hygn_post()
    r = requests.get(conf["url"], headers=UA_HEADERS, timeout=20)
    r.raise_for_status()
    return PARSERS[fmt](r.text, conf["url"])


@functions_framework.http
def fetch(request):
    """POST {"site": "진주시"} (헤더 X-Proxy-Key 필요) -> {"items": [...]}
    site 생략 시 5개 사이트 전체를 한 번에 수집해 {"results": {site: {...}}} 형태로 반환."""
    if not PROXY_KEY or request.headers.get("X-Proxy-Key") != PROXY_KEY:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    site = body.get("site")

    if site:
        if site not in SITES:
            return jsonify({"error": f"unknown site: {site}"}), 400
        try:
            items = fetch_site(site, SITES[site])
            return jsonify({"site": site, "items": items})
        except Exception as e:
            return jsonify({"site": site, "error": str(e)}), 200

    results = {}
    for name, conf in SITES.items():
        try:
            results[name] = {"items": fetch_site(name, conf)}
        except Exception as e:
            results[name] = {"error": str(e)}
    return jsonify({"results": results})
