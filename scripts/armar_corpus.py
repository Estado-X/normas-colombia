#!/usr/bin/env python3
"""Arma data/normas.json con el texto de las normas, artículo por artículo.

Fuente: Secretaría General del Senado de la República, Base Documental
(http://www.secretariasenado.gov.co/senado/basedoc/). La Ley 23 de 1982,
art. 41, permite reproducir la Constitución, las leyes y los decretos,
ajustándose a la edición oficial.

Uso:
    python3 scripts/armar_corpus.py                 # baja las páginas y arma el JSON
    python3 scripts/armar_corpus.py --cache carpeta # usa (y guarda) una copia local

Qué se toma: el texto de cada artículo y su ubicación (título, capítulo). Con las
anotaciones de la fuente se deriva, con palabras propias, una marca de estado
(vigente, derogado, inexequible; «Modificado por la Ley…»). Qué NO se toma ni se
publica: las notas de vigencia, concordancias, jurisprudencia, antecedentes y
anotaciones de la compilación, que tienen derechos de autor de su editor.

Solo usa la biblioteca estándar de Python 3.9 o superior.
Hecho por Estado X. Licencia MIT.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

BASE = "http://www.secretariasenado.gov.co/senado/basedoc/"
AGENTE = "EstadoX-normas-colombia/1.0 (+https://github.com/Estado-X/normas-colombia)"
RAIZ = Path(__file__).resolve().parent.parent

NORMAS = [
    {"id": "constitucion", "archivo": "constitucion_politica_1991", "nombre": "Constitución Política de Colombia",
     "corto": "Constitución", "cita": "art. {n} de la Constitución Política"},
    {"id": "peticion", "archivo": "ley_1755_2015", "nombre": "Ley 1755 de 2015 (derecho de petición)",
     "corto": "Derecho de petición", "cita": "art. {n} de la Ley 1437 de 2011, sustituido por la Ley 1755 de 2015",
     "subarticulos": True},
    {"id": "trabajo", "archivo": "codigo_sustantivo_trabajo", "nombre": "Código Sustantivo del Trabajo",
     "corto": "Código Sustantivo del Trabajo", "cita": "art. {n} del Código Sustantivo del Trabajo"},
    {"id": "policia", "archivo": "ley_1801_2016", "nombre": "Código Nacional de Seguridad y Convivencia Ciudadana (Ley 1801 de 2016)",
     "corto": "Código de Policía", "cita": "art. {n} de la Ley 1801 de 2016 (Código de Policía)"},
    {"id": "consumidor", "archivo": "ley_1480_2011", "nombre": "Estatuto del Consumidor (Ley 1480 de 2011)",
     "corto": "Estatuto del Consumidor", "cita": "art. {n} de la Ley 1480 de 2011 (Estatuto del Consumidor)"},
]

RE_ART = re.compile(r"^\s*ART[IÍ]CULOS?\s+<?(TRANSITORIO\s+(?:FINAL|[0-9]+(?:-?[A-Z](?![a-záéíóúñ]))?)|[0-9]+(?:-?\s?[A-Z](?![a-záéíóúñ]))?)>?\s*[oº°]?\s*\.?\s*")
RE_ART_SIN_NUMERO = re.compile(r"^\s*ART[IÍ]CULO\s*(?:TRANSITORIO|NUEVO)?\s*[A-Z]{0,4}\s*\.\s*")
RE_ACTO = re.compile(r"ACL(\d{2})(\d{3})")
RE_SUBART = re.compile(r"^Art[ií]culo\s+([0-9]+[A-Z]?)\.\s*")
RE_EPIGRAFE = re.compile(r"^([A-ZÁÉÍÓÚÑÜ0-9][A-ZÁÉÍÓÚÑÜ0-9 ,;:()\-–“”\"'/]{2,160}?)\.\s+")
NIVELES = ("libro", "parte", "primera", "segunda", "tercera", "titulo", "capitulo", "seccion")

# Párrafos que en la fuente quedaron sueltos pero son notas de la compilación
# (jurisprudencia, concordancias, legislación anterior) y no texto de la norma.
RE_NOTA = re.compile(
    r"^(sentencias? (de|del)\b.*:$|consejo de estado\b|- (consejo de estado|corte|sentencia)|corte (constitucional|suprema)[^.]{0,40}:$|"
    r"directiva (presidencial|ministerial)? ?\d|directriz \w+ \d|concepto \w+ \d|circular \w* ?\d|resoluci[oó]n \d+ de \d{4}|"
    r"nota (aclaratoria|del editor)|(inciso|art[ií]culo|numeral|literal) c[ao]rregido (seg[uú]n|por) aclaraci[oó]n|"
    r"la secretar[ií]a general de la asamblea nacional constituyente en aclaraci[oó]n|\d{4}:$)", re.I)
RE_NOTA_FUERTE = re.compile(
    r"^(nota (aclaratoria|del editor)$|directriz [A-Z]+ \d|concepto [A-Z]{3,} \d|sentencias? (de|del) .{3,60}:$|"
    r"(inciso|art[ií]culo|numeral|literal) c[ao]rregido (seg[uú]n|por) aclaraci[oó]n|nota:\s)", re.I)
RE_TEXTO_ANTERIOR = re.compile(r"^(legislaci[oó]n anterior|texto (original|anterior|con las modificaciones|modificado)\b.*:?$)", re.I)
RE_CODIGOS_SENTENCIA = re.compile(r"\b(?:C|T|SU|A)-\d{2,4}(?:-\d{2,4}|/\d{2,4})?\b")
RE_RESTO_ENLACE = re.compile(r"[a-z]{1,4}\d{3,5}_\d{2,4}\+|\+\[")
RE_RESTO_ENLACE_COMPLETO = re.compile(r"\b[a-z]{1,4}\d{3,5}_\d{2,4}\+(?:\[[^\]]{0,80}\]|\d+)?")
RE_SOLO_SIGNOS = re.compile(r"^[\s.,;:<>\d\-–]*$")
RE_NAVEGACION = re.compile(r"^(anterior|siguiente)(\s*\|\s*(anterior|siguiente))?$", re.I)


def bajar(pagina: str, cache: Optional[Path]) -> str:
    if cache:
        local = cache / pagina
        if local.exists():
            return local.read_bytes().decode("latin-1")
    req = urllib.request.Request(BASE + pagina, headers={"User-Agent": AGENTE})
    for intento in range(3):
        try:
            datos = urllib.request.urlopen(req, timeout=60).read()
            break
        except Exception:
            if intento == 2:
                raise
            time.sleep(5)
    if cache:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / pagina).write_bytes(datos)
    time.sleep(1)  # cortesía con el servidor
    return datos.decode("latin-1")


class Eventos(HTMLParser):
    """Recorre el documento y deja una lista de eventos en orden:
    ("p", clase, ancla, texto, es_navegacion) para cada párrafo fuera de tablas y scripts, y
    ("caja", nombre) para cada encabezado de las cajas de notas de la compilación."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.eventos = []
        self._en_p = False
        self._bloqueo = 0
        self._texto = []
        self._clase = ""
        self._ancla = None
        self._nav = False
        self._caja = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "table"):
            self._bloqueo += 1
        elif tag == "a" and "caja_vja_encabezado" in (a.get("class") or ""):
            self._caja = []
        elif tag == "p" and not self._bloqueo:
            self._en_p, self._texto, self._clase, self._ancla, self._nav = True, [], a.get("class", ""), None, False
        elif tag == "a" and self._en_p:
            if "antsig" in (a.get("class") or ""):
                self._nav = True
            elif a.get("name") and not a.get("href") and self._ancla is None:
                self._ancla = a["name"]
        elif tag == "br" and self._en_p:
            self._texto.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "table"):
            self._bloqueo = max(0, self._bloqueo - 1)
        elif tag == "a" and self._caja is not None:
            self.eventos.append(("caja", "".join(self._caja).strip()))
            self._caja = None
        elif tag == "p" and self._en_p:
            t = re.sub(r"\s+", " ", "".join(self._texto)).strip()
            if t:
                self.eventos.append(("p", self._clase, self._ancla, t, self._nav))
            self._en_p = False

    def handle_data(self, data):
        if self._caja is not None:
            self._caja.append(data)
        elif self._en_p and not self._bloqueo:
            self._texto.append(data)


