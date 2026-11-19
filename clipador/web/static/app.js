const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const api = async (url, opt = {}) => {
  const r = await fetch(url, { headers: opt.body instanceof FormData ? {} : { "Content-Type": "application/json" }, ...opt });
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch {} throw new Error(m); }
  return r.json();
};
const toast = (msg) => { const t = $("#toast"); t.textContent = msg; t.classList.add("on"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("on"), 2400); };
const copy = async (text, msg = "Copiado!") => { await navigator.clipboard.writeText(text); toast(msg); };
const ago = (ts) => { const s = Date.now() / 1000 - ts; if (s < 60) return "agora"; if (s < 3600) return `${Math.floor(s / 60)} min`; if (s < 86400) return `${Math.floor(s / 3600)} h`; return new Date(ts * 1000).toLocaleDateString("pt-BR"); };
const PLAT = { twitch: "Twitch", kick: "Kick", youtube: "YouTube" };
const ver = (c) => `?v=${c.v || Math.floor(c.created_at)}`;
const postText = (c) => `${c.title}\n\n${c.description}\n\n${c.hashtags.join(" ")}`;

const state = { view: "clips", f: "todo", streamer: "", q: "", clips: [], known: new Set(), first: true, sel: new Set(), open: null };

/* ---------------- navegação ---------------- */
function go(view) {
  state.view = view;
  $$(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
  $$("#nav a[data-view]").forEach((a) => a.classList.toggle("on", a.dataset.view === view));
}
window.addEventListener("hashchange", () => go(location.hash.slice(1) || "clips"));

/* ---------------- clips ---------------- */
function visible() {
  const q = state.q.toLowerCase();
  return state.clips.filter((c) =>
    (state.f === "all" || c.posted === (state.f === "done")) &&
    (!q || `${c.title} ${c.card.join(" ")} ${c.transcript} ${c.display}`.toLowerCase().includes(q)));
}

function tile(c) {
  const el = document.createElement("article");
  el.className = "tile" + (state.sel.has(c.id) ? " picked" : "") + (c.status === "refazendo" ? " busy" : "") +
    (!state.first && !state.known.has(c.id) ? " new" : "");
  el.dataset.id = c.id;
  el.innerHTML = `
    <div class="thumb" data-open>
      <img src="/media/${c.cover_path}${ver(c)}" loading="lazy" alt="">
      <span class="play">▶</span>
      <span class="dur">${Math.round(c.duration)}s</span>
      ${c.posted ? `<span class="badge-posted">postado</span>` : ""}
      <label class="pick" title="Selecionar"><input type="checkbox" ${state.sel.has(c.id) ? "checked" : ""}></label>
    </div>
    <div class="meta">
      <div class="who"><b>${esc(c.display)}</b><span>${esc(PLAT[c.platform] || c.platform)} · ${ago(c.created_at)}</span></div>
      <div class="title" data-open>${esc(c.title)}</div>
      <div class="quick">
        <a class="btn icon" href="/media/${c.video_path}${ver(c)}" download="${c.streamer}_${c.id}.mp4" title="Baixar vídeo">⬇</a>
        <button class="icon" data-a="copy" title="Copiar texto do post">📋</button>
        <button class="icon" data-a="posted" title="${c.posted ? "Voltar pra fila" : "Marcar postado"}">${c.posted ? "↩" : "✅"}</button>
        <button class="icon grow" data-open>Abrir</button>
      </div>
    </div>`;
  return el;
}

function renderGrid() {
  const list = visible();
  const grid = $("#grid");
  grid.replaceChildren(...list.map(tile));
  $("#grid-empty").hidden = list.length > 0;
  $("#n-todo").textContent = state.clips.filter((c) => !c.posted).length;
  $("#n-done").textContent = state.clips.filter((c) => c.posted).length;
  $("#n-badge").textContent = state.clips.filter((c) => !c.posted).length;
  renderBulk();
}

function renderBulk() {
  // descarta seleções de clipes que sumiram
  const ids = new Set(state.clips.map((c) => c.id));
  state.sel.forEach((id) => !ids.has(id) && state.sel.delete(id));
  const n = state.sel.size;
  $("#bulkbar").hidden = n === 0;
  $("#sel-n").textContent = `${n} selecionado${n === 1 ? "" : "s"}`;
  const vis = visible();
  $("#check-all").checked = vis.length > 0 && vis.every((c) => state.sel.has(c.id));
}

async function loadClips() {
  const all = await api("/api/clips" + (state.streamer ? `?streamer=${encodeURIComponent(state.streamer)}` : ""));
  const fresh = all.filter((c) => !state.known.has(c.id));
  const key = (c) => [c.id, c.posted, c.status, c.title, c.card, c.v];
  const changed = JSON.stringify(all.map(key)) !== JSON.stringify(state.clips.map(key));
  const prevOpen = state.clips.find((c) => c.id === state.open);
  state.clips = all;
  if (changed) renderGrid();
  // vídeo aberto foi refeito (card novo): troca pelo vídeo novo sozinho
  const nowOpen = all.find((c) => c.id === state.open);
  if (prevOpen && nowOpen && (prevOpen.v !== nowOpen.v || prevOpen.status !== nowOpen.status)) {
    openViewer(nowOpen.id);
    if (prevOpen.status === "refazendo" && nowOpen.status !== "refazendo")
      toast(nowOpen.v !== prevOpen.v ? "✅ Vídeo refeito com o card novo" : "❌ Não deu pra refazer o vídeo — veja Ao vivo → Atividade");
  }
  if (!state.first && fresh.length) { toast(`🔥 ${fresh.length} clipe novo!`); document.title = `(${fresh.length}) Clipador GTA 6`; }
  all.forEach((c) => state.known.add(c.id));
  state.first = false;
  if (state.open && !state.clips.find((c) => c.id === state.open)) closeViewer();
}

$("#grid").addEventListener("click", async (e) => {
  const tileEl = e.target.closest(".tile");
  if (!tileEl) return;
  const c = state.clips.find((x) => x.id === +tileEl.dataset.id);
  if (e.target.closest(".pick")) {
    if (e.target.tagName !== "INPUT") return;
    e.target.checked ? state.sel.add(c.id) : state.sel.delete(c.id);
    tileEl.classList.toggle("picked", e.target.checked);
    return renderBulk();
  }
  const a = e.target.closest("[data-a]")?.dataset.a;
  try {
    if (a === "copy") return copy(postText(c), "Título + descrição + hashtags copiados");
    if (a === "posted") { await api(`/api/clips/${c.id}/posted`, { method: "POST", body: JSON.stringify({ posted: !c.posted }) }); toast(c.posted ? "Voltou pra fila" : "Marcado como postado"); return loadClips(); }
  } catch (err) { return toast("Erro: " + err.message); }
  if (e.target.closest("[data-open]")) openViewer(c.id);
});

$("#check-all").addEventListener("change", (e) => {
  visible().forEach((c) => (e.target.checked ? state.sel.add(c.id) : state.sel.delete(c.id)));
  renderGrid();
});
$("#seg").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  $$("#seg button").forEach((x) => x.classList.toggle("on", x === b));
  state.f = b.dataset.f; renderGrid();
});
$("#filter").addEventListener("change", (e) => { state.streamer = e.target.value; state.clips = []; loadClips(); });
$("#search").addEventListener("input", (e) => { state.q = e.target.value; renderGrid(); });

