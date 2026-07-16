// 청약 알림봇 — 텔레그램 명령 실시간 처리 Worker (Cloudflare Workers)
//
// 텔레그램 webhook을 받아 구독 명령을 즉시 반영하고 답장한다.
// 상태는 GitHub 저장소의 subs.json (Contents API로 읽기/쓰기 — 봇 본체가 실행 때 읽음).
//
// 필요한 시크릿 (wrangler secret put):
//   TELEGRAM_TOKEN  봇 토큰
//   GITHUB_TOKEN    fine-grained PAT (YCYEOM/chungyak-alert, Contents: Read and write)
//   CHAT_IDS        허용 수신자 챗ID (콤마 구분 — 봇 본체의 TELEGRAM_CHAT_ID와 동일)
//   WEBHOOK_SECRET  setWebhook 때 지정한 secret_token (요청 위조 방지)
//
// 명령 테이블은 chungyak_alert.py와 의미가 같아야 한다 (수정 시 양쪽 함께).

const REPO_FILE = "https://api.github.com/repos/YCYEOM/chungyak-alert/contents/subs.json";

const COMMANDS = { "분양": ["분양"], "임대": ["임대"], "전체": null,
                   "sale": ["분양"], "rent": ["임대"], "all": null };
const REGIONS = { "서울": "서울", "경기": "경기", "인천": "인천",
                  "seoul": "서울", "gyeonggi": "경기", "incheon": "인천",
                  "전지역": null, "allregion": null };
const REMND_TOGGLE = ["무순위", "musunwi"];
const STATUS = ["status", "설정", "상태", "내설정"];
const LIST = ["list", "목록", "접수중", "공고"];

// 접수중 공고 실시간 조회용 (봇 본체 chungyak_alert.py와 동일한 데이터 소스)
const APPLYHOME_BASE = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1";
const APPLYHOME_ENDPOINTS = [
  ["APT", "getAPTLttotPblancDetail", false],
  ["무순위/잔여", "getRemndrLttotPblancDetail", true],
  ["오피스텔/도시형", "getUrbtyOfctlLttotPblancDetail", false],
];
const LH_URL = "https://apis.data.go.kr/B552555/lhLeaseNoticeInfo1/lhLeaseNoticeInfo1";
const BASE_REGIONS = ["서울", "경기", "인천"];

function decodeContent(b64) {
  const bin = atob(b64.replace(/\n/g, ""));
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}

function encodeContent(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

async function readSubs(env) {
  const res = await fetch(REPO_FILE, { headers: ghHeaders(env) });
  if (!res.ok) throw new Error(`subs.json 읽기 실패: ${res.status}`);
  const body = await res.json();
  const subs = JSON.parse(decodeContent(body.content));
  subs.subscriptions ??= {};
  subs.regions ??= {};
  subs.no_remnd ??= {};
  return { subs, sha: body.sha };
}

async function writeSubs(env, subs, sha) {
  const res = await fetch(REPO_FILE, {
    method: "PUT",
    headers: { ...ghHeaders(env), "Content-Type": "application/json" },
    body: JSON.stringify({
      message: "chore: update subs.json (telegram command)",
      content: encodeContent(JSON.stringify(subs, null, 1) + "\n"),
      sha,
    }),
  });
  return res.ok;
}

function ghHeaders(env) {
  return {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    Accept: "application/vnd.github+json",
    "User-Agent": "chungyak-cmd-worker",
  };
}

// 명령을 subs에 반영. 변경 여부와 답장 필요 여부를 반환.
function applyCommand(subs, chatId, text) {
  if (text in COMMANDS) {
    if (COMMANDS[text] === null) delete subs.subscriptions[chatId];
    else subs.subscriptions[chatId] = COMMANDS[text];
    return { mutated: true, reply: true };
  }
  if (text in REGIONS) {
    if (REGIONS[text] === null) delete subs.regions[chatId];
    else subs.regions[chatId] = [REGIONS[text]];
    return { mutated: true, reply: true };
  }
  if (REMND_TOGGLE.includes(text)) {
    if (subs.no_remnd[chatId]) delete subs.no_remnd[chatId];
    else subs.no_remnd[chatId] = true;
    return { mutated: true, reply: true };
  }
  if (STATUS.includes(text)) return { mutated: false, reply: true };
  return { mutated: false, reply: false };
}

function statusText(subs, chatId) {
  const cats = subs.subscriptions[chatId];
  const regs = subs.regions[chatId];
  const catLabel = cats ? `${cats.join("·")} 알림만` : "전체 알림";
  const regLabel = regs && regs.length ? `${regs.join("·")}만` : "전 지역";
  const muteLabel = subs.no_remnd[chatId] ? " · 무순위 제외" : "";
  return (
    `✅ 현재 설정 — ${catLabel} · ${regLabel} 받아요.${muteLabel}\n` +
    `(알림: /sale /rent /all · 지역: /seoul /gyeonggi /incheon /allregion` +
    ` · 무순위 켬/끔: /musunwi · 설정 확인: /status · 접수중 공고: /list)`
  );
}

async function sendReply(env, chatId, text, html = false) {
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId, text,
      ...(html ? { parse_mode: "HTML", disable_web_page_preview: true } : {}),
    }),
  });
}

