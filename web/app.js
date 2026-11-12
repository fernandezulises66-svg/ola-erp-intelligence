// OLA ERP Intelligence — demo web.
// Dos modos:
//  - live:   servida por la API (docker compose up). Usa POST /tasks, SSE /tasks/{id}/events y /approve.
//  - replay: sitio estático (ej. Vercel). Reproduce corridas reales grabadas en demo/escenarios.json
//            con scripts/grabar_demo.py: mismos pasos, mismos textos, tiempos comprimidos.

const $ = (sel) => document.querySelector(sel);
const movimientoReducido = window.matchMedia("(prefers-reduced-motion: reduce)");
const desplazamiento = () => (movimientoReducido.matches ? "auto" : "smooth");

const NODOS = {
  supervisor: "Supervisor", investigador: "Researcher", analista: "Analyst", sintetizador: "Synthesizer",
  evaluar_riesgo: "Risk check", aprobacion_humana: "Human approval", ejecutar_accion: "ERP action",
};

// Íconos de trazo propio (24×24, mismo grosor que los del HTML).
const ICONOS = {
  user: '<circle cx="12" cy="8" r="3.5"/><path d="M5 20c1.2-3.6 3.8-5.5 7-5.5s5.8 1.9 7 5.5"/>',
  agents: '<circle cx="12" cy="5" r="2.5"/><circle cx="5" cy="18" r="2.5"/><circle cx="19" cy="18" r="2.5"/><path d="M10.8 7.2 6.2 15.8M13.2 7.2l4.6 8.6M7.5 18h9"/>',
  rag: '<path d="M13 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h3"/><path d="M13 3l6 6h-5a1 1 0 0 1-1-1V3z"/><circle cx="16" cy="16" r="3"/><path d="m18.2 18.2 2.3 2.3"/>',
  steps: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.5 6H15a3 3 0 0 1 0 6H9a3 3 0 0 0 0 6h6.5"/>',
  shield: '<path d="M12 3 5 6v5c0 4.5 3 8.3 7 10 4-1.7 7-5.5 7-10V6l-7-3z"/><path d="m9 12 2 2 4-4"/>',
  memory: '<path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1L3.5 8.5"/><path d="M3.5 4v4.5H8"/><path d="M12 7.5V12l3 2"/>',
  calc: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8.5 7h7M8.5 11.5h.01M12 11.5h.01M15.5 11.5h.01M8.5 15.5h.01M12 15.5h.01M15.5 15.5h.01"/>',
  play: '<path d="M7 4.8v14.4L19 12z" fill="currentColor" stroke="none"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5M12 16h.01"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  down: '<path d="M12 5v14M6 13l6 6 6-6"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  tool: '<path d="M14.5 6.5a4 4 0 0 0-5.3 5.3L4 17l3 3 5.2-5.2a4 4 0 0 0 5.3-5.3l-2.5 2.5-2.5-2.5z"/>',
};
const icono = (nombre) => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${ICONOS[nombre] || ""}</svg>`;

// Cómo se presenta cada escenario: primero la capacidad que demuestra; la pregunta grabada va debajo.
const PRESENTACION = {
  codigo:      { titulo: "Hybrid RAG",           icono: "rag",    desc: "Exact ERP error-code retrieval and a grounded, cited answer." },
  presupuesto: { titulo: "Multi-step reasoning", icono: "steps",  desc: "Documentation retrieval plus deterministic calculations." },
  aprobacion:  { titulo: "Human approval",       icono: "shield", desc: "A sensitive ERP action pauses before anything executes." },
  seguimiento: { titulo: "Conversation memory",  icono: "memory", desc: "Follow-up reasoning using persisted conversational context." },
  caja:        { titulo: "Cash-desk rule check", icono: "calc",   desc: "A threshold from the manual plus a deterministic calculation." },
};
const ORDEN = ["codigo", "presupuesto", "aprobacion", "seguimiento"];
const presentacion = (id, respaldo) => PRESENTACION[id] || { titulo: respaldo || id, icono: "agents", desc: "" };

const EJEMPLOS_LIVE = [
  { id: "codigo", pregunta: "¿Qué significa el error VTA-410-PRES y cómo se soluciona?" },
  { id: "presupuesto", pregunta: "Hoy es 10/11/2026. Un cliente pidió el 28/10/2026 un presupuesto por una biblioteca a medida de $1.180.000 y quiere confirmarlo con un 18 % de descuento. ¿Sigue vigente?, ¿cuánto queda el precio y la seña?, ¿qué rol aprueba el descuento?" },
  { id: "aprobacion", pregunta: "Hoy es 10/11/2026. Aplicá un descuento del 18 % al presupuesto P-1042 de $1.180.000 (biblioteca a medida, emitido el 28/10/2026) y registralo en el ERP con la seña que corresponda." },
];

const estado = {
  modo: null, conversacion: null, ocupado: false, escenarios: [], grabado: null, t0: null,
  ultimo: null, previo: "start", pendiente: null, pasos: 0,
};

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

// ---------- mensajes ----------
const ROLES = {
  user: `<span class="role-mark role-user" aria-hidden="true">${icono("user")}</span>Question`,
  agent: `<span class="role-mark role-agent" aria-hidden="true">${icono("agents")}</span>Answer`,
  pending: `<span class="role-mark role-agent" aria-hidden="true">${icono("agents")}</span>Agents`,
  actionOk: `<span class="role-mark role-action" aria-hidden="true">${icono("check")}</span>ERP action`,
  actionNo: `<span class="role-mark role-action is-rejected" aria-hidden="true">${icono("x")}</span>ERP action`,
  error: `<span class="role-mark role-error" aria-hidden="true">${icono("alert")}</span>Error`,
};

function desplazarA(el, alInicio = false) {
  const cont = $("#messages");
  if (cont.scrollHeight > cont.clientHeight + 1) {
    cont.scrollTo({ top: alInicio ? el.offsetTop - 6 : cont.scrollHeight, behavior: desplazamiento() });
  } else {
    el.scrollIntoView({ behavior: desplazamiento(), block: alInicio ? "start" : "nearest" });
  }
}

function mensaje(tipo, rol, cuerpo, { desplazar = true, alInicio = false } = {}) {
  $(".chat").classList.add("has-messages");
  $("#messages").tabIndex = 0;               // región desplazable: accesible por teclado cuando tiene contenido
  const art = document.createElement("article");
  art.className = `msg msg-${tipo}`;
  art.innerHTML = `<div class="msg-role">${rol}</div><div class="msg-body">${cuerpo}</div>`;
  $("#messages").appendChild(art);
  if (desplazar) desplazarA(art, alInicio);
  return art;
}

const mensajeUsuario = (texto) => mensaje("user", ROLES.user, `<p lang="es">${escapar(texto)}</p>`);
const mensajeError = (texto) => { quitarPendiente(); return mensaje("error", ROLES.error, `<p>${escapar(texto)}</p>`); };

function mostrarPendiente() {
  quitarPendiente();
  estado.pendiente = mensaje("pending", ROLES.pending,
    `<span class="pending-line"><span class="pending-text">Working through the agent graph</span><span class="dots" aria-hidden="true"><i></i><i></i><i></i></span></span>` +
    `<a class="jump" href="#inside-title">Follow the workflow ${icono("down")}</a>`);
}
function textoPendiente(texto, { esperando = false } = {}) {
  if (!estado.pendiente) return;
  estado.pendiente.querySelector(".pending-text").textContent = texto;
  estado.pendiente.classList.toggle("is-waiting", esperando);
  const salto = estado.pendiente.querySelector(".jump");
  salto.setAttribute("href", esperando ? "#approval-title" : "#inside-title");
  salto.firstChild.textContent = esperando ? "Review the proposed action " : "Follow the workflow ";
}
function quitarPendiente() { estado.pendiente?.remove(); estado.pendiente = null; }

function pill(el, clase, texto) {
  el.className = `pill ${clase}`;
  el.textContent = texto;
}

// ---------- grafo ----------
function reiniciarGrafo() {
  document.querySelectorAll("#graph .node").forEach((n) => n.classList.remove("active", "visited", "waiting", "done-ok"));
  document.querySelectorAll("#graph .edge").forEach((e) => e.classList.remove("traversed"));
  document.querySelector('[data-node="start"]').classList.add("visited");
  estado.previo = "start";
}

// La arista se marca solo cuando dos pasos reales consecutivos la recorren.
function marcarArista(desde, hasta) {
  document.querySelector(`#graph [data-edge="${CSS.escape(`${desde}>${hasta}`)}"]`)?.classList.add("traversed");
}

