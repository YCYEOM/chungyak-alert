# 청약 알림봇

수도권(서울/경기/인천)에 새 청약 공고가 뜨면 텔레그램으로 알려주는 파이썬 자동화 봇.
APT 일반분양 + 무순위/잔여세대 + 오피스텔/도시형 전부 감시한다.

## 1. API 키 발급 (무료)

1. [data.go.kr](https://www.data.go.kr) 가입 후 로그인
2. **"한국부동산원_청약홈 분양정보 조회 서비스"** 검색 → **활용신청** (보통 즉시 승인)
3. 마이페이지에서 **일반 인증키 (Decoding)** 복사 ← Encoding 말고 Decoding!

## 2. 설치

```bash
cd ~/IdeaProjects/chungyak-alert
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

## 3. 설정

`run.sh`를 열어 세 값을 채운다:

- `SERVICE_KEY` — 위에서 발급받은 일반 인증키 (Decoding)
- `TELEGRAM_TOKEN` — @BotFather 봇 토큰 (아침브리핑 봇 재활용 가능)
- `TELEGRAM_CHAT_ID` — 내 채팅 ID
- `CF_ACCOUNT_ID` / `CF_D1_DATABASE_ID` / `CF_API_TOKEN` — 선택. 텔레그램 `/list`가
  data.go.kr을 직접 호출하지 않고 Cloudflare D1을 조회하게 하는 동기화용 (없으면
  D1 동기화만 조용히 건너뛰고 나머지 기능은 정상 동작). 토큰은
  dash.cloudflare.com → My Profile → API Tokens → Create Token → D1 Edit 권한으로 발급.

```bash
chmod +x run.sh
./run.sh   # 첫 실행: 현재 공고를 기억만 하고 "감시 시작" 요약 1건 전송
```

첫 실행은 flood 방지를 위해 알림을 쏘지 않는다. 이후 실행부터 새 공고만 푸시.

## 4. 자동 실행 A안 — GitHub Actions (권장: PC 꺼져 있어도 동작)

GitHub 서버에서 매일 KST 09:00/18:00 풀 실행 + 10:00~16:00 짝수 정시 라이트 4회 실행된다.

1. 저장소 Settings → Secrets and variables → Actions 에 등록:
   필수 `SERVICE_KEY`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` +
   선택(D1 동기화) `CF_ACCOUNT_ID`, `CF_D1_DATABASE_ID`, `CF_API_TOKEN`
2. 실행 시각은 GitHub schedule이 아니라 **Cloudflare Worker cron**이 결정한다
   (`worker/wrangler.toml`의 crons → `worker.js`의 `scheduled`가
   `repository_dispatch(run-alert)`를 쏘면 `.github/workflows/alert.yml`이 받아 실행).
   GitHub 자체 스케줄은 KST 아침에 드롭이 잦아 2026-07-16 폐기함.
3. Actions 탭에서 "Run workflow"로 수동 테스트 가능. `seen.json`은 실행 후 자동 커밋.

크론 시각을 바꾸려면: `worker/wrangler.toml` 수정 후 `cd worker && npx wrangler deploy`.
라이트 폴링 크론을 바꾸면 `worker.js`의 `LIGHT_CRON` 문자열도 같이 맞출 것.

## 4-B. 자동 실행 B안 — launchd (Mac이 켜져 있을 때)

```bash
cp com.chungyak.alert.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.chungyak.alert.plist
```

해제하려면:

```bash
launchctl unload ~/Library/LaunchAgents/com.chungyak.alert.plist
```

실행 로그는 `log.txt`, 이미 본 공고 목록은 `seen.json`에 쌓인다.
`seen.json`을 지우면 다음 실행이 다시 "첫 실행"으로 동작한다.

## 동작 방식

1. 청약홈 공공 API 3개 엔드포인트를 전부 수집 (최근 90일 공고)
2. 공급지역이 서울/경기/인천인 것만 필터
3. `seen.json`에 없는 새 공고만 텔레그램 푸시 (한 번에 15건 초과면 요약으로)