$("#bulkbar").addEventListener("click", async (e) => {
  const a = e.target.closest("[data-bulk]")?.dataset.bulk; if (!a) return;
  const ids = [...state.sel];
  try {
    if (a === "clear") { state.sel.clear(); return renderGrid(); }
    if (a === "zip") {
      toast("Preparando o .zip…");
      const r = await fetch("/api/clips/zip", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids }) });
      if (!r.ok) throw new Error(r.statusText);
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const link = Object.assign(document.createElement("a"), { href: url, download: `clipes_${ids.length}.zip` });
      document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 5000);
      return toast(`${ids.length} clipes baixados`);
    }
    if (a === "posted" || a === "unposted") {
      await api("/api/clips/bulk-posted", { method: "POST", body: JSON.stringify({ ids, posted: a === "posted" }) });
      toast(a === "posted" ? `${ids.length} marcados como postados` : `${ids.length} voltaram pra fila`);
    }
    if (a === "delete") {
      if (!confirm(`Excluir ${ids.length} clipe(s)? Não dá pra desfazer.`)) return;
      await api("/api/clips/bulk-delete", { method: "POST", body: JSON.stringify({ ids }) });
      toast(`${ids.length} excluídos`);
    }
    state.sel.clear(); await loadClips(); renderGrid();
  } catch (err) { toast("Erro: " + err.message); }
});

