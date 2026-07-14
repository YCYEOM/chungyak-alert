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
- 후보(미착수): 접수 시작 D-day 리마인더, 당첨자 발표일/입주예정월 표시, 경쟁률 후속 알림

## 완료 — 운영 중
매일 09:00/18:00 자동 실행. 로그는 `log.txt`, 기억한 공고는 `seen.json`.
텔레그램 봇 토큰은 재발급 완료(구 토큰은 revoke됨), 로컬에 남아 있던 구 토큰 기록도 정리함.
