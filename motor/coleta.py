"""Coleta de dados: Fundamentus (fundamentos), Yahoo (histórico) e Banco Central (Selic/IPCA).

Tudo com biblioteca padrão. Respostas ficam em cache por dia em `cache/AAAA-MM-DD/`.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
FUNDAMENTUS = "https://www.fundamentus.com.br"


class Cache:
    def __init__(self, raiz: Path, ativo: bool = True):
        self.dir = raiz / dt.date.today().isoformat()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ativo = ativo

    def _arq(self, chave: str) -> Path:
        return self.dir / re.sub(r"[^A-Za-z0-9_.-]", "_", chave)

    def ler(self, chave: str) -> str | None:
        p = self._arq(chave)
        if self.ativo and p.exists():
            return p.read_text(encoding="utf-8")
        return None

    def gravar(self, chave: str, texto: str) -> None:
        self._arq(chave).write_text(texto, encoding="utf-8")


def baixar(url: str, encoding: str = "utf-8", tentativas: int = 3) -> str:
    erro: Exception | None = None
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode(encoding, errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            erro = e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            erro = e
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"Falha ao baixar {url}: {erro}")


def _obter(cache: Cache, chave: str, url: str, encoding: str = "utf-8") -> str:
    texto = cache.ler(chave)
    if texto is None:
        texto = baixar(url, encoding)
        cache.gravar(chave, texto)
    return texto


# --------------------------------------------------------------------------- Fundamentus

class _Tabela(HTMLParser):
    """Lê a tabela de resultados do Fundamentus (id 'resultado' ou 'tabelaResultado')."""

    def __init__(self):
        super().__init__()
        self.cabecalho: list[str] = []
        self.linhas: list[tuple[list[str], str]] = []
        self._dentro = False
        self._linha: list[str] | None = None
        self._cel: list[str] | None = None
        self._nome = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table" and a.get("id") in ("resultado", "tabelaResultado"):
            self._dentro = True
        if not self._dentro:
            return
        if tag == "tr":
            self._linha, self._nome = [], ""
        elif tag in ("td", "th"):
            self._cel = []
        elif tag == "span" and "title" in a and self._linha == []:
            self._nome = a["title"] or ""

    def handle_endtag(self, tag):
        if not self._dentro:
            return
        if tag in ("td", "th") and self._cel is not None and self._linha is not None:
            self._linha.append(" ".join("".join(self._cel).split()))
            self._cel = None
        elif tag == "tr" and self._linha:
            if not self.cabecalho:
                self.cabecalho = self._linha
            else:
                self.linhas.append((self._linha, self._nome))
            self._linha = None
        elif tag == "table":
            self._dentro = False

    def handle_data(self, data):
        if self._cel is not None:
            self._cel.append(data)


def _tabela(texto: str) -> list[dict]:
    p = _Tabela()
    p.feed(texto)
    saida = []
    for celulas, nome in p.linhas:
        d = dict(zip(p.cabecalho, celulas))
        d["_nome"] = nome
        saida.append(d)
    return saida


def fiis(cache: Cache) -> list[dict]:
    return _tabela(_obter(cache, "fund_fiis.html", f"{FUNDAMENTUS}/fii_resultado.php", "latin-1"))


def acoes(cache: Cache, setor: str | None = None) -> list[dict]:
    if setor is None:
        return _tabela(_obter(cache, "fund_acoes.html", f"{FUNDAMENTUS}/resultado.php", "latin-1"))
    url = f"{FUNDAMENTUS}/resultado.php?setor={setor}"
    return _tabela(_obter(cache, f"fund_setor_{setor}.html", url, "latin-1"))


def _pagina_detalhes(cache: Cache, ticker: str) -> str:
    url = f"{FUNDAMENTUS}/detalhes.php?papel={ticker}"
    return _obter(cache, f"fund_det_{ticker}.html", url, "latin-1")


def setor_da_acao(cache: Cache, ticker: str) -> tuple[str, str] | None:
    """Retorna (id_setor, nome_setor) a partir da página de detalhes."""
    texto = _pagina_detalhes(cache, ticker)
    m = re.search(r"resultado\.php\?setor=(\d+)[^>]*>([^<]+)<", texto)
    return (m.group(1), m.group(2).strip()) if m else None


_PAR = re.compile(r'<td class="label[^"]*">(.*?)</td>\s*<td class="data[^"]*">(.*?)</td>', re.S)


def detalhes(cache: Cache, ticker: str) -> dict[str, str]:
    """Pares rótulo -> valor (texto) da página de detalhes do Fundamentus.

    Ex.: FII -> 'Ativos', 'Patrim Líquido', 'FFO/Cota'; ação -> 'Dív. Líquida',
    'Valor da firma', 'EV / EBITDA'. Rótulos repetidos (12m x 3m) ficam com o primeiro.
    """
    texto = _pagina_detalhes(cache, ticker)
    saida: dict[str, str] = {}
    for rot, val in _PAR.findall(texto):
        nomes = re.findall(r'<span[^>]*class="txt"[^>]*>(.*?)</span>', rot, re.S)
        if not nomes:
            continue
        chave = " ".join(re.sub(r"<[^>]+>", "", nomes[-1]).split())
        valor = " ".join(re.sub(r"<[^>]+>", "", val).split())
        if chave and valor:
            saida.setdefault(chave, valor)
    return saida


# --------------------------------------------------------------------------- Yahoo

def historico(cache: Cache, ticker: str) -> dict | None:
    """5 anos de cotações semanais + todos os proventos (ajustados por desdobramentos)."""
    chave = f"yahoo_{ticker}.json"
    texto = cache.ler(chave)
    if texto is None:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}.SA"
               "?range=5y&interval=1wk&events=div")
        texto = baixar(url)
        resultado = (json.loads(texto).get("chart") or {}).get("result")
        if not resultado:
            return None
        cache.gravar(chave, texto)
    resultado = (json.loads(texto).get("chart") or {}).get("result")
    if not resultado:
        return None
    r = resultado[0]
    q = (r.get("indicators") or {}).get("quote") or [{}]
    q = q[0]
    precos = [(ts, c, h if h is not None else c)
              for ts, c, h in zip(r.get("timestamp") or [], q.get("close") or [], q.get("high") or [])
              if c is not None and c > 0]
    divs = sorted((v["date"], v["amount"])
                  for v in ((r.get("events") or {}).get("dividends") or {}).values()
                  if v.get("amount"))
    return {"precos": precos, "dividendos": divs}


# --------------------------------------------------------------------------- Banco Central

def selic_ciclo(cache: Cache) -> dict | None:
    """Selic meta hoje, há 12 meses e as mudanças do período."""
    hoje = dt.date.today()
    ini = hoje.replace(year=hoje.year - 1)
    url = ("https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados?formato=json"
           f"&dataInicial={ini:%d/%m/%Y}&dataFinal={hoje:%d/%m/%Y}")
    try:
        dados = json.loads(_obter(cache, "bcb_selic_12m.json", url))
        valores = [float(d["valor"]) / 100 for d in dados]
        return {"atual": valores[-1], "ha_12m": valores[0], "variacao": valores[-1] - valores[0]}
    except Exception:
        return None


def focus_selic(cache: Cache) -> list[tuple[str, float]]:
    """Mediana do Focus para a Selic de fim de ano (pesquisa mais recente)."""
    url = ("https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/"
           "ExpectativasMercadoAnuais?$top=12&$filter=Indicador%20eq%20'Selic'%20and%20baseCalculo%20eq%200"
           "&$orderby=Data%20desc&$format=json&$select=Data,DataReferencia,Mediana")
    try:
        linhas = json.loads(_obter(cache, "bcb_focus_selic.json", url))["value"]
        ultima = linhas[0]["Data"]
        return sorted((l["DataReferencia"], l["Mediana"] / 100) for l in linhas if l["Data"] == ultima)
    except Exception:
        return []


def indicador_bcb(cache: Cache, serie: int) -> tuple[float, str] | None:
    """Último valor de uma série SGS (432 = meta Selic, 13522 = IPCA 12m).

    Consulta por intervalo de datas (6 meses); o atalho 'ultimos/1' da API do BC às vezes
    responde 304 sem dados e fica só como alternativa.
    """
    hoje = dt.date.today()
    ini = hoje - dt.timedelta(days=180)
    urls = [f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{serie}/dados?formato=json"
            f"&dataInicial={ini:%d/%m/%Y}&dataFinal={hoje:%d/%m/%Y}",
            f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{serie}/dados/ultimos/1?formato=json"]
    for k, url in enumerate(urls):
        try:
            dado = json.loads(_obter(cache, f"bcb_{serie}_{k}.json", url))[-1]
            return float(dado["valor"]) / 100, dado["data"]
        except Exception:
            continue
    return None
