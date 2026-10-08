#!/usr/bin/env python3
"""Arma data/normas.json con el texto de las normas, artículo por artículo.

Fuente: Secretaría General del Senado de la República, Base Documental
(http://www.secretariasenado.gov.co/senado/basedoc/). La Ley 23 de 1982,
art. 41, permite reproducir la Constitución, las leyes y los decretos,
ajustándose a la edición oficial.

Uso:
    python3 scripts/armar_corpus.py                 # baja las páginas y arma el JSON
    python3 scripts/armar_corpus.py --cache carpeta # usa (y guarda) una copia local

Qué se toma: el texto de cada artículo, la ubicación (título, capítulo) y las
anotaciones cortas de la Secretaría entre < > (por ejemplo «Artículo modificado
por…»). Qué NO se toma: las notas de vigencia, concordancias, jurisprudencia y
antecedentes de las cajas desplegables de la compilación.

Solo usa la biblioteca estándar de Python 3.9 o superior.
Hecho por Estado X. Licencia MIT.
"""
import argparse
import html
import json
import re
import sys
import time
import unicodedata
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

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

RE_ART = re.compile(r"^\s*ART[IÍ]CULOS?\s+(TRANSITORIO\s+(?:FINAL|[0-9]+(?:-?[A-Z](?![a-záéíóúñ]))?)|[0-9]+(?:-?\s?[A-Z](?![a-záéíóúñ]))?)\s*[oº°]?\s*\.?\s*")
RE_ACTO = re.compile(r"ACL(\d{2})(\d{3})")
RE_SUBART = re.compile(r"^Art[ií]culo\s+([0-9]+[A-Z]?)\.\s*")
RE_EPIGRAFE = re.compile(r"^([A-ZÁÉÍÓÚÑÜ0-9][A-ZÁÉÍÓÚÑÜ0-9 ,;:()\-–“”\"'/]{2,160}?)\.\s+")


def bajar(pagina: str, cache: Path | None) -> str:
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


