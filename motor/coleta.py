"""Coleta de dados — somente fontes primárias (quem produz o dado), uma por tipo de informação.

    B3   cotações oficiais (arquivo COTAHIST), proventos, desdobramentos/bonificações,
         quantidade de ações/cotas e classificação setorial
    CVM  informes mensais e trimestrais dos FIIs; demonstrações (ITR/DFP) das empresas
    BCB  Selic, IPCA e projeções do Focus

Sites agregadores (Fundamentus, Status Invest, Yahoo...) apenas repassam esses dados, com
atraso e janelas diferentes, por isso não são usados. Biblioteca padrão apenas.

Cache: `cache/AAAA-MM-DD/` para o que muda todo dia; `cache/permanente/` para arquivos que
não mudam mais (cotações de meses fechados) ou que mudam devagar (cadastros, informes CVM).
"""
from __future__ import annotations

import base64
import csv
import datetime as dt
import gzip
import io
import json
import re
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
B3_HIST = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist"
B3_API = "https://sistemaswebb3-listados.b3.com.br"
CVM = "https://dados.cvm.gov.br/dados"


class NaoEncontrado(Exception):
    pass


class Cache:
    def __init__(self, raiz: Path, ativo: bool = True):
        self.raiz = raiz
        self.dir = raiz / dt.date.today().isoformat()
        self.perm = raiz / "permanente"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.perm.mkdir(parents=True, exist_ok=True)
        self.ativo = ativo
        self._limpar()

    def _limpar(self) -> None:
        """Apaga pastas diárias com mais de 7 dias e versões vencidas dos arquivos semanais/mensais."""
        limite = (dt.date.today() - dt.timedelta(days=7)).isoformat()
        for p in self.raiz.iterdir():
            if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name) and p.name < limite:
                for f in p.iterdir():
                    f.unlink()
                p.rmdir()
        semana, mes = _semana(), f"{dt.date.today():%Y%m}"
        for f in self.perm.iterdir():
            n = f.name
            if ((n.startswith(("cvm_", "b3_empresas_")) and semana not in n)
                    or (n.startswith("b3_det_") and not n.endswith(f"_{mes}.json"))):
                f.unlink()

    def caminho(self, chave: str, permanente: bool = False) -> Path:
        return (self.perm if permanente else self.dir) / re.sub(r"[^A-Za-z0-9_.-]", "_", chave)

    def ler(self, chave: str, permanente: bool = False) -> bytes | None:
        p = self.caminho(chave, permanente)
        if p.exists() and (self.ativo or permanente):
            return p.read_bytes()
        return None

    def gravar(self, chave: str, dados: bytes, permanente: bool = False) -> None:
        self.caminho(chave, permanente).write_bytes(dados)


def baixar(url: str, tentativas: int = 3) -> bytes:
    erro: Exception | None = None
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NaoEncontrado(url) from e
            erro = e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            erro = e
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"Falha ao baixar {url}: {erro}")


def _obter(cache: Cache, chave: str, url: str, permanente: bool = False) -> bytes:
    dados = cache.ler(chave, permanente)
    if dados is None:
        dados = baixar(url)
        cache.gravar(chave, dados, permanente)
    return dados


def _semana() -> str:
    a, s, _ = dt.date.today().isocalendar()
    return f"{a}w{s:02d}"


def num_br(txt) -> float | None:
    """'1.234,56' -> 1234.56"""
    if txt in (None, ""):
        return None
    try:
        return float(str(txt).replace(".", "").replace(",", "."))
    except ValueError:
        return None


def data_br(txt: str) -> float | None:
    """'30/10/2026' -> timestamp (meio-dia, para não escorregar de dia por fuso)."""
    try:
        d = dt.datetime.strptime(txt.strip(), "%d/%m/%Y")
    except (ValueError, AttributeError):
        return None
    return d.replace(hour=12).timestamp()


# =========================================================================== B3 — cotações

