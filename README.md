# 고객사 지식관리 AI

급여/복리후생 담당자가 고객사별로 남기는 메모·정정이력·메시지를 AI가 구조화된
"예외 규칙"으로 계속 정리해두고, 이를 (1) 고객사 전환 시 짧은 브리핑, (2) 휴가·병가·
퇴사 등 인수인계가 필요할 때 전체 매뉴얼, 두 가지 형태로 즉시 꺼내 쓸 수 있게 하는
내부 도구입니다.

## 실행 방법

```bash
python -m venv venv
./venv/Scripts/activate   # Windows PowerShell: venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

브라우저에서 http://127.0.0.1:5000 접속 후, 데모 계정으로 로그인:

- 아이디: `demo`
- 비밀번호: `demo1234!`

첫 실행 시 예시 고객사 2곳(한빛전자, 그린푸드 물류센터)과 예시 기록·예외 규칙·
인수인계 매뉴얼 1건이 자동으로 채워집니다.

## AI 기능 켜기 (선택)

브리핑 자동 생성 / 새 기록 추가 시 예외 규칙 자동 갱신 / 인수인계 매뉴얼 생성 / 자연어
질의 검색 기능은 **Google Gemini 또는 Anthropic Claude** 중 등록된 쪽을 사용합니다.
둘 다 없어도 기록 작성/조회, 시드된 예시 데이터, Word 다운로드 등 나머지 기능은 모두
정상 동작합니다. 둘 다 등록되어 있으면 Gemini를 우선 사용합니다.

**방법 1 — 화면에서 등록 (추천, 터미널 불필요)**

관리자 계정(`demo`)으로 로그인 → 상단 **"⚙️ 설정"** → Gemini 또는 Claude API 키 입력 후 저장.
서버 재시작 없이 즉시 반영됩니다.

- Gemini 키 발급: https://aistudio.google.com/apikey
- Claude 키 발급: https://console.anthropic.com

**방법 2 — 환경변수**

```bash
export GEMINI_API_KEY=AIza...          # PowerShell: $env:GEMINI_API_KEY = "AIza..."
# 또는
export ANTHROPIC_API_KEY=sk-ant-...    # PowerShell: $env:ANTHROPIC_API_KEY = "sk-ant-..."
python app.py
```

화면에서 등록한 키가 있으면 그 키가 우선 적용되고, 없을 때만 환경변수를 봅니다.

## 구조

```
ETNS_CONTINUITY_APP/
├── app.py            # Flask 라우트
├── models.py          # User / Client / KnowledgeEntry / ExceptionRule / HandoverManual
├── ai.py               # Claude API 연동 (브리핑/규칙갱신/매뉴얼 생성)
├── docx_export.py     # 인수인계 매뉴얼 → Word(.docx) 변환
├── seed.py             # 최초 실행 시 데모 데이터 삽입
├── templates/
└── static/css/
```
