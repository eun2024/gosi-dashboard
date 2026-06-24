#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kakao "나에게 메시지 보내기(talk_message)" 1회용 인증 도우미.

이 스크립트는 사용자의 PC에서 직접 실행해야 한다(Kakao 로그인 창이 뜨므로
GitHub Actions에서는 실행할 수 없다). 실행하면:

  1. 브라우저로 열 인가 URL을 출력한다 (직접 열어 카카오 로그인 후 동의).
  2. 로컬에 임시 웹서버(기본 포트 8888)를 띄워 redirect_uri로 돌아오는
     인가 코드(code)를 자동으로 받는다.
  3. 그 코드로 access_token / refresh_token을 발급받아 화면에 출력한다.
  4. data/gosi_state.json과 같은 위치(이 스크립트가 gosi-dashboard 저장소
     루트에 있다고 가정)에 data/kakao_meta.json을 써서 refresh_token 발급일을
     기록한다(토큰 값 자체는 기록하지 않음 — GitHub Secrets에만 저장).
  5. 테스트로 "나와의 채팅방"에 확인 메시지를 1통 보낸다.

사전 준비 (Kakao Developers, https://developers.kakao.com):
  - 애플리케이션 생성
  - [카카오 로그인] 활성화, Redirect URI에 http://localhost:8888/callback 등록
  - [카카오 로그인 > 동의항목]에서 "카카오톡 메시지 전송(talk_message)" 동의항목 활성화
    ※ "나에게 메시지 보내기"는 친구 발송이 아니므로 별도 권한 심사/비즈앱 전환이 필요 없다.
  - [앱 설정 > 플랫폼 > Web]에 사용할 웹 도메인(예: eun2024.github.io) 등록
    (카카오톡 메시지의 link.web_url/mobile_web_url에 이 도메인을 사용하기 위함)
  - [앱 키]에서 REST API 키를 확인

사용법:
  python kakao_bootstrap.py --rest-api-key <REST_API_KEY>
"""

import argparse
import json
import os
import sys
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import requests

KST = timezone(timedelta(hours=9))
AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"
SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
KAKAO_META_PATH = os.path.join(REPO_ROOT, "data", "kakao_meta.json")


def get_auth_code(rest_api_key, redirect_uri, port):
    auth_url = (
        f"{AUTH_URL}?client_id={rest_api_key}&redirect_uri={redirect_uri}"
        f"&response_type=code&scope=talk_message"
    )
    print("\n아래 URL을 브라우저에서 열어 카카오 로그인 후 동의해 주세요.")
    print("(자동으로 열리지 않으면 직접 복사해서 열어주세요)\n")
    print(auth_url)
    print()
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    code_holder = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = parse_qs(urlparse(self.path).query)
            if "code" in qs:
                code_holder["code"] = qs["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write("<h3>인증 완료. 이 창은 닫아도 됩니다.</h3>".encode("utf-8"))
            else:
                self.send_response(400)
                self.end_headers()

        def log_message(self, fmt, *args):
            pass

    server = HTTPServer(("localhost", port), Handler)
    print(f"localhost:{port} 에서 인가 코드를 기다리는 중...")
    while "code" not in code_holder:
        server.handle_request()
    server.server_close()
    return code_holder["code"]


def exchange_code(rest_api_key, redirect_uri, code):
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": rest_api_key,
            "redirect_uri": redirect_uri,
            "code": code,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def send_test_message(access_token):
    template = {
        "object_type": "text",
        "text": "[경남고시] Kakao 인증 테스트 메시지입니다. 이 메시지가 보이면 설정이 정상입니다.",
        "link": {
            "web_url": "https://eun2024.github.io/gosi-dashboard/",
            "mobile_web_url": "https://eun2024.github.io/gosi-dashboard/",
        },
        "button_title": "대시보드 보기",
    }
    resp = requests.post(
        SEND_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=15,
    )
    return resp


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rest-api-key", required=True, help="Kakao Developers REST API 키")
    parser.add_argument("--port", type=int, default=8888)
    args = parser.parse_args()

    redirect_uri = f"http://localhost:{args.port}/callback"

    code = get_auth_code(args.rest_api_key, redirect_uri, args.port)
    print("인가 코드 수신 완료. 토큰 교환 중...")
    token = exchange_code(args.rest_api_key, redirect_uri, code)

    print("\n========================================")
    print("아래 두 값을 GitHub 저장소 Secrets에 등록하세요.")
    print("(Settings → Secrets and variables → Actions → New repository secret)")
    print("========================================")
    print(f"KAKAO_REST_API_KEY = {args.rest_api_key}")
    print(f"KAKAO_REFRESH_TOKEN = {token['refresh_token']}")
    print("========================================\n")
    print(f"(참고) access_token 만료: {token.get('expires_in')}초, "
          f"refresh_token 만료: {token.get('refresh_token_expires_in')}초\n")

    today = datetime.now(KST).strftime("%Y-%m-%d")
    os.makedirs(os.path.dirname(KAKAO_META_PATH), exist_ok=True)
    with open(KAKAO_META_PATH, "w", encoding="utf-8") as f:
        json.dump({"refresh_token_issued_at": today}, f, ensure_ascii=False, indent=2)
    print(f"data/kakao_meta.json 에 발급일({today}) 기록 완료.")
    print("이 파일은 git add/commit/push 해서 저장소에 반영해야 만료 경고가 정확히 동작합니다.\n")

    try:
        resp = send_test_message(token["access_token"])
        if resp.status_code == 200:
            print("테스트 메시지 발송 성공 — 카카오톡 '나와의 채팅방'을 확인하세요.")
        else:
            print(f"테스트 메시지 발송 실패: {resp.status_code} {resp.text}")
            print("(link.web_url 도메인이 [카카오 로그인 > Web 플랫폼]에 등록되어 있는지 확인하세요)")
    except Exception as e:
        print(f"테스트 메시지 발송 중 오류: {e}")


if __name__ == "__main__":
    main()