function activarNodo(nodo) {
  document.querySelectorAll(".node.active").forEach((n) => { n.classList.remove("active"); n.classList.add("visited"); });
  const el = document.querySelector(`[data-node="${CSS.escape(nodo)}"]`);
  if (el) { el.classList.remove("waiting"); el.classList.add("active", "visited"); }
  if (estado.previo && estado.previo !== nodo) marcarArista(estado.previo, nodo);
  estado.previo = nodo;
}

function cerrarGrafo(final) {
  document.querySelectorAll(".node.active").forEach((n) => { n.classList.remove("active"); n.classList.add("visited"); });
  if (final) document.querySelector(`[data-node="${final}"]`)?.classList.add("done-ok");
  $("#timeline .is-current")?.classList.remove("is-current");
}

// ---------- línea de tiempo ----------
const TIMELINE_VACIA = () => estado.modo === "replay"
  ? "Each recorded step appears here as the replay runs."
  : "Steps stream here in real time (Server-Sent Events).";

function limpiarTimeline() {
  $("#timeline").innerHTML = "";
  $("#step-count").textContent = "";
  estado.t0 = null;
  estado.pasos = 0;
}

function contarRepetidos(lista) {
  const cuenta = new Map();
  for (const x of lista) cuenta.set(x, (cuenta.get(x) || 0) + 1);
  return [...cuenta].map(([x, k]) => `<span class="chip">${escapar(x)}${k > 1 ? ` ×${k}` : ""}</span>`).join("");
}