def separar_notas(texto: str):
    texto = re.sub(r"\{([^{}]{1,80})\}", r"\1", texto)  # términos actualizados que la Secretaría marca con { }
    notas = [n.strip() for n in re.findall(r"<([^<>]{1,600})>", texto)]
    limpio = re.sub(r"<[^<>]{1,600}>", " ", texto)
    limpio = re.sub(r"^[^<>]{0,120}>\s*", "", limpio) if ">" in limpio[:122] and "<" not in limpio[:122] else limpio
    return re.sub(r"\s+", " ", limpio).strip(), notas


def es_nota_suelta(texto: str) -> bool:
    t = texto.strip()
    return bool(RE_NOTA.match(t) or len(RE_CODIGOS_SENTENCIA.findall(t)) >= 3
                or (RE_RESTO_ENLACE.search(t) and len(t) < 160) or RE_SOLO_SIGNOS.match(t))


def normalizar(t: str) -> str:
    t = unicodedata.normalize("NFD", t.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


RE_REFORMA = re.compile(
    r"(modificad[oa]|adicionad[oa]|sustituid[oa]|derogad[oa]|subrogad[oa])\s+por\s+(?:el|los|la)\s+(?:art[ií]culos?\s+[\d\s,y°o.\-]+\s+(?:del?|de la)\s+)?"
    r"((?:Acto Legislativo|Ley|Decreto(?: Ley| Legislativo)?)\s+(?:No\.\s*)?[\d.]+\s+de\s+\d{4})", re.I)


def estado_y_marcas(notas: list, texto: list):
    """Deriva, con palabras propias, el estado del artículo a partir de las anotaciones de la fuente."""
    n = " ".join(notas)
    nn = normalizar(n)
    if re.search(r"(^|\s)articulo\s+(declarado\s+)?inexequible", nn):
        estado = "inexequible"
    elif re.search(r"(^|\s)articulo\s+derogado", nn) or not texto:
        estado = "derogado"
    else:
        estado = "vigente"
    marcas = []
    for verbo, norma in RE_REFORMA.findall(n):
        v = normalizar(verbo)[:-1]
        etiqueta = {"modificad": "Modificado", "adicionad": "Adicionado", "sustituid": "Sustituido",
                    "derogad": "Derogado", "subrogad": "Subrogado"}.get(v, verbo.capitalize())
        frase = etiqueta + " por " + re.sub(r"No\.\s*", "", norma)
        if frase not in marcas:
            marcas.append(frase)
    if "condicionalmente exequible" in nn:
        marcas.append("Tiene partes declaradas condicionalmente exequibles")
    if estado == "vigente" and re.search(r"(inciso|paragrafo|numeral|literal|aparte|expresion)[^.]{0,40}(inexequible|derogad)", nn):
        marcas.append("Tiene partes derogadas o declaradas inexequibles")
    return estado, marcas[:4]


def numero_de_ancla(ancla: str) -> str:
    a = html.unescape(ancla or "").strip()
    m = re.match(r"^TRANSITORIO\s+(\d+[A-Z]?|FINAL)$", a, re.I)
    if m:
        return "transitorio " + m.group(1).lower()
    m = RE_ACTO.search(a)
    if m and "TRANSITORIO" in a.upper():
        num = re.search(r"-(\d+)$", a)
        return "transitorio " + (num.group(1) if num else "")
    return re.sub(r"\s+", "", a).lower()


def armar_norma(norma: dict, cache: Optional[Path]) -> dict:
    pagina, paginas = norma["archivo"] + ".html", []
    while pagina:
        doc = bajar(pagina, cache)
        paginas.append((pagina, doc))
        m = re.search(r'class=antsig href="([^"]+)">Siguiente', doc)
        pagina = m.group(1) if m else None

    m = re.search(r"ltima actualizaci&oacute;n:\s*([^<\r\n]+)", paginas[0][1])
    actualizada = html.unescape(m.group(1)).strip() if m else ""

    articulos, niveles, actual, ultimo = [], {}, None, ""
    descartados = []
    jerarquia = ["libro", "parte", "titulo", "capitulo"]
    vistos = set()
    for nombre_pag, doc in paginas:
        ev = Eventos()
        ev.feed(doc)
        tras_caja, botando = False, False
        for e in ev.eventos:
            if e[0] == "caja":
                tras_caja, botando = True, False
                continue
            _, clase, ancla, texto, nav = e
            if nav or RE_NAVEGACION.match(texto):
                continue
            ancla_txt = html.unescape(ancla or "")
            es_nivel = ancla_txt.startswith("Nivel") or normalizar(texto.split(" ")[0]).rstrip(".") in NIVELES
            mart = msub = None
            sin_numero = False
            if not es_nivel and "centrado" not in clase:
                mart = RE_ART.match(texto)
                if not mart and ancla and RE_ART_SIN_NUMERO.match(texto):
                    mart, sin_numero = RE_ART_SIN_NUMERO.match(texto), True
            if norma.get("subarticulos") and not ancla:
                msub = RE_SUBART.match(texto)

            if mart or msub:
                tras_caja = botando = False
                m = mart or msub
                if sin_numero or (mart and "<" in texto[:m.end()] and ancla):
                    numero = numero_de_ancla(ancla_txt)
                else:
                    numero = re.sub(r"\s+", "", m.group(1)).lower().replace("transitorio", "transitorio ")
                acto = RE_ACTO.search(ancla_txt)
                origen = ""
                if numero.startswith("transitorio") and acto:
                    origen = f"Acto Legislativo {int(acto.group(1))} de {2000 + int(acto.group(2))}"
                    numero += f" AL{int(acto.group(1))}-{2000 + int(acto.group(2))}"
                resto = texto[m.end():]
                titulo = ""
                me = RE_EPIGRAFE.match(resto)
                con_epigrafe = norma["id"] != "constitucion" or numero.startswith("transitorio")
                if con_epigrafe and me and me.group(1).strip().upper() == me.group(1).strip() and len(me.group(1).strip()) > 3:
                    titulo, resto = me.group(1).strip().capitalize(), resto[me.end():]
                elif msub:
                    ms = re.match(r"^([^.<]{4,160})\.\s+", resto)
                    if ms:
                        titulo, resto = ms.group(1).strip(), resto[ms.end():]
                cuerpo, notas = separar_notas(resto)
                if ancla:
                    url = BASE + nombre_pag + "#" + ancla.replace(" ", "%20")
                else:
                    url = BASE + nombre_pag + "#:~:text=" + urllib.parse.quote(texto[:m.end()].strip().rstrip("."))
                actual = {
                    "norma": norma["id"], "numero": numero, "titulo": titulo, "origen": origen,
                    "ubicacion": " · ".join(niveles[k] for k in jerarquia if niveles.get(k)),
                    "texto": [cuerpo] if cuerpo and not RE_SOLO_SIGNOS.match(cuerpo) else [],
                    "notas": notas, "url": url,
                }
                if numero in vistos or (norma.get("subarticulos") and not msub):
                    actual = None  # texto anterior de un artículo ya tomado, o envoltorio de la ley
                else:
                    vistos.add(numero)
                    articulos.append(actual)
            elif es_nivel or "centrado" in clase:
                clave = normalizar(texto.split(" ")[0]).rstrip(".")
                if clave in ("primera", "segunda", "tercera"):
                    clave = "parte"
                if clave in jerarquia:
                    for k in jerarquia[jerarquia.index(clave):]:
                        niveles.pop(k, None)
                    niveles[clave] = separar_notas(texto)[0].rstrip(". ")
                    ultimo = clave
                elif niveles and texto.isupper() and len(texto) < 200 and not texto.startswith("<"):
                    k = ultimo if niveles.get(ultimo) else None
                    if k and len(niveles[k]) < 40:
                        niveles[k] = niveles[k].rstrip(". ") + ". " + texto.strip().rstrip(".").capitalize()
                actual = None
            elif actual is not None:
                if botando or (tras_caja and RE_TEXTO_ANTERIOR.match(texto)):
                    botando = True  # texto anterior de la norma: hasta la siguiente caja o artículo
                    descartados.append((actual["numero"], texto[:90]))
                    continue
                if (tras_caja and es_nota_suelta(texto)) or RE_NOTA_FUERTE.match(texto):
                    descartados.append((actual["numero"], texto[:90]))
                    continue
                texto = RE_RESTO_ENLACE_COMPLETO.sub(" ", texto)
                cuerpo, notas = separar_notas(texto)
                if cuerpo and not RE_SOLO_SIGNOS.match(cuerpo):
                    actual["texto"].append(cuerpo)
                actual["notas"].extend(notas)

    for a in articulos:
        a["estado"], a["marcas"] = estado_y_marcas(a["notas"], a["texto"])
        a["derogado"] = a["estado"] != "vigente"
        del a["notas"]  # las anotaciones de la compilación no se publican; solo el estado derivado
        n = a["numero"]
        if n.startswith("transitorio"):
            corto = n.split(" AL")[0].replace("transitorio", "").strip()
            a["cita"] = norma["cita"].format(n="transitorio " + corto) + (f" ({a['origen']})" if a["origen"] else "")
        else:
            a["cita"] = norma["cita"].format(n=re.sub(r"[a-z]$", lambda x: x.group(0).upper(), n))
    return {
        "id": norma["id"], "nombre": norma["nombre"], "corto": norma["corto"],
        "fuente": BASE + norma["archivo"] + ".html", "actualizada": actualizada,
        "articulos": articulos, "_descartados": descartados,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Arma data/normas.json desde la Secretaría del Senado.")
    ap.add_argument("--cache", type=Path, help="carpeta para guardar o leer las páginas descargadas")
    ap.add_argument("--salida", type=Path, default=RAIZ / "data" / "normas.json")
    ap.add_argument("--ver-descartes", action="store_true", help="muestra los párrafos de notas que se descartaron")
    args = ap.parse_args()

    previo = {}
    if args.salida.exists():
        previo = {x["id"]: x for x in json.loads(args.salida.read_text(encoding="utf-8"))["normas"]}

    normas, errores = [], []
    for n in NORMAS:
        try:
            r = armar_norma(n, args.cache)
            descartes = r.pop("_descartados")
            total = len(r["articulos"])
            antes = len(previo.get(n["id"], {}).get("articulos", []))
            if total < 5:
                raise ValueError(f"solo {total} artículos")
            if antes and total < antes * 0.95:
                raise ValueError(f"bajó de {antes} a {total} artículos; revisar la fuente antes de publicar")
            normas.append(r)
            print(f"{n['id']}: {total} artículos ({len(descartes)} párrafos de notas descartados) · {r['actualizada']}")
            if args.ver_descartes:
                for num, t in descartes:
                    print(f"    [{num}] {t}")
        except Exception as e:
            errores.append(f"{n['id']}: {e}")
            print(f"ERROR {n['id']}: {e}", file=sys.stderr)

    if errores:  # no se publica nada a medias: queda la versión anterior completa
        print("No se actualizó el archivo por los errores anteriores.", file=sys.stderr)
        return 1

    salida = {
        "generado": datetime.now(timezone(timedelta(hours=-5))).strftime("%d/%m/%Y %H:%M"),
        "fuente": "Secretaría General del Senado de la República · Base Documental",
        "normas": normas,
    }
    if previo and [previo.get(n["id"]) for n in NORMAS] == normas:
        print("Sin cambios en el texto de las normas.")
        return 0
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(json.dumps(salida, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("Listo:", args.salida, f"({args.salida.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