/* ---------------- visualizador ---------------- */
function openViewer(id) {
  const c = state.clips.find((x) => x.id === id); if (!c) return;
  state.open = id;
  const v = $("#v-video");
  const src = `/media/${c.video_path}${ver(c)}`;
  if (!v.src.endsWith(src)) { v.src = src; v.poster = `/media/${c.cover_path}${ver(c)}`; v.play().catch(() => {}); }
  const busy = c.status === "refazendo";
  $("#v-side").innerHTML = `
    <div class="who"><b>${esc(c.display)}</b><span>${esc(PLAT[c.platform] || c.platform)} · ${ago(c.created_at)} · ${Math.round(c.duration)}s</span></div>
    <div class="actions-main">
      <a class="btn primary" href="/media/${c.video_path}${ver(c)}" download="${c.streamer}_${c.id}.mp4">⬇ Baixar vídeo</a>
      <a class="btn" href="/media/${c.cover_path}${ver(c)}" download="${c.streamer}_${c.id}_capa.jpg">🖼 Capa</a>
      <button data-a="posted">${c.posted ? "↩ Voltar pra fila" : "✅ Postado"}</button>
    </div>
    <div class="field"><label>Título <button class="mini" data-copy="title">copiar</button></label><input data-f="title" value="${esc(c.title)}"></div>
    <div class="field"><label>Descrição <button class="mini" data-copy="description">copiar</button></label><textarea data-f="description" rows="3">${esc(c.description)}</textarea></div>
    <div class="field"><label>Hashtags <button class="mini" data-copy="hashtags">copiar</button></label><input data-f="hashtags" value="${esc(c.hashtags.join(" "))}"></div>
    <div class="row"><button data-a="save">💾 Salvar textos</button><button data-a="copyall" class="primary">📋 Copiar tudo</button></div>
    <div class="field card-edit"><label>Card do vídeo (máx. 2 linhas de 42 letras)</label>
      <input data-f="l1" maxlength="42" value="${esc(c.card[0] || "")}"><input data-f="l2" maxlength="42" value="${esc(c.card[1] || "")}">
      <button data-a="card" ${busy ? "disabled" : ""}>${busy ? "⏳ Refazendo o vídeo… (troca sozinho quando ficar pronto)" : "🔁 Refazer vídeo com esse card"}</button></div>
    ${c.transcript ? `<details><summary>Fala transcrita</summary><p class="hint">${esc(c.transcript)}</p></details>` : ""}
    <button data-a="del" class="danger ghost">🗑 Excluir clipe</button>`;
  $("#viewer").hidden = false;
  document.body.style.overflow = "hidden";
}
function closeViewer() {
  state.open = null; $("#viewer").hidden = true; document.body.style.overflow = "";
  const v = $("#v-video"); v.pause(); v.removeAttribute("src"); v.load();
}
function step(d) {
  const list = visible(); const i = list.findIndex((c) => c.id === state.open);
  if (i >= 0 && list[i + d]) openViewer(list[i + d].id);
}
$("#v-prev").onclick = () => step(-1);
$("#v-next").onclick = () => step(1);
$("#viewer").addEventListener("click", async (e) => {
  if (e.target.closest("[data-close]")) return closeViewer();
  const c = state.clips.find((x) => x.id === state.open); if (!c) return;
  const f = (k) => $(`#v-side [data-f="${k}"]`).value;
  const cp = e.target.closest("[data-copy]")?.dataset.copy;
  if (cp) return copy(f(cp), "Copiado");
  const a = e.target.closest("[data-a]")?.dataset.a; if (!a) return;
  try {
    if (a === "copyall") return copy(`${f("title")}\n\n${f("description")}\n\n${f("hashtags")}`, "Título + descrição + hashtags copiados");
    if (a === "posted") { await api(`/api/clips/${c.id}/posted`, { method: "POST", body: JSON.stringify({ posted: !c.posted }) }); toast(c.posted ? "Voltou pra fila" : "Marcado como postado"); }
    if (a === "save") { await api(`/api/clips/${c.id}`, { method: "PATCH", body: JSON.stringify({ title: f("title"), description: f("description"), hashtags: f("hashtags").split(/\s+/).filter(Boolean) }) }); toast("Textos salvos"); }
    if (a === "card") { await api(`/api/clips/${c.id}/card`, { method: "POST", body: JSON.stringify({ lines: [f("l1"), f("l2")].filter((x) => x.trim()) }) }); toast("Refazendo o vídeo… (aparece em ~1 min)"); }
    if (a === "del") { if (!confirm("Excluir esse clipe?")) return; await api(`/api/clips/${c.id}`, { method: "DELETE" }); toast("Excluído"); closeViewer(); }
    await loadClips(); if (state.open) openViewer(state.open);
  } catch (err) { toast("Erro: " + err.message); }
});
document.addEventListener("keydown", (e) => {
  if ($("#viewer").hidden || ["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
  if (e.key === "Escape") closeViewer();
  if (e.key === "ArrowLeft") step(-1);
  if (e.key === "ArrowRight") step(1);
});

/* ---------------- ao vivo ---------------- */
async function loadStatus() {
  const s = await api("/api/status");
  renderAlert(s);
  renderControl(s);
  $("#pill-live").innerHTML = !s.running ? "painel sem gravação"
    : !s.auto ? `<a href="#live" class="paused">⏸ cortes parados</a>`
    : `<span class="dot"></span> ${s.live.length} gravando · fila ${s.queue + s.processing.length}`;
  $("#live-badge").textContent = s.live.length;
  $("#live-count").textContent = s.live.length;
  $("#lives").innerHTML = s.live.length ? s.live.map((l) => `
    <div class="live"><span class="dot"></span>
      <div class="grow"><b>${esc(l.display)}</b> <span class="hint">${esc(PLAT[l.platform] || l.platform)} · buffer ${l.buffer_s}s · ${l.chat_msgs} msgs</span>
        <div class="meta">${esc(l.title)}</div></div>
      <button data-cut="${esc(l.name)}" title="A IA analisa os últimos ~2 min e corta o melhor trecho">✂️ Cortar agora</button>
    </div>`).join("") : `<div class="empty">${!s.auto ? "Cortes parados: ninguém está sendo gravado."
      : "Ninguém dos marcados está ao vivo jogando GTA. Checando a cada minuto…"}</div>`;
  $("#busy").innerHTML = [...s.processing.map((p) => `⚙️ editando ${esc(p)}`), ...s.background.map((b) => `⏳ ${esc(b)}`),
    ...(s.vods || []).map((v) => `🎞️ VOD ${esc(v.label)}: ${esc(v.stage)}`)].map((t) => `<span class="chip">${t}</span>`).join("");
  $("#log").innerHTML = s.log.length ? s.log.map((l) => `<div>${esc(l)}</div>`).join("") : `<div class="empty">Aguardando…</div>`;
}
document.addEventListener("click", async (e) => {
  const cut = e.target.closest("[data-cut]");
  if (cut) { try { await api(`/api/streamers/${cut.dataset.cut}/clip`, { method: "POST" }); toast("A IA está analisando… (~1 min)"); } catch (err) { toast(err.message); } }
});

/* ---------------- configurações ---------------- */
async function loadSettings() {
  const s = await api("/api/settings");
  $("#set-handle").value = s.handle === "@seuperfil" ? "" : s.handle;
  $("#set-score").value = s.min_score;
  $("#set-gta").checked = s.require_gta;
  $("#set-zoom").value = s.zoom;
  $("#set-quality").value = s.quality;
  $("#set-autocam").checked = s.auto_webcam;
  $$(".key-state").forEach((el) => { const h = s.keys[el.dataset.p]; el.textContent = h ? `· salva (${h})` : "· sem chave"; });
  $$(".signup").forEach((a) => (a.href = s.signup[a.dataset.p]));
  $("#saved-in").textContent = `Fica salvo em ${s.saved_in} — atualizar o Clipador não apaga nada.`;
  return s;
}
$("#settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = { handle: fd.get("handle").trim(), min_score: +fd.get("min_score"), require_gta: $("#set-gta").checked,
    zoom: fd.get("zoom"), quality: fd.get("quality"), auto_webcam: $("#set-autocam").checked };
  for (const k of ["groq_key", "cerebras_key", "gemini_key"]) if (fd.get(k).trim()) body[k] = fd.get(k).trim();
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(body) });
    ["#set-key", "#set-cerebras", "#set-gemini"].forEach((s) => ($(s).value = ""));
    await loadSettings(); toast("Configurações salvas"); loadStatus();
  } catch (err) { toast("Erro: " + err.message); }
});
const PNAME = { groq: "Groq", cerebras: "Cerebras", gemini: "Gemini" };
$("#test-key").addEventListener("click", async () => {
  $("#test-res").textContent = "testando…";
  try {
    const r = await api("/api/settings/test-groq", { method: "POST" });
    $("#test-res").textContent = Object.entries(r.providers).map(([k, v]) => `${PNAME[k]}: ${v}`).join(" · ");
    toast("✅ IA funcionando");
  } catch (err) { $("#test-res").textContent = ""; toast("❌ " + err.message); }
});
function renderAlert(s) {
  const msgs = [];
  if (!s.groq) msgs.push(`Sem chave de IA: a IA não escolhe os cortes nem escreve o card. <a href="#tools">Colar a chave</a>`);
  if (s.running && s.watching === 0) msgs.push("Nenhum streamer na lista (streamers.yaml).");
  $("#alert").innerHTML = msgs.join("<br>");
  $("#alert").hidden = msgs.length === 0;
}