def _arquivos_cotahist(anos_hist: int) -> list[tuple[str, bool]]:
    """(nome do arquivo, fechado?) do mais antigo ao mais recente.
    Anos anteriores: anual. Ano corrente: mensal até o mês passado e diário no mês atual."""
    hoje = dt.date.today()
    nomes: list[tuple[str, bool]] = []
    for ano in range(hoje.year - anos_hist, hoje.year):
        nomes.append((f"COTAHIST_A{ano}", True))
    for mes in range(1, hoje.month):
        nomes.append((f"COTAHIST_M{mes:02d}{hoje.year}", True))
    d = hoje.replace(day=1)
    while d <= hoje:
        if d.weekday() < 5:
            nomes.append((f"COTAHIST_D{d:%d%m%Y}", d < hoje))
        d += dt.timedelta(days=1)
    return nomes


_BDI = {"02", "12", "14"}   # ações/units (lote padrão), FII, ETF


def _ler_cotahist(dados: bytes) -> dict:
    """Filtra o COTAHIST: só mercado à vista, ações/units, FIIs e ETFs."""
    saida: dict[str, dict] = {}
    with zipfile.ZipFile(io.BytesIO(dados)) as zf:
        with zf.open(zf.namelist()[0]) as fh:
            for linha in fh:
                if linha[:2] != b"01" or linha[24:27] != b"010":
                    continue
                bdi = linha[10:12].decode()
                if bdi not in _BDI:
                    continue
                tk = linha[12:24].decode("latin-1").strip()
                fat = int(linha[210:217]) or 1
                s = saida.setdefault(tk, {"isin": linha[230:242].decode(), "bdi": bdi,
                                          "nome": linha[27:39].decode("latin-1").strip(),
                                          "esp": linha[39:49].decode("latin-1").strip()[:3],
                                          "d": []})
                s["d"].append([int(linha[2:10]), int(linha[108:121]) / 100 / fat,
                               int(linha[69:82]) / 100 / fat, int(linha[170:188]) / 100])
    return saida


def cotacoes(cache: Cache, anos_hist: int = 2) -> dict[str, dict]:
    """Série diária oficial da B3 por ticker: {'isin','bdi','nome','esp','d': [[aaaammdd, fech, máx, vol]]}.
    Arquivos de períodos fechados ficam no cache permanente já filtrados (json.gz)."""
    serie: dict[str, dict] = {}
    for nome, fechado in _arquivos_cotahist(anos_hist):
        chave = f"{nome}.json.gz"
        bruto = cache.ler(chave, permanente=True)
        if bruto is not None:
            parte = json.loads(gzip.decompress(bruto))
        else:
            try:
                parte = _ler_cotahist(baixar(f"{B3_HIST}/{nome}.ZIP"))
            except NaoEncontrado:
                # mês recém-fechado ainda sem arquivo mensal: completa com os diários
                if nome.startswith("COTAHIST_M"):
                    mes, ano = int(nome[10:12]), int(nome[12:16])
                    d = dt.date(ano, mes, 1)
                    while d.month == mes:
                        if d.weekday() < 5:
                            try:
                                p2 = _ler_cotahist(baixar(f"{B3_HIST}/COTAHIST_D{d:%d%m%Y}.ZIP"))
                                _juntar(serie, p2)
                            except NaoEncontrado:
                                pass
                        d += dt.timedelta(days=1)
                continue   # feriado/fim de semana ou pregão de hoje ainda não publicado
            if fechado:
                cache.gravar(chave, gzip.compress(json.dumps(parte).encode()), permanente=True)
        _juntar(serie, parte)
    for s in serie.values():
        s["d"].sort()
    return serie


def _juntar(serie: dict, parte: dict) -> None:
    for tk, s in parte.items():
        if tk in serie:
            serie[tk]["d"].extend(s["d"])
            serie[tk]["isin"] = s["isin"]
        else:
            serie[tk] = s


def ts_de(aaaammdd: int) -> float:
    a, r = divmod(aaaammdd, 10000)
    m, d = divmod(r, 100)
    return dt.datetime(a, m, d, 12).timestamp()


# =========================================================================== B3 — cadastro e proventos

