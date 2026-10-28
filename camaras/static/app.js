const $ = (s) => document.querySelector(s);
const ROTULOS = {
  encargo: "ENCARGO VIGENTE", sem_encargo: "sem encargo", indeterminado: "indeterminado",
  pendente: "na fila", em_andamento: "consultando", aguardando_captcha: "resolva o captcha", erro: "erro",
};
let ultimoId = 0;

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
function exibir(p) {
  if (p.length === 6) return `${p.slice(0, 2)}·${p.slice(2, 4)}·${p.slice(4)}`;
  return /^[A-Z]{2}\d{3}$/.test(p) ? `${p.slice(0, 2)}·${p.slice(2)}` : `${p.slice(0, 3)}·${p.slice(3)}`;
}
function chip(status) {
  return status ? `<span class="chip ${esc(status)}">${esc(ROTULOS[status] || status)}</span>` : "";
}
function hora(t) {
  return new Date(t * 1000).toLocaleString("pt-BR");
}
async function json(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

async function carregarCameras() {
  const cams = await json("/api/cameras");
  $("#sem-cameras").hidden = cams.length > 0;
  $("#resumo-cameras").textContent = cams.length ? `${cams.filter((c) => c.online).length}/${cams.length} online` : "";
  const grade = $("#cameras");
  for (const c of cams) {
    let el = document.getElementById(`cam-${c.id}`);
    if (!el) {
      el = document.createElement("div");
      el.className = "camera";
      el.id = `cam-${c.id}`;
      el.innerHTML = `<img alt="" src="/api/cameras/${encodeURIComponent(c.id)}/mjpeg">
        <div class="rodape"><span><span class="status"></span><b></b> <span class="muted local"></span></span>
        <span class="muted info"></span></div>`;
      grade.appendChild(el);
    }
    el.querySelector(".status").classList.toggle("on", c.online);
    el.querySelector("b").textContent = c.nome;
    el.querySelector(".local").textContent = c.local || "";
    el.querySelector(".info").textContent = c.online ? (c.alpr ? "ALPR ativo" : "") : c.erro || "offline";
  }
}

function itemDeteccao(d) {
  const li = document.createElement("li");
  li.innerHTML = `<img alt="" src="/api/deteccoes/${d.id}/recorte.jpg">
    <div><span class="placa">${esc(exibir(d.placa))}</span> ${chip(d.consulta_status)}<br>
    <span class="muted">${esc(d.camera_id)} · ${hora(d.criado_em)} · OCR ${Math.round(d.confianca_ocr * 100)}%</span></div>`;
  li.onclick = () => abrirPlaca(d.placa);
  return li;
}

async function carregarDeteccoes() {
  const novas = await json(`/api/deteccoes?desde_id=${ultimoId}&limite=50`);
  const lista = $("#deteccoes");
  for (const d of novas.reverse()) {
    lista.prepend(itemDeteccao(d));
    ultimoId = Math.max(ultimoId, d.id);
  }
  while (lista.children.length > 200) lista.lastChild.remove();
}

async function carregarAlertas() {
  const alertas = await json("/api/alertas");
  const box = $("#alertas");
  box.hidden = alertas.length === 0;
  box.innerHTML = alertas.map((a) => {
    const u = a.ultima_deteccao;
    return `⚠️ <span class="placa">${esc(exibir(a.placa))}</span> com encargo vigente` +
      (u ? ` · visto em <b>${esc(u.camera_id)}</b> ${hora(u.criado_em)}` : "");
  }).join("<br>");
}

async function abrirPlaca(placa) {
  const conteudo = $("#detalhe-conteudo");
  try {
    const p = await json(`/api/placas/${encodeURIComponent(placa)}`);
    const c = p.consulta;
    conteudo.innerHTML = `
      <h2><span class="placa">${esc(p.exibicao)}</span> <span class="muted">${esc(p.tipo)} · formato ${esc(p.formato)}</span></h2>
      <p>Autoseguro: ${c ? chip(c.status) + ` <span class="muted">${hora(c.atualizado_em)}</span>` : '<span class="muted">não consultado</span>'}
        <button id="btn-consultar">Consultar agora</button>
        <a href="${esc(p.link_autoseguro)}" target="_blank" rel="noopener">abrir o site</a></p>
      ${c && c.detalhe ? `<pre>${esc(c.detalhe)}</pre>` : ""}
      ${p.deteccoes[0] ? `<img alt="" src="/api/deteccoes/${p.deteccoes[0].id}/quadro.jpg">` : ""}
      <table><tr><th>Quando</th><th>Câmera</th><th>OCR</th></tr>
      ${p.deteccoes.map((d) => `<tr><td>${hora(d.criado_em)}</td><td>${esc(d.camera_id)}</td><td>${esc(d.leitura_ocr)} (${Math.round(d.confianca_ocr * 100)}%)</td></tr>`).join("")}
      </table>`;
    $("#btn-consultar").onclick = async () => {
      const r = await json(`/api/placas/${encodeURIComponent(p.placa)}/consultar?forcar=true`, { method: "POST" });
      if (r.status === "manual") window.open(r.link_autoseguro, "_blank", "noopener");
      navigator.clipboard?.writeText(p.placa).catch(() => {});
      abrirPlaca(p.placa);
    };
  } catch (e) {
    conteudo.innerHTML = `<p>${esc(e.message)}</p>`;
  }
  if (!$("#detalhe").open) $("#detalhe").showModal();
}

$("#busca").onsubmit = (ev) => {
  ev.preventDefault();
  const v = $("#busca-placa").value.trim();
  if (v) abrirPlaca(v.toUpperCase().replace(/[^A-Z0-9]/g, ""));
};

$("#analisar").onsubmit = async (ev) => {
  ev.preventDefault();
  const arq = $("#foto").files[0];
  if (!arq) return;
  const fd = new FormData();
  fd.append("imagem", arq);
  const out = $("#resultado-analise");
  out.textContent = "Processando…";
  try {
    const r = await json("/api/analisar", { method: "POST", body: fd });
    const img = await fetch("/api/analisar?anotada=true", { method: "POST", body: fd }).then((x) => x.blob());
    out.innerHTML = (r.placas.length
      ? r.placas.map((p) => `<span class="placa">${esc(p.exibicao)}</span> <span class="muted">OCR "${esc(p.leitura_ocr)}" ${Math.round(p.confianca_ocr * 100)}%</span>`).join("<br>")
      : "Nenhuma placa encontrada.") + ` <span class="muted">(${r.tempo_ms} ms)</span>`;
    const el = document.createElement("img");
    el.src = URL.createObjectURL(img);
    el.style.maxWidth = "100%";
    out.appendChild(el);
  } catch (e) {
    out.textContent = e.message;
  }
};

async function ciclo() {
  await Promise.allSettled([carregarCameras(), carregarDeteccoes(), carregarAlertas()]);
}
ciclo();
setInterval(ciclo, 3000);