/* ---------------- ligar/parar e escolher streamers ---------------- */
state.auto = true; state.streamers = []; state.liveNames = new Set();
function renderControl(s) {
  if (s) { state.auto = s.auto; state.running = s.running; state.liveNames = new Set(s.live.map((l) => l.name)); }
  const on = state.auto;
  $("#auto-state").textContent = on ? "LIGADOS" : "PARADOS";
  $("#auto-state").className = "state " + (on ? "on" : "off");
  $("#auto-btn").textContent = on ? "⏸ Parar cortes" : "▶ Ligar cortes";
  $("#auto-btn").className = "big " + (on ? "danger" : "primary");
  $("#auto-hint").textContent = !state.running
    ? "Painel aberto sem gravação. Abra pelo INICIAR.bat (ou pelo atalho) para gravar."
    : on ? "Grava quem estiver ao vivo jogando GTA e corta sozinho quando o chat explode."
         : "Nada está sendo gravado. Aperte Ligar para voltar a cortar.";
  const list = state.streamers;
  const n = list.filter((x) => x.enabled).length;
  $("#pick-count").textContent = `${n} de ${list.length} marcados`;
  $("#pick-list").innerHTML = list.map((x) => {
    const rec = state.liveNames.has(x.name);
    const tag = rec ? `<span class="tag rec">● gravando</span>` : "";
    return `<label class="pick-item${x.enabled ? " on" : ""}"><input type="checkbox" data-name="${esc(x.name)}" ${x.enabled ? "checked" : ""}>
      <span class="grow"><b>${esc(x.display)}</b> <span class="hint">${esc(x.channels.map((c) => PLAT[c.platform] || c.platform).join(" · "))}</span></span>${tag}</label>`;
  }).join("");
}
$("#auto-btn").addEventListener("click", async () => {
  const on = !state.auto;
  if (!on && !confirm("Parar os cortes automáticos? As gravações em andamento param (clipes que já estão sendo editados terminam).")) return;
  try { const r = await api("/api/auto", { method: "POST", body: JSON.stringify({ on }) }); state.auto = r.auto; renderControl(); toast(on ? "▶ Cortes ligados" : "⏸ Cortes parados"); loadStatus(); }
  catch (err) { toast("Erro: " + err.message); }
});
async function setEnabled(changes) {
  try {
    await api("/api/streamers/enabled", { method: "PUT", body: JSON.stringify({ enabled: changes }) });
    state.streamers.forEach((x) => { if (x.name in changes) x.enabled = changes[x.name]; });
    renderControl();
  } catch (err) { toast("Erro: " + err.message); }
}
$("#pick-list").addEventListener("change", (e) => {
  const cb = e.target.closest("input[data-name]"); if (!cb) return;
  setEnabled({ [cb.dataset.name]: cb.checked });
  toast(cb.checked ? "Vai clipar esse streamer" : "Esse streamer não vai mais ser clipado");
});
$$("[data-pick]").forEach((b) => b.addEventListener("click", () => {
  const on = b.dataset.pick === "all";
  setEnabled(Object.fromEntries(state.streamers.map((x) => [x.name, on])));
  toast(on ? "Todos marcados" : "Nenhum marcado");
}));