def _b3(cache: Cache, rota: str, params: dict, chave: str, permanente: bool = False):
    p = base64.b64encode(json.dumps(params, separators=(",", ":")).encode()).decode()
    return json.loads(_obter(cache, chave, f"{B3_API}/{rota}/{p}", permanente))


def empresas_b3(cache: Cache) -> dict[str, dict]:
    """issuingCompany (4 letras) -> {codeCVM, nome}. Atualizado semanalmente."""
    saida: dict[str, dict] = {}
    pag, total = 1, 1
    while pag <= total:
        r = _b3(cache, "listedCompaniesProxy/CompanyCall/GetInitialCompanies",
                {"language": "pt-br", "pageNumber": pag, "pageSize": 120},
                f"b3_empresas_{_semana()}_{pag}.json", permanente=True)
        total = r["page"]["totalPages"]
        for e in r["results"]:
            if e.get("issuingCompany") and e.get("codeCVM"):
                saida.setdefault(e["issuingCompany"], {"codeCVM": e["codeCVM"], "nome": e["companyName"]})
        pag += 1
    return saida


def detalhe_empresa(cache: Cache, code_cvm: str) -> dict:
    """Classificação setorial oficial da B3 e códigos de negociação. Atualizado mensalmente."""
    return _b3(cache, "listedCompaniesProxy/CompanyCall/GetDetail",
               {"codeCVM": code_cvm, "language": "pt-br"},
               f"b3_det_{code_cvm}_{dt.date.today():%Y%m}.json", permanente=True)


def proventos_empresa(cache: Cache, issuing: str) -> dict:
    r = _b3(cache, "listedCompaniesProxy/CompanyCall/GetListedSupplementCompany",
            {"issuingCompany": issuing, "language": "pt-br"}, f"b3_prov_{issuing}.json")
    return r[0] if isinstance(r, list) and r else (r or {})


def historico_proventos_empresa(cache: Cache, nome_pregao: str) -> list[dict]:
    """Histórico completo de proventos em dinheiro (a rota acima traz só ~12 meses).
    Cada item: typeStock (ON/PN/UNT), valueCash, corporateAction, lastDatePriorEx (data-com)."""
    saida, pag, total = [], 1, 1
    while pag <= total:
        r = _b3(cache, "listedCompaniesProxy/CompanyCall/GetListedCashDividends",
                {"language": "pt-br", "pageNumber": pag, "pageSize": 120, "tradingName": nome_pregao},
                f"b3_provhist_{re.sub(r'[^A-Za-z0-9]', '', nome_pregao)}_{pag}.json")
        total = (r.get("page") or {}).get("totalPages") or 0
        saida += r.get("results") or []
        pag += 1
    return saida


def proventos_fii(cache: Cache, cnpj: str, identificador: str) -> dict:
    return _b3(cache, "fundsProxy/fundsCall/GetListedSupplementFunds",
               {"cnpj": re.sub(r"\D", "", cnpj), "identifierFund": identificador, "typeFund": 7},
               f"b3_fii_{identificador}.json") or {}


# =========================================================================== CVM — FIIs

def _zip_cvm(cache: Cache, rel: str, nome: str) -> zipfile.ZipFile | None:
    """Arquivo de dados abertos da CVM, renovado uma vez por semana."""
    try:
        dados = _obter(cache, f"cvm_{_semana()}_{nome}", f"{CVM}/{rel}/{nome}", permanente=True)
    except NaoEncontrado:
        return None
    return zipfile.ZipFile(io.BytesIO(dados))


def _f(v) -> float:
    try:
        return float(v) if v not in ("", None) else 0.0
    except ValueError:
        return 0.0


