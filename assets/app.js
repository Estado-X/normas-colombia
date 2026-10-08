/* Normas de Colombia · buscador de artículos en el navegador.
   Estado X · Licencia MIT. Sin servidor, sin claves, sin rastreo. */
(function () {
  "use strict";

  var NORMAS = [], ARTS = [], IDX = null, ACTUAL = [];
  var $ = function (id) { return document.getElementById(id); };
  var VACIAS = new Set(("a al algo algun alguna algunas alguno algunos ante antes aqui asi aun cada como con contra cual cuales cuando de del desde donde dos el ella ellas ello ellos en entre era es esa esas ese eso esos esta estan estas este esto estos fue ha hay hacer hace la las le les lo los mas me mi mis muy ni no nos o otra otro para pero poco por porque puede pueden puedo que quien se sea ser si sin sobre su sus tambien tan tanto te tengo tiene tienen todo todos tu un una uno unos usted y ya yo articulo articulos ley codigo constitucion norma derecho").split(" "));

  function normalizar(t) {
    return (t || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9ñ\s-]/g, " ").replace(/ñ/g, "n");
  }
  function raiz(p) {
    if (p.length <= 4) return p;
    var fines = ["aciones", "amiento", "imiento", "idades", "ciones", "mente", "acion", "idad", "cion", "ados", "idas", "idos", "adas", "ando", "iendo", "ante", "ar", "er", "ir", "es", "os", "as", "a", "o", "e", "s"];
    for (var i = 0; i < fines.length; i++) {
      var f = fines[i];
      if (p.length - f.length >= 4 && p.slice(-f.length) === f) return p.slice(0, -f.length);
    }
    return p;
  }
  function tokens(t) {
    return normalizar(t).split(/[\s-]+/).filter(function (w) { return w && !VACIAS.has(w) && (w.length > 2 || /^\d+$/.test(w)); }).map(raiz);
  }

  /* ---------- Índice BM25 ---------- */
  function construirIndice() {
    var df = new Map(), docs = [], largoTotal = 0;
    ARTS.forEach(function (a) {
      var tf = new Map();
      var cuerpo = tokens(a.texto.join(" "));
      var cabeza = tokens(a.titulo + " " + a.ubicacion);
      cuerpo.forEach(function (w) { tf.set(w, (tf.get(w) || 0) + 1); });
      cabeza.forEach(function (w) { tf.set(w, (tf.get(w) || 0) + 3); });
      var largo = cuerpo.length + cabeza.length * 3;
      largoTotal += largo;
      tf.forEach(function (_, w) { df.set(w, (df.get(w) || 0) + 1); });
      docs.push({ tf: tf, largo: largo });
    });
    return { df: df, docs: docs, prom: largoTotal / Math.max(1, docs.length), n: docs.length };
  }

  var CLAVES = null;
  function expandir(pregunta) {
    if (!CLAVES) CLAVES = Object.keys(window.SINONIMOS || {}).map(function (k) { return { k: k, r: normalizar(k).split(/\s+/).map(raiz) }; });
    var q = new Set(normalizar(pregunta).split(/[\s-]+/).filter(Boolean).map(raiz));
    var extra = [];
    CLAVES.forEach(function (c) {
      if (c.r.every(function (r) { return q.has(r); })) extra = extra.concat(window.SINONIMOS[c.k]);
    });
    return extra.join(" ");
  }

  function puntaje(consulta, pesos) {
    var k1 = 1.4, b = 0.75, res = new Float64Array(IDX.n);
    consulta.forEach(function (w, i) {
      var df = IDX.df.get(w); if (!df) return;
      var idf = Math.log(1 + (IDX.n - df + 0.5) / (df + 0.5));
      for (var d = 0; d < IDX.n; d++) {
        var f = IDX.docs[d].tf.get(w); if (!f) continue;
        res[d] += pesos[i] * idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * IDX.docs[d].largo / IDX.prom));
      }
    });
    return res;
  }

  /* Referencias directas: «art 86», «artículo 23 de la constitución», «64 del código del trabajo» */
  var ALIAS = [["constitucion", /constituc/], ["peticion", /1755|peticion/], ["trabajo", /trabajo|laboral|cst\b/], ["policia", /policia|1801|convivencia/], ["consumidor", /consumidor|1480/]];
  function referenciaDirecta(q) {
    var n = normalizar(q);
    var m = n.match(/\b(?:art(?:iculo)?s?\.?\s*)(\d+(?:-?[a-z])?)\b/);
    if (!m) return null;
    var num = m[1].replace(/^(\d+)([a-z])$/, "$1-$2");
    var norma = null;
    ALIAS.forEach(function (a) { if (a[1].test(n)) norma = norma || a[0]; });
    return ARTS.filter(function (a) { return a.numero.replace(/\s/g, "") === num && (!norma || a.norma === norma); });
  }

  function buscar(q) {
    var filtro = new Set(Array.prototype.map.call(document.querySelectorAll("#filtros input:checked"), function (i) { return i.value; }));
    var directos = (referenciaDirecta(q) || []).filter(function (a) { return filtro.has(a.norma); });
    var base = tokens(q), ext = tokens(expandir(q)).filter(function (w) { return base.indexOf(w) < 0; });
    var consulta = base.concat(ext), pesos = base.map(function () { return 1; }).concat(ext.map(function () { return 0.6; }));
    var res = [];
    if (consulta.length) {
      var p = puntaje(consulta, pesos);
      for (var i = 0; i < ARTS.length; i++) {
        if (!p[i] || !filtro.has(ARTS[i].norma)) continue;
        res.push({ a: ARTS[i], s: p[i] * (ARTS[i].derogado ? 0.15 : 1) * (ARTS[i].numero.indexOf("transitorio") === 0 ? 0.5 : 1) });
      }
      res.sort(function (x, y) { return y.s - x.s; });
    }
    var vistos = new Set(directos.map(function (a) { return a.id; }));
    var lista = directos.concat(res.map(function (r) { return r.a; }).filter(function (a) { return !vistos.has(a.id); }));
    return { lista: lista.slice(0, 6), terminos: base.concat(ext) };
  }

  /* ---------- Pintar ---------- */
  function el(tag, cls, txt) { var n = document.createElement(tag); if (cls) n.className = cls; if (txt != null) n.textContent = txt; return n; }

  function resaltar(texto, raices) {
    var frag = document.createDocumentFragment();
    texto.split(/(\s+)/).forEach(function (pal) {
      var r = raiz(normalizar(pal).replace(/[^a-z0-9]/g, ""));
      if (r.length > 2 && raices.has(r)) frag.appendChild(el("mark", null, pal));
      else frag.appendChild(document.createTextNode(pal));
    });
    return frag;
  }

  function tarjeta(a, raices, i) {
    var art = el("article", "articulo" + (a.derogado ? " articulo--derogado" : ""));
    var cab = el("header", "articulo__cab");
    cab.appendChild(el("span", "articulo__num", String(i + 1)));
    var t = el("div", "articulo__tit");
    t.appendChild(el("h3", null, cap(a.cita) + (a.titulo ? " · " + a.titulo : "")));
    if (a.ubicacion) t.appendChild(el("p", "articulo__ubic", a.ubicacion));
    cab.appendChild(t);
    if (a.derogado) cab.appendChild(el("span", "pildora pildora--gris", "Derogado"));
    art.appendChild(cab);
    var cuerpo = el("div", "articulo__texto");
    var parrafos = a.texto.length ? a.texto : ["(Sin texto vigente.)"];
    parrafos.forEach(function (p, j) {
      var par = el("p", j > 2 ? "oculto" : null);
      par.appendChild(resaltar(p, raices));
      cuerpo.appendChild(par);
    });
    art.appendChild(cuerpo);
    if (parrafos.length > 3) {
      var mas = el("button", "enlace", "Ver el artículo completo (" + parrafos.length + " párrafos)");
      mas.type = "button";
      mas.addEventListener("click", function () { cuerpo.querySelectorAll(".oculto").forEach(function (x) { x.classList.remove("oculto"); }); mas.remove(); });
      art.appendChild(mas);
    }
    if (a.notas.length) {
      var nota = el("p", "articulo__nota");
      nota.textContent = "Nota de la Secretaría del Senado: " + a.notas.slice(0, 3).join(" · ") + (a.notas.length > 3 ? " …" : "");
      art.appendChild(nota);
    }
    var pie = el("footer", "articulo__pie");
    var fuente = el("a", null, "Ver en la fuente oficial");
    fuente.href = a.url; fuente.target = "_blank"; fuente.rel = "noopener";
    pie.appendChild(fuente);
    var copiar = el("button", "enlace", "Copiar cita");
    copiar.type = "button";
    copiar.addEventListener("click", function () { copiarTexto(cap(a.cita) + ": " + a.texto.join(" ") + " (" + a.url + ")", copiar, "Copiar cita"); });
    pie.appendChild(copiar);
    art.appendChild(pie);
    return art;
  }

  function cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

  function textoParaIA(q, lista) {
    var fecha = (NORMAS[0] && NORMAS[0].actualizada || "").split(" - ")[0];
    var l = [
      "Responde mi pregunta usando SOLO los artículos que van abajo.",
      "- Cita cada afirmación con el artículo entre corchetes, por ejemplo [" + (lista[0] ? lista[0].cita : "art. 23 de la Constitución Política") + "].",
      "- Si los artículos no alcanzan para responder, dilo con claridad y no completes con otras fuentes ni con tu memoria.",
      "- Responde en español sencillo, en máximo 8 líneas, y termina recordando que esto no es asesoría legal.",
      "",
      "Mi pregunta: " + q,
      "",
      "Artículos (texto según la Secretaría del Senado de la República" + (fecha ? ", actualizado al " + fecha : "") + "):"
    ];
    lista.forEach(function (a, i) {
      l.push("");
      l.push("[" + (i + 1) + "] " + cap(a.cita) + (a.titulo ? " — " + a.titulo : "") + (a.derogado ? " (DEROGADO)" : ""));
      l.push(a.texto.join("\n"));
      l.push("Fuente: " + a.url);
    });
    return l.join("\n");
  }

  function copiarTexto(t, boton, etiqueta) {
    function listo(ok) { boton.textContent = ok ? "¡Copiado!" : "No se pudo copiar"; setTimeout(function () { boton.textContent = etiqueta; }, 1800); }
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(t).then(function () { listo(true); }, function () { listo(respaldo(t)); });
    else listo(respaldo(t));
  }
  function respaldo(t) {
    var ta = el("textarea"); ta.value = t; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    var ok = false; try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    ta.remove(); return ok;
  }

  function pintar(q) {
    var zona = $("resultados"), ia = $("ia");
    zona.innerHTML = ""; ia.hidden = true;
    if (!q.trim()) { $("estado").textContent = ""; return; }
    var r = buscar(q);
    ACTUAL = r.lista;
    if (!r.lista.length) {
      $("estado").textContent = "No encontré artículos para esa pregunta. Prueba con otras palabras o revisa los filtros.";
      return;
    }
    $("estado").textContent = "Estos " + r.lista.length + " artículos son los que más se acercan a tu pregunta. Léelos: la página no interpreta, solo busca.";
    var raices = new Set(r.terminos);
    r.lista.forEach(function (a, i) { zona.appendChild(tarjeta(a, raices, i)); });
    $("ia-texto").value = textoParaIA(q, r.lista);
    ia.hidden = false;
  }

  /* ---------- Arranque ---------- */
  function cargar() {
    var pre = window.__DATOS;
    var promesa = pre ? Promise.resolve(pre) : fetch("data/normas.json").then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); });
    promesa.then(function (d) {
      NORMAS = d.normas;
      NORMAS.forEach(function (n) {
        n.articulos.forEach(function (a, i) { a.id = n.id + ":" + i; a.nombreNorma = n.corto; ARTS.push(a); });
      });
      IDX = construirIndice();
      var cifras = $("cifras");
      cifras.textContent = ARTS.length.toLocaleString("es-CO") + " artículos de " + NORMAS.length + " normas · texto actualizado al " + (NORMAS[0].actualizada.split(" - ")[0] || "—");
      var filtros = $("filtros");
      NORMAS.forEach(function (n) {
        var lab = el("label", "filtro");
        var inp = el("input"); inp.type = "checkbox"; inp.value = n.id; inp.checked = true;
        inp.addEventListener("change", function () { pintar($("q").value); });
        lab.appendChild(inp); lab.appendChild(el("span", null, n.corto));
        filtros.appendChild(lab);
      });
      $("q").disabled = false; $("buscar").disabled = false;
      var inicial = new URLSearchParams(location.search).get("q");
      if (inicial) { $("q").value = inicial; pintar(inicial); }
    }).catch(function () {
      $("estado").textContent = "No se pudieron cargar las normas. Recarga la página.";
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    $("form").addEventListener("submit", function (e) {
      e.preventDefault();
      var q = $("q").value;
      try { history.replaceState(null, "", q ? "?q=" + encodeURIComponent(q) : location.pathname); } catch (err) { /* vista previa */ }
      pintar(q);
      var est = $("estado");
      if (q && est.getBoundingClientRect().top > window.innerHeight * 0.6) est.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    document.querySelectorAll("[data-ejemplo]").forEach(function (b) {
      b.addEventListener("click", function () { $("q").value = b.dataset.ejemplo; $("form").requestSubmit(); });
    });
    $("copiar-ia").addEventListener("click", function () { copiarTexto($("ia-texto").value, $("copiar-ia"), "Copiar el texto"); });
    cargar();
  });
})();
