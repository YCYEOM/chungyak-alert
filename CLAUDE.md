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
      활용신청 승인 완료 — 실호출 검증됨 (2026-07-15, resultCode 000,
      필드 excluUseAr/dealAmount 확인). 시세 라인 자동 작동 중.
- [x] 스케줄 지연 완화 + heartbeat (2026-07-15) — cron을 정각→43분(KST 08:43/17:43)으로
      옮겨 대기열 혼잡 회피. 보낼 메시지가 하나도 없는 실행은 "✅ ..." 요약 1줄을 전송해
      "텔레그램 무소식 = 실행 안 됨"으로 구분 가능 (GitHub 스케줄은 지연/드롭 가능).
- [x] 텔레그램 명령 구독 변경 (2026-07-15) — 수신자가 봇에게 /분양 /임대 /전체 를
      보내면 다음 실행 때 getUpdates로 읽어 반영 + 확인 답장. 상태는 `subs.json`
      (offset + 구독, Actions 자동 커밋, config subscriptions보다 우선).
      반영 지연 최대 다음 실행 시각까지. 명령은 CHAT_ID 등록된 수신자만 인식.
      발견성: 봇 명령 메뉴 setMyCommands 등록 완료(영문만 허용이라 /sale /rent /all —
      코드가 한글·영문 둘 다 인식), 전 수신자 1회 안내 방송 전송 완료 (2026-07-15).
      /help 답장은 즉각 반응이 아니라 무의미해서 제거함 (메뉴에서도 뺌).
- [x] 사용자별 구독 (2026-07-15) — config `subscriptions`: {"챗ID": ["분양", "임대"]}.
      비어 있거나 챗ID 미등록이면 전부 수신(하위호환). 카테고리 없는 메시지(하트비트·
      감시시작·실패알림)는 전원 수신. `send_telegram(text, category=...)` + `_targets()`.
- [x] LH 임대주택 감시 (2026-07-15) — "한국토지주택공사_분양임대공고문 조회 서비스"
      (data 15058530, 활용신청 승인·실데이터 검증 완료). 수도권 + `UPP_AIS_TP_NM`에
      "임대" 포함(상가/토지/분양주택 제외)만 감시, seen 키 "LH:{PAN_ID}", category="임대".
      첫 실행 flood 방지 요약(56건 기록됨). config `lh_rental`로 on/off.
      ⚠️ 이 API는 인증키 파라미터가 `ServiceKey`(대문자 S), 날짜가 "2026.07.15" 형식,
      응답이 [{dsSch}, {dsList+resHeader}] 배열 구조. CNP_CD_NM "전국" 공고는 지역
      필터에서 제외됨(대부분 상가/토지라 무해).
- [x] LH 마감 임박 리마인더 (2026-07-15) — LH는 접수 시작일 정보가 없어 마감 D-1 기준
      "⏰ LH 임대 내일 마감" 묶음 메시지 (category="임대", 플래그 "lh_end",
      `reminders.lh_end`로 on/off). 신규 발견이 곧 D-1인 공고는 새 공고 알림의
      마감일 표기로 갈음 (리마인더는 다음 실행부터라 못 잡음 — 알려진 한계).
- [x] 특별공급 접수일 구분 + 입주예정월 (2026-07-15) — SPSPLY_RCEPT_*가 일반 접수와
      다르면 상세 메시지 별도 라인 + 리마인더 특공 버킷(sp_today/sp_tomorrow).
      MVN_PREARNGE_YM → "🏗️ 입주예정 2029년 9월" (3개 엔드포인트 공통 확인).
- [x] 수신자별 지역 선택 (2026-07-15) — /서울 /경기 /인천 (/seoul /gyeonggi /incheon)
      으로 그 지역 공고만, /전지역 (/allregion) 해제. 단일 선택·마지막 명령 우선,
      subs.json "regions"에 저장. 상세·경쟁률·LH는 send_telegram(region=) 필터,
      리마인더·초과요약은 build/format 분리로 수신자별 개별 구성.
      지역 미상(region 없는) 공고는 지역 설정자에겐 안 감. 메뉴 7개 명령 등록됨.
- [x] 무순위 폴링 강화 (2026-07-15) — 주간 라이트 스케줄 4회(KST 10:13/12:13/14:13/16:13,
      cron 분=13이 라이트 표식 → workflow에서 LIGHT_MODE=1). 라이트 실행은 무순위만
      수집, LH·heartbeat 생략, 명령 처리·리마인더·경쟁률은 그대로(플래그로 중복 방지).
      부수효과: 텔레그램 구독 명령 반영도 주간 최대 ~2시간으로 단축.
      ⚠️ 로컬 수동 실행 전엔 반드시 git pull — seen.json이 원격보다 오래되면
      리마인더 플래그가 없어서 중복 전송됨 (2026-07-15 실제 발생).