def fiis_cvm(cache: Cache) -> dict[str, dict]:
    """ISIN -> dados oficiais do FII (último informe mensal + trimestrais), já consolidados."""
    ano = dt.date.today().year
    # 5 anos de informes mensais: o histórico de rendimentos por cota vem daqui (DY do mês × VP da cota)
    mensais = [z for z in (_zip_cvm(cache, "FII/DOC/INF_MENSAL/DADOS", f"inf_mensal_fii_{a}.zip")
                           for a in range(ano - 4, ano + 1)) if z]
    trims = [z for z in (_zip_cvm(cache, "FII/DOC/INF_TRIMESTRAL/DADOS", f"inf_trimestral_fii_{a}.zip")
                         for a in (ano - 1, ano)) if z]

    geral: dict[str, dict] = {}            # CNPJ -> registro mais recente
    for z in mensais:
        for nome in z.namelist():
            if "_geral_" in nome:
                for r in csv.DictReader(io.TextIOWrapper(z.open(nome), encoding="latin-1"), delimiter=";"):
                    c = r["CNPJ_Fundo_Classe"]
                    if c not in geral or r["Data_Referencia"] >= geral[c]["Data_Referencia"]:
                        geral[c] = r

    compl: dict[str, dict[str, dict]] = {}   # CNPJ -> data -> registro
    ap: dict[str, dict] = {}                  # CNPJ -> ativo/passivo mais recente
    for z in mensais:
        for nome in z.namelist():
            if "_complemento_" in nome or "_ativo_passivo_" in nome:
                alvo = compl if "_complemento_" in nome else None
                for r in csv.DictReader(io.TextIOWrapper(z.open(nome), encoding="latin-1"), delimiter=";"):
                    c, d = r["CNPJ_Fundo_Classe"], r["Data_Referencia"]
                    if alvo is not None:
                        alvo.setdefault(c, {})[d] = r
                    elif c not in ap or d >= ap[c]["Data_Referencia"]:
                        ap[c] = r

    resultado: dict[str, dict[str, dict]] = {}
    imoveis: dict[str, list[dict]] = {}
    contratos: dict[str, dict] = {}
    for z in trims:
        for nome in z.namelist():
            base = nome.rsplit("_", 1)[0]
            leitor = lambda: csv.DictReader(io.TextIOWrapper(z.open(nome), encoding="latin-1"), delimiter=";")
            if base.endswith("resultado_contabil_financeiro"):
                for r in leitor():
                    resultado.setdefault(r["CNPJ_Fundo_Classe"], {})[r["Data_Referencia"]] = r
            elif base.endswith("fii_imovel"):
                for r in leitor():
                    imoveis.setdefault(r["CNPJ_Fundo_Classe"], []).append(r)
            elif base.endswith("fii_complemento"):
                for r in leitor():
                    c = r["CNPJ_Fundo_Classe"]
                    if c not in contratos or r["Data_Referencia"] >= contratos[c]["Data_Referencia"]:
                        contratos[c] = r

    por_isin: dict[str, dict] = {}
    for cnpj, g in geral.items():
        isin = (g.get("Codigo_ISIN") or "").strip()
        if not isin or g.get("Mercado_Negociacao_Bolsa") != "S":
            continue
        meses = compl.get(cnpj) or {}
        ultimo = meses[max(meses)] if meses else {}
        reg = {"cnpj": cnpj, "nome": g.get("Nome_Fundo_Classe", ""), "segmento": g.get("Segmento_Atuacao", ""),
               "data_ref": g["Data_Referencia"], "pl": _f(ultimo.get("Patrimonio_Liquido")),
               "valor_ativo": _f(ultimo.get("Valor_Ativo")), "cotas": _f(ultimo.get("Cotas_Emitidas")),
               "vp_cota": _f(ultimo.get("Valor_Patrimonial_Cotas")) or None,
               "cotistas": _f(ultimo.get("Total_Numero_Cotistas"))}
        a = ap.get(cnpj) or {}
        reg["imoveis_fisicos"] = (_f(a.get("Direitos_Bens_Imoveis")) + _f(a.get("Acoes_Sociedades_Atividades_FII"))
                                  + _f(a.get("Cotas_Sociedades_Atividades_FII")))
        reg["credito"] = sum(_f(a.get(k)) for k in ("CRI", "CRI_CRA", "LCI", "LCI_LCA", "LIG", "Letras_Hipotecarias",
                                                    "Debentures", "Notas_Promissorias", "Certificados_Deposito_Valores_Mobiliarios"))
        reg["cotas_fundos"] = sum(_f(a.get(k)) for k in ("FII", "Fundo_Acoes", "FIP", "FDIC", "Outras_Cotas_FI"))
        reg["total_investido"] = _f(a.get("Total_Investido"))
        reg["divida"] = _f(a.get("Obrigacoes_Aquisicao_Imoveis")) + _f(a.get("Obrigacoes_Securitizacao_Recebiveis"))
        reg["obrig_aquisicao"] = _f(a.get("Obrigacoes_Aquisicao_Imoveis"))
        reg["securitizacao"] = _f(a.get("Obrigacoes_Securitizacao_Recebiveis"))
        reg["recorrente"] = _recorrente_12m(resultado.get(cnpj) or {}, meses)
        reg.update(_imoveis(imoveis.get(cnpj) or []))
        reg.update(_contratos(contratos.get(cnpj) or {}))
        reg["_rend"] = _rendimentos_mensais(meses)
        atual = por_isin.get(isin)
        # O mesmo ISIN pode aparecer em mais de um CNPJ (fundo e classe após a Resolução CVM 175,
        # ou erro de cadastro de outro fundo): fica o de maior patrimônio
        if not atual or (reg["pl"], reg["data_ref"]) > (atual["pl"], atual["data_ref"]):
            if atual:
                reg["_rend_outros"] = atual.get("_rend", {})
            por_isin[isin] = reg
        else:
            atual.setdefault("_rend_outros", {}).update(reg["_rend"])
    for reg in por_isin.values():
        # histórico do CNPJ escolhido, completado por outro CNPJ do mesmo ISIN (fundo antigo x classe)
        meses = dict(reg.pop("_rend_outros", {}))
        meses.update(reg.pop("_rend"))
        reg["rendimentos_mensais"] = sorted(meses.items())
    return por_isin