// Da formato a los detalles que ya vienen en cada paso (ruteo del supervisor, herramientas, fuentes).
// Si el texto no tiene una forma conocida se muestra tal cual.
function formatearDetalle(detalle) {
  const texto = String(detalle || "");
  const ruta = texto.match(/^→\s*(\w+)\s*\(([^)]*)\):\s*([\s\S]*)$/);
  if (ruta) {
    return `<span class="step-route">${icono("arrow")}<span>Routes to <strong>${escapar(NODOS[ruta[1]] || ruta[1])}</strong></span><span class="tag">${escapar(ruta[2])}</span></span>` +
      (ruta[3] ? `<span class="why" lang="es">${escapar(ruta[3])}</span>` : "");
  }
  const partes = texto.split(" · ");
  const campo = (prefijo) => {
    const p = partes.find((x) => x.startsWith(prefijo));
    return p ? p.slice(prefijo.length).split(",").map((s) => s.trim()).filter(Boolean) : null;
  };
  const herramientas = campo("herramientas:"), fuentes = campo("fuentes:");
  if (herramientas || fuentes) {
    let html = "";
    if (herramientas?.length) html += `<span class="kv"><span class="k">Tools</span>${contarRepetidos(herramientas)}</span>`;
    if (fuentes?.length) html += `<span class="kv"><span class="k">Retrieved</span>${fuentes.map((f) => `<span class="chip">${escapar(f)}</span>`).join("")}</span>`;
    return html;
  }
  return texto ? `<span lang="es">${escapar(texto)}</span>` : "";
}

function recortarMotivo(li) {
  const why = li.querySelector(".why");
  if (!why || why.scrollHeight <= why.clientHeight + 1) return;
  const b = document.createElement("button");
  b.type = "button";
  b.className = "step-more";
  b.textContent = "Show full reasoning";
  b.setAttribute("aria-expanded", "false");
  b.addEventListener("click", () => {
    const abierto = why.classList.toggle("is-open");
    b.textContent = abierto ? "Show less" : "Show full reasoning";
    b.setAttribute("aria-expanded", String(abierto));
  });
  why.after(b);
}