- [x] 무순위 켬/끔 토글 (2026-07-15) — /무순위 (/musunwi) 보낼 때마다 전환,
      subs.json "no_remnd". 끄면 무순위 공고의 새 공고·리마인더·경쟁률에서 제외
      (카테고리·지역과 독립, 확인 답장에 "무순위 제외" 표시). 메뉴 8개 명령.
- [x] 설정 확인 명령 (2026-07-15) — /status (/설정 /상태 /내설정). 변경 없이
      "✅ 현재 설정 — ..." 확인 답장만 (변경 확인 답장과 같은 포맷 재사용). 메뉴 9개.
- [x] 명령 실시간화 (2026-07-15) — Cloudflare Worker `chungyak-cmd`
      (https://chungyak-cmd.ycyeom.workers.dev, worker/ 디렉토리, wrangler 배포)가
      텔레그램 webhook을 받아 즉시 처리: subs.json을 GitHub Contents API로 커밋 +
      즉시 답장. 봇 본체는 load_subs()로 읽기만, getUpdates 제거됨 (webhook 켜면
      getUpdates 사용 불가). 시크릿 4개: TELEGRAM_TOKEN/GITHUB_TOKEN(클래식 PAT,
      fine-grained로 교체 권장)/CHAT_IDS/WEBHOOK_SECRET (헤더 검증, 위조 403 확인).
      명령 테이블은 worker.js와 python 양쪽 동기 유지 필요. Worker 재배포:
      `cd worker && npx wrangler deploy`.
- [x] 접수중 공고 조회 /list (2026-07-15) — Worker가 청약홈 3종+LH를 실시간 조회해
      오늘 접수중(청약홈: 접수기간 내, LH: 마감 전 공고중)인 공고를 마감 임박순으로
      답장. 사용자의 지역·무순위·카테고리 설정 적용. 전체 건수를 다 보여줌 —
      4096자 제한은 raw 3500자 단위로 쪼개 여러 메시지 전송 (2026-07-16, 15건 캡 폐기).
      한글 별칭:
      /목록 /접수중 /공고. Worker 시크릿에 SERVICE_KEY 추가됨 (총 5개).
      LH는 최신 3페이지(300건)만 훑음 — 공고중 물량이 최신에 몰려 있어 충분.
- [x] 스케줄을 Cloudflare Worker cron으로 이관 (2026-07-16) — GitHub schedule이
      KST 아침(=UTC 자정 피크)에 3일 연속 드롭돼 폐기. Worker `scheduled` 핸들러가
      `repository_dispatch(run-alert)`를 쏘고 workflow는 repository_dispatch +
      workflow_dispatch만 listen. 라이트 모드는 `client_payload.light`로 전달.
      기존 fine-grained PAT(Contents:write)로 dispatch 가능해 시크릿 변경 없음.
      Cloudflare cron은 정시 발화가 보장되므로 혼잡 회피용 어중간한 분(43/13분)도
      폐기, 정시로 복귀 — KST 09:00/18:00 풀, 10:00~16:00 짝수 정시 라이트.
      라이트 판별은 worker.js `LIGHT_CRON` 문자열 일치 — 라이트 크론 변경 시
      wrangler.toml과 같이 수정할 것.
- 후보(미착수): 지역 세분화(시군구 단위) include_keywords, 가격 필터,
  SH·GH 지방공사 공고(데이터 소스 조사 필요), 당첨 가점 커트라인, 주간 다이제스트

## 완료 — GitHub Actions에서 운영 중 (2026-07-14 이전)
- 저장소: `YCYEOM/chungyak-alert` (비공개). 매일 KST 09:00/18:00 GitHub 서버에서 실행
  → **PC 꺼져 있어도 알림 옴**. `seen.json`은 실행 후 자동 커밋으로 상태 유지.
- 비밀값 3개는 저장소 Actions Secrets에 등록: `SERVICE_KEY`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`
- 로컬 launchd는 이중 알림 방지를 위해 해제함 (plist는 리포에서 gitignore, `run.sh`는
  토큰이 들어 있어 gitignore — 로컬 수동 테스트용으로만 사용)
- 수동 실행: Actions 탭 "Run workflow" 또는 `gh workflow run chungyak-alert`
- ~~주의: GitHub 스케줄은 수십 분 지연될 수 있음~~ → 2026-07-16부터 GitHub schedule
  미사용, Cloudflare Worker cron이 repository_dispatch로 트리거 (지연·드롭·60일 중지 없음).
- 텔레그램 봇 토큰은 재발급 완료(구 토큰은 revoke됨), 로컬에 남아 있던 구 토큰 기록도 정리함.