function kstToday() {
  return new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);
}

function escapeHtml(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function regionOk(region, prefs) {
  if (!prefs || !prefs.length) return true;
  return prefs.some((p) => region.startsWith(p));
}

// 청약홈 3종에서 오늘 접수중인 수도권 공고 (봇의 rcept_dates fallback과 동일한 필드 순서)
async function fetchOpenApplyhome(env, prefs, noRemnd) {
  const today = kstToday();
  const since = new Date(Date.now() - 60 * 86400 * 1000).toISOString().slice(0, 10);
  const out = [];
  for (const [typeName, ep, isRemnd] of APPLYHOME_ENDPOINTS) {
    if (isRemnd && noRemnd) continue;
    for (let page = 1; page <= 5; page++) {
      const u = new URL(`${APPLYHOME_BASE}/${ep}`);
      u.searchParams.set("serviceKey", env.SERVICE_KEY);
      u.searchParams.set("page", String(page));
      u.searchParams.set("perPage", "100");
      u.searchParams.set("cond[RCRIT_PBLANC_DE::GTE]", since);
      const res = await fetch(u);
      if (!res.ok) break;
      const body = await res.json().catch(() => ({}));
      const data = body.data || [];
      for (const row of data) {
        const region = (row.SUBSCRPT_AREA_CODE_NM || "").trim();
        if (!BASE_REGIONS.includes(region) || !regionOk(region, prefs)) continue;
        const begin = row.RCEPT_BGNDE || row.SUBSCRPT_RCEPT_BGNDE || row.GNRL_RCEPT_BGNDE;
        const end = row.RCEPT_ENDDE || row.SUBSCRPT_RCEPT_ENDDE || row.GNRL_RCEPT_ENDDE;
        if (!begin || !end || begin > today || end < today) continue;
        out.push({ name: row.HOUSE_NM, region, type: typeName, end, url: row.PBLANC_URL });
      }
      const total = body.matchCount ?? body.totalCount ?? 0;
      if (data.length < 100 || page * 100 >= total) break;
    }
  }
  return out;
}

// LH에서 공고중(마감 전)인 수도권 임대 공고
async function fetchOpenLH(env, prefs) {
  const today = kstToday();
  const out = [];
  // ponytail: 최신순 3페이지(300건)만 훑음 — 공고중 물량은 최신에 몰려 있음
  for (let page = 1; page <= 3; page++) {
    const u = new URL(LH_URL);
    u.searchParams.set("ServiceKey", env.SERVICE_KEY);
    u.searchParams.set("PG_SZ", "100");
    u.searchParams.set("PAGE", String(page));
    const res = await fetch(u);
    if (!res.ok) break;
    const body = await res.json().catch(() => null);
    if (!body) break;
    let ds = [];
    for (const part of Array.isArray(body) ? body : [body])
      if (part && part.dsList) ds = part.dsList;
    for (const row of ds) {
      if (!(row.UPP_AIS_TP_NM || "").includes("임대")) continue;
      const region = (row.CNP_CD_NM || "").trim();
      if (!BASE_REGIONS.some((r) => region.startsWith(r)) || !regionOk(region, prefs)) continue;
      const end = (row.CLSG_DT || "").replaceAll(".", "-");
      if (!end || end < today) continue;
      out.push({ name: row.PAN_NM, region, end, url: row.DTL_URL });
    }
    if (ds.length < 100) break;
  }
  return out;
}

function listSection(title, items) {
  if (!items.length) return [];
  const lines = [`[${title}]`];
  for (const it of items.slice(0, 15)) {
    const name = it.url
      ? `<a href="${it.url}">${escapeHtml(it.name)}</a>`
      : escapeHtml(it.name);
    const type = it.type ? `, ${it.type}` : "";
    lines.push(` · ${name} (${it.region}${type}) ~${it.end}`);
  }
  if (items.length > 15) lines.push(` · …외 ${items.length - 15}건`);
  return lines;
}

async function handleList(env, subs, chatId) {
  const cats = subs.subscriptions[chatId]; // null/undefined = 전체
  const prefs = subs.regions[chatId];
  const noRemnd = !!subs.no_remnd[chatId];
  const wantSale = !cats || cats.includes("분양");
  const wantRent = !cats || cats.includes("임대");

  const [sale, rent] = await Promise.all([
    wantSale ? fetchOpenApplyhome(env, prefs, noRemnd) : Promise.resolve([]),
    wantRent ? fetchOpenLH(env, prefs) : Promise.resolve([]),
  ]);
  sale.sort((a, b) => (a.end < b.end ? -1 : 1)); // 마감 임박순
  rent.sort((a, b) => (a.end < b.end ? -1 : 1));

  const regLabel = prefs && prefs.length ? prefs.join("·") : "수도권";
  const lines = [`📋 <b>접수중 공고</b> (${regLabel}${noRemnd ? " · 무순위 제외" : ""})`,
                 ...listSection("청약홈 분양", sale),
                 ...listSection("LH 임대", rent)];
  if (lines.length === 1) lines.push("지금 접수중인 공고가 없어요.");
  await sendReply(env, chatId, lines.join("\n"), true);
}

export default {
  async fetch(req, env) {
    if (req.method !== "POST") return new Response("ok");
    if (req.headers.get("x-telegram-bot-api-secret-token") !== env.WEBHOOK_SECRET)
      return new Response("forbidden", { status: 403 });

    const update = await req.json().catch(() => ({}));
    const msg = update.message;
    const chatId = String(msg?.chat?.id ?? "");
    const allowed = (env.CHAT_IDS || "").split(",").map((s) => s.trim());
    if (!chatId || !allowed.includes(chatId)) return new Response("ok");

    let text = (msg.text || "").trim().replace(/^\//, "").toLowerCase();
    if (text.endsWith("만")) text = text.slice(0, -1);

    // 접수중 공고 실시간 조회 (설정 변경 없음)
    if (LIST.includes(text)) {
      try {
        const { subs } = await readSubs(env);
        await handleList(env, subs, chatId);
      } catch (e) {
        await sendReply(env, chatId, "⚠️ 공고 조회에 실패했어요. 잠시 후 다시 시도해주세요.");
      }
      return new Response("ok");
    }

    // 상태 변경은 subs.json 커밋 — sha 충돌(동시 수정) 시 1회 재시도
    for (let attempt = 0; attempt < 2; attempt++) {
      let state;
      try {
        state = await readSubs(env);
      } catch (e) {
        await sendReply(env, chatId, "⚠️ 설정 저장소에 접근하지 못했어요. 잠시 후 다시 시도해주세요.");
        return new Response("ok");
      }
      const { subs, sha } = state;
      const { mutated, reply } = applyCommand(subs, chatId, text);
      if (!reply) return new Response("ok"); // 명령 아님 — 무시
      if (mutated && !(await writeSubs(env, subs, sha))) continue; // sha 충돌 → 재시도
      await sendReply(env, chatId, statusText(subs, chatId));
      return new Response("ok");
    }
    await sendReply(env, chatId, "⚠️ 설정 저장에 실패했어요. 잠시 후 다시 시도해주세요.");
    return new Response("ok");
  },

  // Cloudflare cron → repository_dispatch로 봇 워크플로 실행 (GitHub schedule 드롭 대체)
  async scheduled(controller, env) {
    const light = controller.cron.startsWith("13 "); // wrangler.toml 크론과 동기
    const res = await fetch("https://api.github.com/repos/YCYEOM/chungyak-alert/dispatches", {
      method: "POST",
      headers: { ...ghHeaders(env), "Content-Type": "application/json" },
      body: JSON.stringify({ event_type: "run-alert", client_payload: { light } }),
    });
    if (!res.ok) throw new Error(`repository_dispatch 실패: ${res.status}`); // 실패는 CF 대시보드에 기록
  },
};