function paso(nodo, detalle, tMs) {
  const ol = $("#timeline");
  ol.querySelector(".timeline-empty")?.remove();
  ol.querySelector(".is-current")?.classList.remove("is-current");
  const li = document.createElement("li");
  li.className = `step n-${String(nodo).replace(/[^\w-]/g, "")} is-current`;
  const segundos = tMs != null ? (tMs / 1000).toFixed(1) + "s" : "";
  li.innerHTML = `<span class="step-time">${segundos}</span>` +
    `<span class="step-rail" aria-hidden="true"><span class="step-dot"></span></span>` +
    `<div class="step-body"><span class="step-node">${escapar(NODOS[nodo] || nodo)}</span><div class="step-detail">${formatearDetalle(detalle)}</div></div>`;
  ol.appendChild(li);
  recortarMotivo(li);
  ol.scrollTo({ top: ol.scrollHeight, behavior: desplazamiento() });
  estado.pasos += 1;
  $("#step-count").textContent = `${estado.pasos} step${estado.pasos === 1 ? "" : "s"}`;
  activarNodo(nodo);
}

function setOcupado(ocupado) {
  estado.ocupado = ocupado;
  $("#send").disabled = ocupado || estado.modo === "replay";
  document.querySelectorAll(".scenario").forEach((b) => (b.disabled = ocupado));
  $("#new-conv").disabled = ocupado;
}

// ---------- respuesta final ----------
function rutaHtml(recorrido) {
  const grupos = [];
  for (const n of recorrido) {
    const g = grupos[grupos.length - 1];
    if (g && g.n === n) g.k += 1; else grupos.push({ n, k: 1 });
  }
  return grupos.map((g) =>
    `<span class="route-step n-${escapar(g.n)}">${escapar(NODOS[g.n] || g.n)}${g.k > 1 ? ` <span class="x">×${g.k}</span>` : ""}</span>`
  ).join(`<span class="route-sep">${icono("arrow")}<span class="sr-only">, then</span></span>`);
}

const metaFila = (etiqueta, valor) => `<div class="meta-row"><dt>${etiqueta}</dt><dd>${valor}</dd></div>`;

function respuestaFinal(resultado, extra = {}) {
  quitarPendiente();
  const filas = [];
  if (resultado.recorrido?.length) filas.push(metaFila("Workflow", `<span class="route">${rutaHtml(resultado.recorrido)}</span>`));
  if (resultado.fuentes?.length) filas.push(metaFila("Sources", resultado.fuentes.map((f) => `<span class="chip">${icono("file")}${escapar(f)}</span>`).join("")));
  if (extra.segundos) {
    filas.push(metaFila("Execution time", `<span class="meta-time">${extra.segundos.toFixed(1)} s</span>` +
      (extra.grabado ? '<span class="meta-note">real run, replayed with compressed timing</span>' : "")));
  }
  const respuesta = mensaje("agent", ROLES.agent,
    `<div class="answer" lang="es">${markdown(resultado.respuesta_final || "(sin respuesta)")}</div>` +
    (filas.length ? `<dl class="msg-meta">${filas.join("")}</dl>` : ""), { desplazar: false });
  if (resultado.accion_resultado) {
    // La respuesta se redacta ANTES de la pausa humana; la acción se ejecuta después, solo si se aprueba.
    const ok = resultado.aprobacion?.aprobado;
    const revisor = resultado.aprobacion?.revisor;
    mensaje("action", ok ? ROLES.actionOk : ROLES.actionNo,
      `<div class="action-card ${ok ? "is-ok" : "is-rejected"}">${icono(ok ? "check" : "x")}<div>` +
      `<p class="action-title">${ok ? "Executed after human approval" : "Not executed: rejected by the reviewer"}</p>` +
      `<p class="action-result" lang="es">${escapar(resultado.accion_resultado)}</p>` +
      (revisor ? `<p class="action-reviewer">Reviewer: <span lang="es">${escapar(revisor)}</span></p>` : "") +
      `</div></div>`, { desplazar: false });
  }
  desplazarA(respuesta, true);
}

