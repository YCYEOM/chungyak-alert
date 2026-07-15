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
    ` · 무순위 켬/끔: /musunwi · 설정 확인: /status)`
  );
}

async function sendReply(env, chatId, text) {
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text }),
  });
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
};