def _rendimentos_mensais(meses: dict[str, dict]) -> dict[float, float]:
    """{fim do mês (timestamp): rendimento por cota} = DY do mês × valor patrimonial da cota (CVM)."""
    out = {}
    for data, r in meses.items():
        dy, vp = _f(r.get("Percentual_Dividend_Yield_Mes")), _f(r.get("Valor_Patrimonial_Cotas"))
        if dy > 0 and vp > 0:
            a, m = int(data[:4]), int(data[5:7])
            fim = (dt.date(a + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
            out[dt.datetime(fim.year, fim.month, fim.day, 12).timestamp()] = dy * vp
    return out


def _recorrente_12m(trimestres: dict[str, dict], meses: dict[str, dict]) -> dict:
    """Resultado de caixa recorrente por cota nos últimos 4 trimestres (informe trimestral da CVM):
    resultado financeiro do trimestre menos ganhos com venda de imóveis e de títulos."""
    datas = sorted(trimestres)[-4:]
    if len(datas) < 4:
        return {"por_cota": None, "motivo": "menos de 4 trimestres na CVM"}
    total, ganhos, anterior = 0.0, 0.0, None
    for d in datas:
        r = trimestres[d]
        res = _f(r.get("Resultado_Trimestral_Liquido_Financeiro"))
        if anterior is not None and res and res == anterior:
            return {"por_cota": None, "motivo": f"informe de {d[:7]} repete o trimestre anterior"}
        anterior = res
        g = (_f(r.get("Resultado_Venda_TVM_Financeiro")) + _f(r.get("Receita_Venda_Investimento_Financeiro"))
             + _f(r.get("Custo_Investimento_Financeiro")) + _f(r.get("Resultado_Liquido_Estoque_Financeiro")))
        mes = d[:8] + "01"
        cotas = _f((meses.get(mes) or {}).get("Cotas_Emitidas"))
        if not cotas:
            return {"por_cota": None, "motivo": f"sem quantidade de cotas em {d[:7]}"}
        total += (res - g) / cotas
        ganhos += g / cotas
    return {"por_cota": total, "ganhos_por_cota": ganhos, "ate": datas[-1]}


def _imoveis(linhas: list[dict]) -> dict:
    if not linhas:
        return {"vacancia": None, "qtd_imoveis": None}
    ult = max(r["Data_Referencia"] for r in linhas)
    renda = [r for r in linhas if r["Data_Referencia"] == ult and r.get("Classe", "").startswith("Imóveis para renda")]
    pesos = [(_f(r.get("Percentual_Vacancia")), _f(r.get("Percentual_Receitas_FII"))) for r in renda]
    soma = sum(p for _, p in pesos)
    vac = (sum(v * p for v, p in pesos) / soma if soma > 0 else
           (sum(v for v, _ in pesos) / len(pesos) if pesos else None))
    return {"vacancia": vac, "qtd_imoveis": len(renda)}


def _contratos(r: dict) -> dict:
    if not r:
        return {"venc_12m": None, "acima_36m": None}
    pre = "Percentual_Vencimento_Receita_FII_Faixa_"
    v12 = sum(_f(r.get(pre + k)) for k in ("Ate_3Meses", "3a6Meses", "6a9Meses", "9a12Meses"))
    return {"venc_12m": v12, "acima_36m": _f(r.get(pre + "Acima_36Meses"))}


# =========================================================================== CVM — empresas

def _linhas_cias(zf, sufixo: str, codigos: set[int]) -> list[dict]:
    """Linhas do demonstrativo (consolidado; individual se a empresa não tiver consolidado)."""
    if zf is None:
        return []
    out = []
    for parte in ("_con_", "_ind_"):
        nomes = [n for n in zf.namelist() if f"{sufixo}{parte}" in n]
        if not nomes:
            continue
        ja = {int(r["CD_CVM"]) for r in out}
        for r in csv.DictReader(io.TextIOWrapper(zf.open(nomes[0]), encoding="latin-1"), delimiter=";"):
            c = int(r["CD_CVM"])
            if c in codigos and c not in ja:
                escala = 1000.0 if r.get("ESCALA_MOEDA", "").upper() == "MIL" else 1.0
                r["_v"] = _f(r["VL_CONTA"]) * escala
                r["_c"] = c
                out.append(r)
    # cada reapresentação vira uma nova VERSAO do mesmo documento: fica só a mais recente
    versao: dict[tuple, int] = {}
    for r in out:
        k = (r["_c"], r["DT_REFER"])
        versao[k] = max(versao.get(k, 0), int(r.get("VERSAO") or 0))
    return [r for r in out if int(r.get("VERSAO") or 0) == versao[(r["_c"], r["DT_REFER"])]]


_RE_LUCRO = re.compile(r"(lucro|preju[ií]zo|resultado).*per[ií]odo$", re.I)


def _conta_lucro(linhas: list[dict]) -> str | None:
    cands = sorted({r["CD_CONTA"] for r in linhas
                    if r["CD_CONTA"].count(".") == 1 and _RE_LUCRO.search(r["DS_CONTA"].strip())})
    return cands[-1] if cands else None


def cias_cvm(cache: Cache, codigos: set[int]) -> dict[int, dict]:
    """Lucro, receita, EBIT, D&A (12 meses), PL, dívida e caixa de cada empresa, pela CVM."""
    ano = dt.date.today().year
    itr = _zip_cvm(cache, "CIA_ABERTA/DOC/ITR/DADOS", f"itr_cia_aberta_{ano}.zip")
    dfp = _zip_cvm(cache, "CIA_ABERTA/DOC/DFP/DADOS", f"dfp_cia_aberta_{ano - 1}.zip")
    dfp_antigo = _zip_cvm(cache, "CIA_ABERTA/DOC/DFP/DADOS", f"dfp_cia_aberta_{ano - 3}.zip")
    dados = {"itr": {}, "dfp": {}, "dfp_ant": {}}
    for chave, zf in (("itr", itr), ("dfp", dfp), ("dfp_ant", dfp_antigo)):
        for suf in ("DRE", "BPP", "BPA", "DFC_MI"):
            for r in _linhas_cias(zf, suf, codigos):
                dados[chave].setdefault(r["_c"], {}).setdefault(suf, []).append(r)

    saida: dict[int, dict] = {}
    for c in codigos:
        i, d, a = dados["itr"].get(c, {}), dados["dfp"].get(c, {}), dados["dfp_ant"].get(c, {})
        saida[c] = _fundamentos(i, d, a)
    return saida


def _valor(linhas, conta, fim: str, ini: str | None = None, ordem: str | None = None,
           prefixo: bool = False) -> float | None:
    """conta pode ser uma lista em ordem de preferência: vale a primeira preenchida (≠ 0)."""
    if not conta:
        return None
    if isinstance(conta, (list, tuple)):
        for c in conta:
            v = _valor(linhas, c, fim, ini, ordem, prefixo)
            if v:
                return v
        return _valor(linhas, conta[-1], fim, ini, ordem, prefixo)
    vals = [r["_v"] for r in linhas if (r["CD_CONTA"].startswith(conta) if prefixo else r["CD_CONTA"] == conta)
            and r["DT_FIM_EXERC"] == fim and (ini is None or r.get("DT_INI_EXERC") == ini)
            and (ordem is None or r["ORDEM_EXERC"] == ordem)]
    return sum(vals) if vals else None


def _fundamentos(itr: dict, dfp: dict, dfp_ant: dict) -> dict:
    out: dict = {}
    dre_i, dre_d = itr.get("DRE", []), dfp.get("DRE", [])
    fim_i = max((r["DT_FIM_EXERC"] for r in dre_i if r["ORDEM_EXERC"] == "ÚLTIMO"), default=None)
    fim_d = max((r["DT_FIM_EXERC"] for r in dre_d if r["ORDEM_EXERC"] == "ÚLTIMO"), default=None)
    if not fim_d and not fim_i:
        return {"erro": "sem demonstrações na CVM"}
    usa_itr = bool(fim_i and (not fim_d or fim_i > fim_d))

    def doze_meses(linhas_i, linhas_d, conta_i, conta_d, prefixo=False, so_soma=None):
        if not usa_itr:
            return _valor(linhas_d, conta_d, fim_d, f"{fim_d[:4]}-01-01", "ÚLTIMO", prefixo)
        ytd = _valor(linhas_i, conta_i, fim_i, f"{fim_i[:4]}-01-01", "ÚLTIMO", prefixo)
        fim_ant = f"{int(fim_i[:4]) - 1}{fim_i[4:]}"
        ytd_ant = _valor(linhas_i, conta_i, fim_ant, f"{int(fim_i[:4]) - 1}-01-01", "PENÚLTIMO", prefixo)
        ano = _valor(linhas_d, conta_d, fim_d, None, "ÚLTIMO", prefixo) if fim_d else None
        if None in (ytd, ytd_ant, ano):
            return None
        return ytd + ano - ytd_ant

    out["data_ref"] = fim_i if usa_itr else fim_d
    li, ld = _conta_lucro(dre_i), _conta_lucro(dre_d)
    # parte dos controladores (conta .01) quando preenchida; senão, o lucro consolidado
    pref = lambda c: [c + ".01", c] if c else None
    out["lucro_12m"] = doze_meses(dre_i, dre_d, pref(li), pref(ld))
    out["receita_12m"] = doze_meses(dre_i, dre_d, "3.01", "3.01")
    ebit_ok = any(r["CD_CONTA"] == "3.05" and r["DS_CONTA"].startswith("Resultado Antes do Resultado Financeiro")
                  for r in dre_i + dre_d)
    out["ebit_12m"] = doze_meses(dre_i, dre_d, "3.05", "3.05") if ebit_ok else None
    # D&A pela DFC (linhas de depreciação/amortização dentro do caixa operacional)
    def contas_da(linhas):
        return sorted({r["CD_CONTA"] for r in linhas if r["CD_CONTA"].startswith("6.01.01.")
                       and re.search(r"deprecia|amortiza|exaust", r["DS_CONTA"], re.I)})
    dfc_i, dfc_d = itr.get("DFC_MI", []), dfp.get("DFC_MI", [])
    da = None
    for conta in set(contas_da(dfc_i)) | set(contas_da(dfc_d)):
        v = doze_meses(dfc_i, dfc_d, conta, conta)
        if v is not None:
            da = (da or 0) + abs(v)
    out["da_12m"] = da
    # balanço do fim do período mais recente
    bpp = (itr if usa_itr else dfp).get("BPP", [])
    bpa = (itr if usa_itr else dfp).get("BPA", [])
    fim = out["data_ref"]
    pl_c = next((r["CD_CONTA"] for r in bpp if r["CD_CONTA"].count(".") == 1
                 and r["DS_CONTA"].strip().lower().startswith("patrimônio líquido")), None)
    out["pl"] = _valor(bpp, pl_c, fim, None, "ÚLTIMO")
    emprest = [r["CD_CONTA"] for r in bpp if r["CD_CONTA"] in ("2.01.04", "2.02.01")
               and "mprést" in r["DS_CONTA"]]
    out["divida_bruta"] = (sum(_valor(bpp, k, fim, None, "ÚLTIMO") or 0 for k in set(emprest))
                           if emprest else None)
    caixa = [k for k in ("1.01.01", "1.01.02") if any(r["CD_CONTA"] == k for r in bpa)]
    out["caixa"] = sum(_valor(bpa, k, fim, None, "ÚLTIMO") or 0 for k in caixa) if caixa else None
    # receita de 3 anos antes (crescimento)
    dre_a = dfp_ant.get("DRE", [])
    fim_a = min((r["DT_FIM_EXERC"] for r in dre_a), default=None)
    rec_ant = _valor(dre_a, "3.01", fim_a, None, "PENÚLTIMO") if fim_a else None
    rec_ult = _valor(dre_d, "3.01", fim_d, None, "ÚLTIMO") if fim_d else None
    out["cresc_receita_3a"] = ((rec_ult / rec_ant) ** (1 / 3) - 1
                               if rec_ult and rec_ant and rec_ult > 0 and rec_ant > 0 else None)
    return out


# =========================================================================== Banco Central

def _bcb(cache: Cache, chave: str, url: str):
    return json.loads(_obter(cache, chave, url))


def selic_ciclo(cache: Cache) -> dict | None:
    hoje = dt.date.today()
    ini = hoje.replace(year=hoje.year - 1)
    url = ("https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados?formato=json"
           f"&dataInicial={ini:%d/%m/%Y}&dataFinal={hoje:%d/%m/%Y}")
    try:
        valores = [float(d["valor"]) / 100 for d in _bcb(cache, "bcb_selic_12m.json", url)]
        return {"atual": valores[-1], "ha_12m": valores[0], "variacao": valores[-1] - valores[0]}
    except Exception:
        return None


def ipca_12m(cache: Cache) -> tuple[float, str] | None:
    hoje = dt.date.today()
    ini = hoje - dt.timedelta(days=180)
    url = ("https://api.bcb.gov.br/dados/serie/bcdata.sgs.13522/dados?formato=json"
           f"&dataInicial={ini:%d/%m/%Y}&dataFinal={hoje:%d/%m/%Y}")
    try:
        d = _bcb(cache, "bcb_ipca12m.json", url)[-1]
        return float(d["valor"]) / 100, d["data"]
    except Exception:
        return None


def focus(cache: Cache, indicador: str) -> list[tuple[str, float]]:
    """Mediana do Focus (pesquisa mais recente) para o fim de cada ano. indicador: 'Selic' | 'IPCA'."""
    url = ("https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/"
           f"ExpectativasMercadoAnuais?$top=12&$filter=Indicador%20eq%20'{indicador}'%20and%20baseCalculo%20eq%200"
           "&$orderby=Data%20desc&$format=json&$select=Data,DataReferencia,Mediana")
    try:
        linhas = _bcb(cache, f"bcb_focus_{indicador}.json", url)["value"]
        ultima = linhas[0]["Data"]
        return sorted((l["DataReferencia"], l["Mediana"] / 100) for l in linhas if l["Data"] == ultima)
    except Exception:
        return []