// ---------- aprobación humana ----------
function mostrarAprobacion(pendiente, { soloAprobar = false } = {}) {
  const accion = pendiente?.accion_propuesta || {};
  $("#approval-type").textContent = accion.tipo || "accion_erp";
  $("#approval-desc").textContent = accion.descripcion || "Acción sobre el ERP";
  $("#approval-reason").textContent = pendiente?.motivo || "—";
  $("#reject").disabled = soloAprobar;
  $("#approval-note").hidden = !soloAprobar;
  const panel = $("#approval");
  panel.hidden = false;
  document.querySelectorAll(".node.active").forEach((n) => n.classList.remove("active"));
  document.querySelector('[data-node="aprobacion_humana"]').classList.add("waiting", "visited");
  $("#timeline .is-current")?.classList.add("is-waiting");
  pill($("#job-pill"), "pill-wait", "Waiting for approval");
  textoPendiente("Paused: waiting for human approval", { esperando: true });
  panel.scrollIntoView({ behavior: desplazamiento(), block: "nearest" });
  panel.focus({ preventScroll: true });
  return new Promise((resolve) => {
    const decidir = (aprobado) => {
      panel.hidden = true;
      $("#timeline .is-waiting")?.classList.remove("is-waiting");
      textoPendiente(aprobado ? "Approved: resuming from the checkpoint" : "Rejected: resuming from the checkpoint");
      ($("#messages").tabIndex >= 0 ? $("#messages") : $("#graph"))?.focus?.({ preventScroll: true });
      resolve(aprobado);
    };
    $("#approve").onclick = () => decidir(true);
    $("#reject").onclick = () => decidir(false);
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
  mensajeUsuario(pregunta);
  mostrarPendiente();
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
      cerrarGrafo(null);
      mensajeError(job.error || "The job failed.");
      pill($("#job-pill"), "pill-fail", "Failed");
    }
  } catch (err) {
    cerrarGrafo(null);
    mensajeError(err.message);
    pill($("#job-pill"), "pill-fail", "Error");
  } finally {
    quitarPendiente();
    setOcupado(false);
    pintarEjemplos();
  }
}

// ---------- modo replay (corridas grabadas) ----------
async function reproducir(escenario, { liberar = true } = {}) {
  setOcupado(true);
  limpiarTimeline(); reiniciarGrafo();
  mensajeUsuario(escenario.pregunta);
  mostrarPendiente();
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
  respuestaFinal(escenario.resultado, { segundos: escenario.latencia_s, grabado: true });
  pill($("#job-pill"), "pill-done", "Done");
  estado.ultimo = escenario.id;
  if (liberar) {
    setOcupado(false);
    pintarEjemplos();
  }
}

// ---------- escenarios y arranque ----------
function tarjeta({ id, titulo, icono: ic, desc, pregunta, pie, insignia = "", listo = false, nota = false }, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = `scenario${listo ? " is-ready" : ""}${nota ? " scenario-note" : ""}`;
  b.dataset.id = id;
  const uid = `sc-${String(id).replace(/[^\w-]/g, "")}`;
  b.setAttribute("aria-label", `${estado.modo === "replay" ? "Replay" : "Run"}: ${titulo}${insignia ? ` (${insignia})` : ""}`);
  b.setAttribute("aria-describedby", [desc && `${uid}-d`, `${uid}-q`, `${uid}-f`].filter(Boolean).join(" "));
  b.innerHTML = `
    <span class="scenario-head">
      <span class="scenario-icon" aria-hidden="true">${icono(ic)}</span>
      <span class="scenario-title">${escapar(titulo)}</span>
      ${insignia ? `<span class="badge">${escapar(insignia)}</span>` : ""}
      <span class="scenario-go" aria-hidden="true">${icono("play")}</span>
    </span>
    ${desc ? `<span class="scenario-desc" id="${uid}-d">${escapar(desc)}</span>` : ""}
    <span class="scenario-q" id="${uid}-q" lang="es">${escapar(pregunta)}</span>
    <span class="scenario-foot" id="${uid}-f"><span class="foot-idle">${escapar(pie)}</span><span class="foot-playing">${estado.modo === "replay" ? "Replaying…" : "Running…"}</span></span>`;
  b.addEventListener("click", () => {
    if (estado.ocupado) return;
    b.classList.add("is-playing");
    onClick();
  });
  return b;
}

const duracion = (e) => (e.latencia_s ? ` · ${Number(e.latencia_s).toFixed(1)} s` : "");