class Parrafos(HTMLParser):
    """Recoge los <p> del documento fuera de tablas y scripts, con sus anclas."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parrafos = []  # (clase, ancla, texto)
        self._en_p = False
        self._bloqueo = 0
        self._texto = []
        self._clase = ""
        self._ancla = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "table"):
            self._bloqueo += 1
        elif tag == "p" and not self._bloqueo:
            self._en_p, self._texto, self._clase, self._ancla = True, [], a.get("class", ""), None
        elif tag == "a" and self._en_p and a.get("name") and not a.get("href") and self._ancla is None:
            self._ancla = a["name"]
        elif tag == "br" and self._en_p:
            self._texto.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "table"):
            self._bloqueo = max(0, self._bloqueo - 1)
        elif tag == "p" and self._en_p:
            t = re.sub(r"\s+", " ", "".join(self._texto)).strip()
            if t:
                self.parrafos.append((self._clase, self._ancla, t))
            self._en_p = False

    def handle_data(self, data):
        if self._en_p and not self._bloqueo:
            self._texto.append(data)


def separar_notas(texto: str):
    texto = re.sub(r"\{([^{}]{1,80})\}", r"\1", texto)  # términos actualizados que la Secretaría marca con { }
    notas = [n.strip() for n in re.findall(r"<([^<>]{3,600})>", texto)]
    limpio = re.sub(r"<[^<>]{3,600}>", " ", texto)
    return re.sub(r"\s+", " ", limpio).strip(), notas


def normalizar(t: str) -> str:
    t = unicodedata.normalize("NFD", t.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def armar_norma(norma: dict, cache: Path | None) -> dict:
    pagina, paginas = norma["archivo"] + ".html", []
    while pagina:
        doc = bajar(pagina, cache)
        paginas.append((pagina, doc))
        m = re.search(r'class=antsig href="([^"]+)">Siguiente', doc)
        pagina = m.group(1) if m else None

    m = re.search(r"ltima actualizaci&oacute;n:\s*([^<\r\n]+)", paginas[0][1])
    actualizada = html.unescape(m.group(1)).strip() if m else ""

    articulos, niveles, actual, ultimo = [], {}, None, ""
    jerarquia = ["libro", "parte", "titulo", "capitulo"]
    for nombre_pag, doc in paginas:
        p = Parrafos()
        p.feed(doc)
        for clase, ancla, texto in p.parrafos:
            mart = None
            if not (ancla or "").startswith("Nivel") and "centrado" not in clase:
                mart = RE_ART.match(texto)
            msub = RE_SUBART.match(texto) if norma.get("subarticulos") and not ancla else None
            if mart or msub:
                m = mart or msub
                numero = re.sub(r"\s+", " ", m.group(1)).strip().lower().replace(" ", "")
                numero = numero.replace("transitorio", "transitorio ")
                acto = RE_ACTO.search(ancla or "")
                origen = ""
                if numero.startswith("transitorio") and acto:
                    origen = f"Acto Legislativo {int(acto.group(1))} de {2000 + int(acto.group(2))}"
                    numero += f" AL{int(acto.group(1))}-{2000 + int(acto.group(2))}"
                resto = texto[m.end():]
                titulo = ""
                me = RE_EPIGRAFE.match(resto)
                if me and me.group(1).strip().upper() == me.group(1).strip():
                    titulo, resto = me.group(1).strip().capitalize(), resto[me.end():]
                elif msub:
                    ms = re.match(r"^([^.<]{4,160})\.\s+", resto)
                    if ms:
                        titulo, resto = ms.group(1).strip(), resto[ms.end():]
                cuerpo, notas = separar_notas(resto)
                actual = {
                    "norma": norma["id"],
                    "numero": numero,
                    "titulo": titulo,
                    "origen": origen,
                    "ubicacion": " · ".join(niveles[k] for k in jerarquia if niveles.get(k)),
                    "texto": [cuerpo] if cuerpo else [],
                    "notas": notas,
                    "url": BASE + nombre_pag + ("#" + ancla.replace(" ", "%20") if ancla else ""),
                }
                vistos = {x["numero"] for x in articulos}
                if numero in vistos:
                    actual = None  # texto anterior de un artículo ya tomado: se omite
                elif msub or not norma.get("subarticulos"):
                    articulos.append(actual)
            elif "centrado" in clase:
                clave = normalizar(texto.split(" ")[0]).rstrip(".")
                if clave in jerarquia:
                    for k in jerarquia[jerarquia.index(clave):]:
                        niveles.pop(k, None)
                    niveles[clave] = texto.rstrip(". ")
                    ultimo = clave
                elif niveles and texto.isupper() and len(texto) < 200 and not texto.startswith("<"):
                    k = ultimo if niveles.get(ultimo) else None
                    if k and "." not in niveles[k][-3:] and len(niveles[k]) < 40:
                        niveles[k] = niveles[k] + ". " + texto.strip().rstrip(".").capitalize()
                actual = None
            elif actual is not None:
                cuerpo, notas = separar_notas(texto)
                if cuerpo:
                    actual["texto"].append(cuerpo)
                actual["notas"].extend(notas)

    for a in articulos:
        estado = " ".join(a["notas"]).lower()
        a["derogado"] = (not a["texto"]) or ("derogado" in estado and len(" ".join(a["texto"])) < 40)
        n = a["numero"]
        if n.startswith("transitorio"):
            corto = n.split(" AL")[0].replace("transitorio", "").strip()
            a["cita"] = norma["cita"].format(n="transitorio " + corto) + (f" ({a['origen']})" if a["origen"] else "")
        else:
            a["cita"] = norma["cita"].format(n=n)
    return {
        "id": norma["id"], "nombre": norma["nombre"], "corto": norma["corto"],
        "fuente": BASE + norma["archivo"] + ".html", "actualizada": actualizada,
        "articulos": articulos,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Arma data/normas.json desde la Secretaría del Senado.")
    ap.add_argument("--cache", type=Path, help="carpeta para guardar o leer las páginas descargadas")
    ap.add_argument("--salida", type=Path, default=RAIZ / "data" / "normas.json")
    args = ap.parse_args()

    normas, errores = [], []
    for n in NORMAS:
        try:
            r = armar_norma(n, args.cache)
            if len(r["articulos"]) < 5:
                raise ValueError(f"solo {len(r['articulos'])} artículos")
            normas.append(r)
            print(f"{n['id']}: {len(r['articulos'])} artículos · {r['actualizada']}")
        except Exception as e:  # una norma que falla no tumba las demás
            errores.append(f"{n['id']}: {e}")
            print(f"ERROR {n['id']}: {e}", file=sys.stderr)

    if errores and args.salida.exists():  # conserva la versión anterior de las que fallaron
        previo = {x["id"]: x for x in json.loads(args.salida.read_text(encoding="utf-8"))["normas"]}
        hechas = {x["id"] for x in normas}
        normas += [v for k, v in previo.items() if k not in hechas]

    orden = [n["id"] for n in NORMAS]
    normas.sort(key=lambda x: orden.index(x["id"]))
    salida = {
        "generado": datetime.now(timezone(timedelta(hours=-5))).strftime("%d/%m/%Y %H:%M"),
        "fuente": "Secretaría General del Senado de la República · Base Documental",
        "errores": errores,
        "normas": normas,
    }
    if args.salida.exists():  # si el texto no cambió, no se toca el archivo
        anterior = json.loads(args.salida.read_text(encoding="utf-8"))
        if anterior.get("normas") == normas and anterior.get("errores") == errores:
            print("Sin cambios en el texto de las normas.")
            return 0
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(json.dumps(salida, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("Listo:", args.salida, f"({args.salida.stat().st_size // 1024} KB)")
    return 1 if len(errores) == len(NORMAS) else 0


if __name__ == "__main__":
    sys.exit(main())