/* ---------------- ferramentas ---------------- */
async function loadStreamers() {
  const st = await api("/api/streamers");
  state.streamers = st; renderControl();
  const opts = st.map((s) => `<option value="${esc(s.name)}">${esc(s.display)}</option>`).join("");
  $$(".streamer-select").forEach((el) => (el.innerHTML = opts));
  $("#filter").innerHTML = `<option value="">Todos os streamers</option>` + opts;
}
$("#vod-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  try {
    await api("/api/vod", { method: "POST", body: JSON.stringify({ url: fd.get("url"), streamer: fd.get("streamer"), top_n: +fd.get("top_n") }) });
    toast("VOD na fila — acompanhe logo abaixo"); e.target.querySelector("[name=url]").value = ""; loadVods();
  } catch (err) { toast("Erro: " + err.message); }
});

/* ---------------- tarefas de VOD ---------------- */
const VOD_BADGE = { "na fila": "fila", rodando: "on", pausado: "pause", "concluído": "done", erro: "err" };
const fmtDur = (s) => (s ? `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}` : "");
let vodOpen = new Set();
async function loadVods() {
  let list;
  try { list = await api("/api/vods"); } catch { return; }
  const names = Object.fromEntries((state.streamers || []).map((s) => [s.name, s.display]));
  $("#vod-list").innerHTML = list.map((t) => {
    const pct = Math.round((t.progress || 0) * 100);
    const active = t.state === "rodando" || t.state === "na fila";
    const btn = active ? `<button data-vod="pause" data-id="${t.id}">⏸ Pausar</button>`
      : (t.state === "pausado" || t.state === "erro") ? `<button class="primary" data-vod="resume" data-id="${t.id}">▶ Continuar</button>` : "";
    return `<div class="vod-item ${VOD_BADGE[t.state] || ""}">
      <div class="vod-top">
        <span class="vod-state">${esc(t.state)}</span>
        <b class="grow" title="${esc(t.url)}">${esc(names[t.streamer] || t.streamer)} · ${esc(t.title || t.url)}</b>
        <span class="hint">${fmtDur(t.total)}</span>
      </div>
      <div class="bar"><span style="width:${pct}%"></span></div>
      <div class="vod-stage">${esc(t.stage)}${t.error ? ` — <span class="danger-txt">${esc(t.error)}</span>` : ""}</div>
      <div class="vod-foot">
        <span class="hint">✅ ${t.clips_done} de ${t.top_n} clipes · ${t.tried} momento(s) analisado(s)${t.moments ? ` de ${t.moments.length}` : ""}</span>
        <span class="grow"></span>
        ${btn}
        <button class="ghost" data-vod="log" data-id="${t.id}">${vodOpen.has(t.id) ? "Esconder" : "Detalhes"}</button>
        <button class="danger ghost" data-vod="del" data-id="${t.id}">🗑 Excluir</button>
      </div>
      ${vodOpen.has(t.id) ? `<div class="log vod-log">${(t.log || []).slice().reverse().map((l) => `<div>${esc(l)}</div>`).join("") || "<div>—</div>"}</div>` : ""}
    </div>`;
  }).join("");
}
$("#vod-list").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-vod]"); if (!b) return;
  const id = b.dataset.id, a = b.dataset.vod;
  try {
    if (a === "log") { vodOpen.has(id) ? vodOpen.delete(id) : vodOpen.add(id); return loadVods(); }
    if (a === "pause") { await api(`/api/vods/${id}/pause`, { method: "POST" }); toast("Pausando — termina o trecho atual e para"); }
    if (a === "resume") { await api(`/api/vods/${id}/resume`, { method: "POST" }); toast("Continuando de onde parou"); }
    if (a === "del") {
      if (!confirm("Excluir essa tarefa de VOD? Ela para agora. Os clipes que já ficaram prontos continuam em Clips.")) return;
      await api(`/api/vods/${id}`, { method: "DELETE" }); toast("Tarefa excluída");
    }
    loadVods();
  } catch (err) { toast("Erro: " + err.message); }
});
$("#test-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await api("/api/test-clip", { method: "POST", body: new FormData(e.target) }); toast("Gerando clipe de teste… acompanhe em Ao vivo"); }
  catch (err) { toast("Erro: " + err.message); }
});

