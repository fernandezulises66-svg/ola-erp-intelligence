// ERP Intelligence System — demo web.
// Dos modos:
//  - live:   servida por la API (docker compose up). Usa POST /tasks, SSE /tasks/{id}/events y /approve.
//  - replay: sitio estático (ej. Vercel). Reproduce corridas reales grabadas en demo/escenarios.json
//            con scripts/grabar_demo.py: mismos pasos, mismos textos, tiempos comprimidos.

const $ = (sel) => document.querySelector(sel);
const NODOS = {
  supervisor: "Supervisor", investigador: "Researcher", analista: "Analyst", sintetizador: "Synthesizer",
  evaluar_riesgo: "Risk check", aprobacion_humana: "Human approval", ejecutar_accion: "ERP action",
};
const EJEMPLOS_LIVE = [
  { tag: "Hybrid RAG", pregunta: "¿Qué significa el error VTA-410-PRES y cómo se soluciona?" },
  { tag: "Multi-step", pregunta: "Hoy es 10/11/2026. Un cliente pidió el 28/10/2026 un presupuesto por una biblioteca a medida de $1.180.000 y quiere confirmarlo con un 18 % de descuento. ¿Sigue vigente?, ¿cuánto queda el precio y la seña?, ¿qué rol aprueba el descuento?" },
  { tag: "Human approval", pregunta: "Hoy es 10/11/2026. Aplicá un descuento del 18 % al presupuesto P-1042 de $1.180.000 (biblioteca a medida, emitido el 28/10/2026) y registralo en el ERP con la seña que corresponda." },
];

const estado = { modo: null, conversacion: null, ocupado: false, escenarios: [], grabado: null, t0: null };

