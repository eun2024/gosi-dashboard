import json
import requests
from bs4 import BeautifulSoup

# [중요] 여기에 공고를 가져올 사이트 URL을 적으세요!
url = "여기에_공고_사이트_주소를_넣으세요" 

# 로봇이 사이트에서 정보를 가져오는 코드입니다.
# (현재 사이트 주소를 알려주시면, 그 사이트 맞춤형 코드로 수정해 드릴게요.)
print("공고를 수집 중입니다...")

# 예시 데이터 저장
data = [{"title": "자동 수집 테스트", "date": "2026-06-24", "link": "https://google.com"}]
with open('data.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=4)