function pintarEjemplos() {
  const cont = $("#examples");
  cont.innerHTML = "";
  if (estado.modo === "replay") {
    const porId = new Map(estado.escenarios.map((e) => [e.id, e]));
    const ordenados = [
      ...ORDEN.map((id) => porId.get(id)).filter(Boolean),
      ...estado.escenarios.filter((e) => !ORDEN.includes(e.id)),
    ];
    for (const e of ordenados) {
      const p = presentacion(e.id, e.capacidad);
      if (e.seguimiento_de) {
        // El seguimiento se grabó en la misma conversación que su pregunta original: si esa no fue la
        // última reproducida, se reproduce primero y después el seguimiento, igual que en la grabación.
        const padre = porId.get(e.seguimiento_de);
        const listo = estado.ultimo === e.seguimiento_de;
        if (!padre && !listo) continue;
        const nombrePadre = padre ? presentacion(padre.id, padre.capacidad).titulo : "";
        cont.appendChild(tarjeta({
          id: e.id, titulo: p.titulo, icono: p.icono, desc: p.desc, pregunta: e.pregunta, listo, nota: true,
          insignia: listo ? "Ready" : "",
          pie: listo ? `Follow-up in this conversation${duracion(e)}` : `Replays “${nombrePadre}” first, then this follow-up`,
        }, async () => {
          if (listo) return reproducir(e);
          nuevaConversacion(false);
          await reproducir(padre, { liberar: false });
          await sleep(1400);
          await reproducir(e);
        }));
      } else {
        cont.appendChild(tarjeta({
          id: e.id, titulo: p.titulo, icono: p.icono, desc: p.desc, pregunta: e.pregunta,
          pie: `Recorded run${duracion(e)}`,
        }, () => { nuevaConversacion(false); reproducir(e); }));
      }
    }
  } else {
    for (const e of EJEMPLOS_LIVE) {
      const p = presentacion(e.id);
      cont.appendChild(tarjeta({ id: e.id, titulo: p.titulo, icono: p.icono, desc: p.desc, pregunta: e.pregunta, pie: "Runs the full agent graph" },
        () => preguntarLive(e.pregunta)));
    }
  }
}

function textosDeModo() {
  const replay = estado.modo === "replay";
  if (replay && !estado.escenarios.length) {
    $("#scenarios-title").textContent = "The recorded scenarios could not be loaded.";
    $("#scenarios-sub").textContent = "Reload the page, or run the repository locally to use the full system.";
    return;
  }
  $("#scenarios-title").textContent = replay
    ? "Choose a recorded scenario to watch a real system execution."
    : "Try an example, or ask your own question below.";
  $("#scenarios-sub").textContent = replay
    ? "The public demo replays executions recorded from the full local system. Run the repository locally to submit arbitrary questions."
    : "Connected to the local API: every question runs the full agent graph.";
}

function nuevaConversacion(repintar = true) {
  estado.conversacion = null;
  estado.ultimo = null;
  quitarPendiente();
  $("#messages").replaceChildren();
  $("#messages").removeAttribute("tabindex");
  $(".chat").classList.remove("has-messages");
  limpiarTimeline(); reiniciarGrafo();
  $("#approval").hidden = true;
  pill($("#job-pill"), "pill-muted", "Idle");
  $("#timeline").innerHTML = `<li class="timeline-empty">${TIMELINE_VACIA()}</li>`;
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
  document.body.dataset.mode = estado.modo;
  if (estado.modo === "replay") {
    try {
      const datos = await (await fetch("./demo/escenarios.json")).json();
      estado.escenarios = datos.escenarios || [];
      estado.grabado = datos.grabado;
    } catch {
      estado.escenarios = [];
    }
    pill($("#mode-pill"), "pill-replay", "Replay · recorded runs");
    $("#mode-pill").title = "Static demo: replays real runs recorded from the full local system.";
    const fecha = estado.grabado ? new Date(estado.grabado).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" }) : "";
    $("#replay-note").textContent = `Replay mode: real runs recorded${fecha ? " on " + fecha : ""}, timing compressed. No live backend is connected.`;
  } else {
    pill($("#mode-pill"), "pill-live", "Live · connected to API");
    $("#ask-form").hidden = false;
  }
  textosDeModo();
  nuevaConversacion();
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