// ---------- utilidades de interfaz ----------
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function escapar(texto) {
  return String(texto).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function inline(texto) {
  return escapar(texto)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

// Markdown mínimo y seguro (se escapa todo antes de formatear): párrafos, listas (con numeración real
// aunque el modelo deje líneas en blanco entre ítems), sub-listas indentadas, negrita y código.
function markdown(texto) {
  const lineas = String(texto).replace(/\r/g, "").split("\n");
  let html = "", lista = null, li = false, sub = false;
  const cerrarSub = () => { if (sub) { html += "</ul>"; sub = false; } };
  const cerrarLi = () => { cerrarSub(); if (li) { html += "</li>"; li = false; } };
  const cerrar = () => { cerrarLi(); if (lista) { html += `</${lista}>`; lista = null; } };
  const item = (contenido) => { cerrarLi(); html += `<li>${inline(contenido)}`; li = true; };
  for (const cruda of lineas) {
    const linea = cruda.trim();
    const indent = cruda.length - cruda.trimStart().length;
    const ul = linea.match(/^[-*]\s+(.*)/), ol = linea.match(/^(\d+)[.)]\s+(.*)/);
    if (ul && lista === "ol" && li && indent >= 2) {    // sub-viñeta dentro de un ítem numerado
      if (!sub) { html += '<ul class="sub">'; sub = true; }
      html += `<li>${inline(ul[1])}</li>`;
    } else if (ol) {
      if (lista !== "ol") { cerrar(); html += `<ol start="${Number(ol[1])}">`; lista = "ol"; }
      item(ol[2]);
    } else if (ul) {
      if (lista !== "ul") { cerrar(); html += "<ul>"; lista = "ul"; }
      item(ul[1]);
    } else if (!linea) {
      cerrarSub();                                     // una línea en blanco no corta la numeración
    } else if (li && indent >= 2) {
      cerrarSub();
      html += `<p class="cont">${inline(linea)}</p>`;  // continuación del ítem
    } else {
      cerrar();
      html += `<p>${inline(linea)}</p>`;
    }
  }
  cerrar();
  return html;
}

function mensaje(tipo, html) {
  $("#empty-state")?.remove();
  const div = document.createElement("div");
  div.className = `msg msg-${tipo}`;
  div.innerHTML = html;
  $("#messages").appendChild(div);
  div.scrollIntoView({ behavior: "smooth", block: "end" });
  return div;
}

function pill(el, clase, texto) {
  el.className = `pill ${clase}`;
  el.textContent = texto;
}

function reiniciarGrafo() {
  document.querySelectorAll(".node").forEach((n) => n.classList.remove("active", "visited", "waiting", "done-ok"));
  document.querySelector('[data-node="start"]').classList.add("visited");
}

function activarNodo(nodo) {
  document.querySelectorAll(".node.active").forEach((n) => { n.classList.remove("active"); n.classList.add("visited"); });
  const el = document.querySelector(`[data-node="${nodo}"]`);
  if (el) { el.classList.remove("waiting"); el.classList.add("active", "visited"); }
}

function cerrarGrafo(final) {
  document.querySelectorAll(".node.active").forEach((n) => { n.classList.remove("active"); n.classList.add("visited"); });
  if (final) document.querySelector(`[data-node="${final}"]`)?.classList.add("done-ok");
}

function limpiarTimeline() {
  $("#timeline").innerHTML = "";
  estado.t0 = null;
}

function paso(nodo, detalle, tMs) {
  const ol = $("#timeline");
  ol.querySelector(".timeline-empty")?.remove();
  const li = document.createElement("li");
  const segundos = tMs != null ? (tMs / 1000).toFixed(1) + "s" : "";
  li.innerHTML = `<span class="t">${segundos}</span><span class="n n-${escapar(nodo)}">${escapar(NODOS[nodo] || nodo)}</span><span class="d">${escapar(detalle || "")}</span>`;
  ol.appendChild(li);
  li.scrollIntoView({ block: "nearest" });
  activarNodo(nodo);
}

function setOcupado(ocupado) {
  estado.ocupado = ocupado;
  $("#send").disabled = ocupado || estado.modo === "replay";
  document.querySelectorAll(".example").forEach((b) => (b.disabled = ocupado));
  $("#new-conv").disabled = ocupado;
}

function respuestaFinal(resultado, extra = {}) {
  const chips = [];
  if (resultado.recorrido?.length) chips.push(`<span class="chip chip-accent">${escapar(resultado.recorrido.map((r) => NODOS[r] || r).join(" → "))}</span>`);
  for (const f of resultado.fuentes || []) chips.push(`<span class="chip">${escapar(f)}</span>`);
  if (extra.segundos) chips.push(`<span class="chip">${extra.segundos.toFixed(1)} s</span>`);
  mensaje("agent", markdown(resultado.respuesta_final || "(sin respuesta)") + (chips.length ? `<div class="msg-meta">${chips.join("")}</div>` : ""));
  if (resultado.accion_resultado) {
    // La respuesta se redacta ANTES de la pausa humana; la acción se ejecuta después, solo si se aprueba.
    const ok = resultado.aprobacion?.aprobado;
    mensaje("agent", `<p class="msg-action-title">${ok ? "Executed after human approval" : "Not executed: rejected by the reviewer"}</p>` +
      `<div class="msg-meta"><span class="chip ${ok ? "chip-good" : ""}">${escapar(resultado.accion_resultado)}</span></div>`);
  }
}

function mostrarAprobacion(pendiente, { soloAprobar = false } = {}) {
  const accion = pendiente?.accion_propuesta || {};
  $("#approval-desc").textContent = `${accion.descripcion || "Acción sobre el ERP"} — ${pendiente?.motivo || ""}`;
  $("#reject").disabled = soloAprobar;
  $("#reject").title = soloAprobar ? "The recorded run was approved; run locally to try a rejection." : "";
  $("#approval").hidden = false;
  document.querySelectorAll(".node.active").forEach((n) => n.classList.remove("active"));
  document.querySelector('[data-node="aprobacion_humana"]').classList.add("waiting", "visited");
  pill($("#job-pill"), "pill-wait", "Waiting for approval");
  return new Promise((resolve) => {
    $("#approve").onclick = () => { $("#approval").hidden = true; resolve(true); };
    $("#reject").onclick = () => { $("#approval").hidden = true; resolve(false); };
  });
}

// ---------- modo live (API real) ----------
function escucharJob(jobId, vistos) {
  return new Promise((resolve) => {
    const es = new EventSource(`/tasks/${jobId}/events`);
    let n = 0;
    es.addEventListener("paso", (e) => {
      n += 1;
      if (n <= vistos) return;                      // al reanudar, el stream vuelve a mandar los pasos anteriores
      const p = JSON.parse(e.data);
      const t = Date.parse(p.at);
      if (estado.t0 == null) estado.t0 = t;
      paso(p.nodo, p.detalle, t - estado.t0);
    });
    es.addEventListener("estado", () => { es.close(); resolve(n); });
    es.onerror = () => { es.close(); resolve(n); };
  });
}

async function preguntarLive(pregunta) {
  setOcupado(true);
  limpiarTimeline(); reiniciarGrafo();
  mensaje("user", `<p>${escapar(pregunta)}</p>`);
  pill($("#job-pill"), "pill-run", "Running");
  const inicio = performance.now();
  try {
    const r = await fetch("/tasks", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pregunta, ...(estado.conversacion ? { conversation_id: estado.conversacion } : {}) }) });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const tarea = await r.json();
    estado.conversacion = tarea.conversation_id;
    let vistos = await escucharJob(tarea.job_id, 0);
    let job = await (await fetch(`/tasks/${tarea.job_id}`)).json();
    while (job.status === "WAITING_APPROVAL") {
      const aprobado = await mostrarAprobacion(job.aprobacion_pendiente);
      await fetch(`/tasks/${tarea.job_id}/approve`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ aprobado, revisor: "manager (demo)", comentario: "web demo" }) });
      pill($("#job-pill"), "pill-run", "Resuming");
      vistos = await escucharJob(tarea.job_id, vistos);
      job = await (await fetch(`/tasks/${tarea.job_id}`)).json();
    }
    if (job.status === "DONE") {
      cerrarGrafo(job.resultado?.accion_resultado?.includes("registrada") ? "ejecutar_accion" : "sintetizador");
      respuestaFinal(job.resultado, { segundos: (performance.now() - inicio) / 1000 });
      pill($("#job-pill"), "pill-done", "Done");
    } else {
      mensaje("agent msg-error", `<p>${escapar(job.error || "The job failed.")}</p>`);
      pill($("#job-pill"), "pill-fail", "Failed");
    }
  } catch (err) {
    mensaje("agent msg-error", `<p>${escapar(err.message)}</p>`);
    pill($("#job-pill"), "pill-fail", "Error");
  } finally {
    setOcupado(false);
  }
}