window.addEventListener("focus", () => (document.title = "Clipador GTA 6"));
// fechar a aba desliga o Clipador (recarregar não: a página nova volta a falar com ele em segundos)
window.addEventListener("pagehide", () => { try { navigator.sendBeacon("/api/bye"); } catch {} });
// segura uma trava pra o navegador não "congelar" a aba em segundo plano (senão o Clipador acharia que ela fechou)
try { navigator.locks?.request("clipador-painel", () => new Promise(() => {})); } catch {}
go(location.hash.slice(1) || "clips");
loadStreamers(); loadClips(); loadStatus(); loadSettings().catch(() => {}); loadVods();
// janela/aba escondida (minimizada, outra aba na frente): não redesenha nada, só avisa a cada 30 s que
// o painel continua aberto (senão o Clipador acharia que a aba fechou). Ao voltar, atualiza na hora.
let lastPing = 0;
const hidden = () => document.hidden;
const syncHidden = () => document.documentElement.classList.toggle("is-hidden", hidden());
document.addEventListener("visibilitychange", () => {
  syncHidden();
  if (!hidden()) { loadStatus().catch(() => {}); loadClips().catch(() => {}); loadVods(); }
});
syncHidden();
setInterval(() => {
  if (hidden()) {
    if (Date.now() - lastPing < 30000) return;
    lastPing = Date.now();
    loadStatus().catch(() => {});
    return;
  }
  loadStatus().catch(() => {}); loadClips().catch(() => {});
}, 5000);
// progresso do VOD: a cada 2 s com Ferramentas aberta, senão a cada 5 s
setInterval(() => { if (!hidden() && (state.view === "tools" || Date.now() % 5000 < 2000)) loadVods(); }, 2000);
