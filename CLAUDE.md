# 청약 알림봇 (진행 중 — 인수인계 노트)

> 이 파일은 Claude Code가 자동으로 읽습니다. 터미널 CLI에서 `claude`를 이 폴더에서 실행하면
> 아래 맥락을 그대로 이어받아 작업을 계속할 수 있습니다.

## 목표
아파트/주택 **청약 공고가 새로 뜨면 텔레그램으로 알림**을 보내주는 파이썬 자동화 프로그램.
"계속 지켜보다가 새 거 뜨면 푸시" — 챗봇이 못 하는 무인 감시 자동화.

## 확정된 결정사항
- **감시 지역**: 수도권 — 서울, 경기, 인천 (그 외 지역은 무시)
- **청약 종류**: 전 종류 — APT 일반분양 + 무순위/잔여세대 + 오피스텔/도시형
- **알림 채널**: 텔레그램 봇 (아침브리핑에서 만든 봇 재활용).
  - 토큰/챗ID는 코드에 넣지 말고 환경변수로: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`
  - (2026-07-14 재발급 완료 — `run.sh`에 새 토큰 반영됨)

## 데이터 출처 — 한국부동산원 청약홈 공공 API (data.go.kr / odcloud)
무료지만 **API 키 발급 필요**: data.go.kr 가입 → "한국부동산원_청약홈 분양정보 조회 서비스" 활용신청 → 승인(보통 즉시).
발급받은 **일반 인증키(Decoding)**를 환경변수 `SERVICE_KEY`로 사용.

엔드포인트 (serviceKey, page, perPage 파라미터, JSON 응답):
- APT 일반분양:   `https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail`
- 무순위/잔여세대: `https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getRemndrLttotPblancDetail`
- 오피스텔/도시형: `https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getUrbtyOfctlLttotPblancDetail`

주요 응답 필드(방어적으로 .get() 처리 — 엔드포인트마다 조금씩 다름):
`HOUSE_MANAGE_NO`(관리번호=중복판별키), `PBLANC_NO`(공고번호), `HOUSE_NM`(주택명),
`SUBSCRPT_AREA_CODE_NM`(공급지역=지역필터), `HSSPLY_ADRES`(공급위치),
`RCRIT_PBLANC_DE`(모집공고일), `RCEPT_BGNDE`/`RCEPT_ENDDE`(청약접수 시작/종료),
`TOT_SUPLY_HSHLDCO`(공급규모), `PBLANC_URL`(공고 URL).

## 설계
1. 3개 엔드포인트를 page 순회하며 전부 수집
2. `SUBSCRPT_AREA_CODE_NM`이 서울/경기/인천인 것만 필터
3. 이전에 본 공고 id(`HOUSE_MANAGE_NO`)를 `seen.json`에 저장, 새 것만 골라냄
4. **첫 실행은 flood 방지**: 현재 공고를 전부 seen에 기록만 하고 "감시 시작 (N건)" 요약만 전송
5. 이후 실행부터 새 공고만 텔레그램 푸시 (한 번에 최대 ~15건, 초과 시 요약)
6. launchd로 하루 2회(예: 오전 9시, 오후 6시) 자동 실행

## 현재 상태
- [x] 프로젝트 폴더 생성
- [x] `chungyak_alert.py` 작성 (모킹 테스트로 첫실행/필터/신규푸시 로직 검증 완료)
- [x] `requirements.txt` (requests) + venv 설치 완료
- [x] `run.sh` + launchd plist (`com.chungyak.alert.plist`, 09:00/18:00) — plutil 검증 OK
- [x] README (키 발급법)
- [x] data.go.kr API 키로 실제 호출 테스트 — `SUBSCRPT_AREA_CODE_NM`이 "경기" 형태로
      확인됨 (필터 그대로 유효). 키/토큰/챗ID는 `run.sh`에 채워짐.
- [x] 첫 실행 완료 (수도권 156건 기록, "감시 시작" 요약 전송), 2차 실행에서 중복 방지 확인
- [x] plist를 `~/Library/LaunchAgents/`에 설치하고 load (`launchctl list`에 등록 확인)

## 추가 기능
- [x] 주택형별 분양가 표시 — 새 공고 상세 알림에 `getAPTLttotPblancMdl`(무순위/오피스텔은
      `getRemndrLttotPblancMdl`/`getUrbtyOfctlLttotPblancMdl`)를 공고별로 1회 조회해
      "84A㎡ 7.8억 (239세대)" 형식으로 최대 6개 타입 + 나머지는 가격 범위로 축약.
      APT/무순위는 `LTTOT_TOP_AMOUNT`, 오피스텔은 `SUPLY_AMOUNT` (단위: 만원).
      실패해도 알림 자체는 나가도록 방어 처리.
- [x] 확장성 개편 (2026-07-14):
  - `config.json` — 지역/조회기간/제외 키워드/리마인더 on-off를 코드 수정 없이 조정
  - `seen.json` v2 — 공고별 메타데이터(이름/지역/접수일/URL/리마인더 이력) 저장.
    v1(값=날짜 문자열)은 로드 시 자동 마이그레이션, 기존 공고도 실행 시 메타 백필
  - 접수 D-day 리마인더 — 접수 시작 당일/전날 1회씩 묶음 메시지 (중복 방지 플래그)
  - 다중 수신자 — `TELEGRAM_CHAT_ID`에 콤마 구분. 한 명 실패해도 나머지에겐 전송
    (새 수신자는 봇 @yyc_house_bot 에게 먼저 /start 를 눌러야 수신 가능)
- ⚠️ 필드명 함정: 접수일이 APT는 `RCEPT_*`, 무순위/오피스텔은 `SUBSCRPT_RCEPT_*`
  (무순위는 `GNRL_RCEPT_*`도 있음) — `rcept_dates()`가 fallback 처리
- [x] 실행 실패 알림 — 워크플로 실패 시(`if: failure()`) 텔레그램으로 실패 + 로그 링크 전송
- [x] 당첨자 발표일 알림 — `PRZWNER_PRESNATN_DE` 기반, 발표 당일 "🎉 오늘 당첨자 발표"
      (리마인더 메시지에 통합, `reminders.announce`로 on/off, 중복 방지 플래그 공유)
- [x] 경쟁률 후속 알림 — 접수 마감 다음날부터 14일간 경쟁률 API를 폴링, 발표되면
      "📊 경쟁률 발표" 메시지 (타입·순위·지역별, 중복 방지 플래그 "cmpet").
      경쟁률 서비스(data 15098905)는 활용신청 완료 → 실데이터 검증 완료 (2026-07-14).
      행이 (주택형×순위×해당/기타지역) 조합으로 오므로 타입당 1줄(1순위·해당지역
      우선)로 축약. `SUBSCRPT_RANK_CODE`는 정수(1/2)로 옴.
- [x] 실거래가 시세 비교 — 새 공고 상세 메시지에 같은 시군구 최근 3개월 아파트 매매
      실거래 중위가를 면적대(±5㎡)별로 분양가와 비교해 "N억 비쌈/저렴/비슷" 표시.
      주소→법정동코드 매핑은 `lawd_codes.py`(수도권 89개 시군구, 2024-08 기준) 사용.
      오피스텔은 비교 무의미해서 제외, 표본 3건 미만이면 생략, config `market_compare`.
      **활용신청 필요**: data.go.kr "국토교통부_아파트 매매 실거래가 자료" — 미신청
      상태면 시세 라인만 조용히 생략됨 (승인되면 자동 작동, 필드: excluUseAr/dealAmount)
- 후보(미착수): 입주예정월 표시, 특별공급 접수일 구분 리마인더, 무순위 폴링 강화,
  지역 세분화 include_keywords

## 완료 — GitHub Actions에서 운영 중 (2026-07-14 이전)
- 저장소: `YCYEOM/chungyak-alert` (비공개). 매일 KST 09:00/18:00 GitHub 서버에서 실행
  → **PC 꺼져 있어도 알림 옴**. `seen.json`은 실행 후 자동 커밋으로 상태 유지.
- 비밀값 3개는 저장소 Actions Secrets에 등록: `SERVICE_KEY`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`
- 로컬 launchd는 이중 알림 방지를 위해 해제함 (plist는 리포에서 gitignore, `run.sh`는
  토큰이 들어 있어 gitignore — 로컬 수동 테스트용으로만 사용)
- 수동 실행: Actions 탭 "Run workflow" 또는 `gh workflow run chungyak-alert`
- 주의: GitHub 스케줄은 수십 분 지연될 수 있음. 60일 무커밋 시 스케줄 자동 중지되나
  seen.json 커밋이 주기적으로 생겨 실질 문제 없음.
- 텔레그램 봇 토큰은 재발급 완료(구 토큰은 revoke됨), 로컬에 남아 있던 구 토큰 기록도 정리함.