// ---------- modo replay (corridas grabadas) ----------
async function reproducir(escenario) {
  setOcupado(true);
  limpiarTimeline(); reiniciarGrafo();
  mensaje("user", `<p>${escapar(escenario.pregunta)}</p>`);
  pill($("#job-pill"), "pill-run", "Replaying");
  let offset = 0;
  for (const fase of escenario.fases) {
    let previo = 0;
    for (const ev of fase.eventos) {
      await sleep(Math.min(1800, Math.max(350, (ev.t - previo) * 0.25)));   // tiempos reales comprimidos
      previo = ev.t;
      paso(ev.nodo, ev.detalle, offset + ev.t);
    }
    offset += fase.duracion_ms || previo;
    if (fase.estado === "WAITING_APPROVAL") {
      await mostrarAprobacion(fase.aprobacion_pendiente, { soloAprobar: true });
      pill($("#job-pill"), "pill-run", "Resuming");
    }
  }
  const final = escenario.resultado?.accion_resultado?.includes("registrada") ? "ejecutar_accion" : "sintetizador";
  cerrarGrafo(final);
  respuestaFinal(escenario.resultado, { segundos: escenario.latencia_s });
  pill($("#job-pill"), "pill-done", "Done");
  setOcupado(false);
  pintarEjemplos(escenario.id);
}

// ---------- ejemplos y arranque ----------
function boton(tag, texto, onClick, extraClase = "") {
  const b = document.createElement("button");
  b.type = "button";
  b.className = `example ${extraClase}`;
  b.innerHTML = `<span class="example-tag">${escapar(tag)}</span><span>${escapar(texto.length > 120 ? texto.slice(0, 117) + "…" : texto)}</span>`;
  b.addEventListener("click", onClick);
  return b;
}

function pintarEjemplos(ultimo = null) {
  const cont = $("#examples");
  cont.innerHTML = "";
  if (estado.modo === "replay") {
    const seguimiento = estado.escenarios.find((e) => e.seguimiento_de && e.seguimiento_de === ultimo);
    if (seguimiento) cont.appendChild(boton("Follow-up · memory", seguimiento.pregunta, () => reproducir(seguimiento), "example-follow"));
    for (const e of estado.escenarios.filter((x) => !x.seguimiento_de)) {
      cont.appendChild(boton(e.capacidad, e.pregunta, () => { nuevaConversacion(false); reproducir(e); }));
    }
  } else {
    for (const e of EJEMPLOS_LIVE) cont.appendChild(boton(e.tag, e.pregunta, () => preguntarLive(e.pregunta)));
  }
}

function nuevaConversacion(repintar = true) {
  estado.conversacion = null;
  $("#messages").innerHTML = "";
  limpiarTimeline(); reiniciarGrafo();
  $("#approval").hidden = true;
  pill($("#job-pill"), "pill-muted", "Idle");
  $("#timeline").innerHTML = '<li class="timeline-empty">Steps stream here in real time (Server-Sent Events).</li>';
  if (repintar) pintarEjemplos();
}

async function detectarModo() {
  if (new URLSearchParams(location.search).get("mode") === "replay") return "replay";
  try {
    const ctrl = new AbortController();
    setTimeout(() => ctrl.abort(), 2500);
    const r = await fetch("/health", { signal: ctrl.signal });
    const h = await r.json();
    return typeof h.redis === "boolean" ? "live" : "replay";
  } catch {
    return "replay";
  }
}

async function init() {
  reiniciarGrafo();
  estado.modo = await detectarModo();
  if (estado.modo === "replay") {
    try {
      const datos = await (await fetch("./demo/escenarios.json")).json();
      estado.escenarios = datos.escenarios || [];
      estado.grabado = datos.grabado;
    } catch {
      estado.escenarios = [];
    }
    pill($("#mode-pill"), "pill-replay", "Replay · recorded runs");
    $("#mode-pill").title = "Static demo: replays real runs recorded from the live system.";
    $("#question").readOnly = true;
    $("#question").placeholder = "Replay mode: pick a recorded question below. Run the repo locally for live questions.";
    const fecha = estado.grabado ? new Date(estado.grabado).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" }) : "";
    $("#replay-note").textContent = `Replay mode: real runs recorded${fecha ? " on " + fecha : ""}, timing compressed.`;
  } else {
    pill($("#mode-pill"), "pill-live", "Live · connected to API");
  }
  pintarEjemplos();
  setOcupado(false);

  $("#ask-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("#question").value.trim();
    if (estado.modo !== "live" || estado.ocupado || q.length < 5) return;
    $("#question").value = "";
    preguntarLive(q);
  });
  $("#question").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#ask-form").requestSubmit(); }
  });
  $("#new-conv").addEventListener("click", () => nuevaConversacion());
}

init();
