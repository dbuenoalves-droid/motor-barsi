"""Motor de Oportunidades — Barsi + Axiomas de Zurique + Kiyosaki.

Varre FIIs de TIJOLO e ações de empresas BESST em busca de bons pagadores de proventos
cuja cota caiu por debandada do mercado, não por piora da renda.

Fontes (somente primárias): B3 (cotações, proventos, cadastro), CVM (informes de FII e
demonstrações das empresas) e Banco Central (Selic, IPCA, Focus).

Uso:
    python analisar.py                     # varredura completa
    python analisar.py --abrir             # varre e abre o relatório no navegador
    python analisar.py --tickers TRXF11,BBAS3,HGLG11
    python analisar.py --so fiis
    python analisar.py --sem-cache         # ignora o cache do dia (proventos, juros) e baixa de novo
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import sys
import time
import tomllib
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from motor import analise, coleta, relatorio, sugestoes

RAIZ = Path(__file__).resolve().parent
ESPECIES_ACAO = ("ON", "PN", "PNA", "PNB", "UNT")


# --------------------------------------------------------------------------- séries B3

def pregoes(cot: dict) -> list[int]:
    """Datas de pregão (pela série do BOVA11, que negocia todo dia)."""
    return [x[0] for x in (cot.get("BOVA11") or {}).get("d", [])]


def liquidez(serie: dict, ultimos: list[int]) -> float:
    """Volume financeiro médio por pregão nos últimos ~2 meses (dias sem negócio contam zero)."""
    alvo = set(ultimos)
    return sum(v for d, c, h, v in serie["d"] if d in alvo) / max(len(ultimos), 1)


def eventos_de(prov: dict, isins: set[str]) -> list[tuple[float, float]]:
    """(data-com, multiplicador de quantidade) de desdobramentos, bonificações e grupamentos."""
    vistos, out = set(), []
    for e in prov.get("stockDividends") or []:
        rot = (e.get("label") or "").upper()
        if e.get("isinCode") not in isins or not any(x in rot for x in ("DESDOBR", "BONIFIC", "GRUPAM")):
            continue
        t, fat = coleta.data_br(e.get("lastDatePrior", "")), coleta.num_br(e.get("factor"))
        if not t or not fat:
            continue
        mult = fat if "GRUPAM" in rot else 1 + fat / 100
        if (t, round(mult, 6)) not in vistos:
            vistos.add((t, round(mult, 6)))
            out.append((t, mult))
    return out


def fator_ajuste(t: float, eventos: list[tuple[float, float]]) -> float:
    """Divisor para trazer um valor da data t para a base de ações/cotas de hoje."""
    f = 1.0
    for te, mult in eventos:
        if t <= te:
            f *= mult
    return f


def precos_ajustados(serie: dict, eventos) -> list[tuple[float, float, float]]:
    out = []
    for d, c, h, v in serie["d"]:
        t = coleta.ts_de(d)
        f = fator_ajuste(t, eventos)
        out.append((t, c / f, h / f))
    return out


def proventos_de(prov: dict, isin: str, eventos, ir_jcp: float, fii: bool):
    """(líquidos [(data-com, valor)], brutos [(data-com, valor)], fração em JCP), ajustados por
    desdobramentos. Líquido = o que cai na conta: dividendo e rendimento de FII isentos, JCP com IR
    retido na fonte no próprio pagamento. Amortização de cotas não é renda."""
    vistos, out, brutos, bruto, jcp = set(), [], [], 0.0, 0.0
    agora = time.time()
    for c in prov.get("cashDividends") or []:
        if c.get("isinCode") != isin:
            continue
        rot = (c.get("label") or "").upper()
        t, v = coleta.data_br(c.get("lastDatePrior", "")), coleta.num_br(c.get("rate"))
        if not t or not v or v <= 0:
            continue
        chave = (t, round(v, 8), rot)
        if chave in vistos:
            continue
        vistos.add(chave)
        if fii:
            if "RENDIMENTO" not in rot and "DIVIDENDO" not in rot:
                continue
            liquido = v
        elif "JRS" in rot or "JUROS" in rot:
            liquido = v * (1 - ir_jcp)
            if t > agora - analise.ANO:
                jcp += v
        elif "DIVIDENDO" in rot or "RENDIMENTO" in rot:
            liquido = v
        else:
            continue
        if t > agora - analise.ANO:
            bruto += v
        f = fator_ajuste(t, eventos)
        out.append((t, liquido / f))
        brutos.append((t, v / f))
    out.sort()
    brutos.sort()
    return out, brutos, (jcp / bruto if bruto else 0.0)


def proventos_totais_12m(prov: dict, eventos) -> float | None:
    """Total pago pela empresa em 12 meses (ON × qtd ON + PN × qtd PN), para o payout."""
    q_on, q_pn = coleta.num_br(prov.get("numberCommonShares")), coleta.num_br(prov.get("numberPreferredShares"))
    agora, total, vistos = time.time(), 0.0, set()
    for c in prov.get("cashDividends") or []:
        isin = c.get("isinCode") or ""
        classe = isin[6:11]
        t, v = coleta.data_br(c.get("lastDatePrior", "")), coleta.num_br(c.get("rate"))
        if not t or not v or t <= agora - analise.ANO or t > agora or (isin, t, v, c.get("label")) in vistos:
            continue
        vistos.add((isin, t, v, c.get("label")))
        v /= fator_ajuste(t, eventos)
        if classe == "ACNOR" and q_on:
            total += v * q_on
        elif classe.startswith("ACNP") and q_pn:
            total += v * q_pn
    return total or None


def listado(txt: str | None) -> float | None:
    return coleta.data_br(txt) if txt else None


def em_paralelo(func, itens, workers):
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(func, itens))


# --------------------------------------------------------------------------- FIIs

def fora_do_escopo_fii(tk: str, cvm: dict | None, cfg: dict) -> str | None:
    f = cfg["fiis"]
    manual = (f.get("manual") or {}).get(tk) or {}
    if not cvm:
        return "sem informe na CVM"
    ti = cvm.get("total_investido") or 0
    if not ti or cvm.get("imoveis_fisicos", 0) / ti < f["tijolo_fisico_minimo"]:
        return "não é tijolo"
    nome = cvm.get("nome", "").upper()
    if re.search(r"\bFOF\b|FUNDOS? DE FUNDOS", nome):
        return "fundo de fundos"
    if cvm.get("segmento") in f["segmentos_excluidos"] and not manual.get("segmento"):
        return "segmento"
    if any(p in nome for p in f["palavras_excluidas"]) or manual.get("excluir"):
        return "segmento"
    return None


def montar_fii(cache, cfg, cot, tk, cvm, ult) -> dict:
    s = cot[tk]
    d = {"ticker": tk, "nome": s["nome"], "cvm": cvm, "liquidez": liquidez(s, ult),
         "preco": s["d"][-1][1] if s["d"] and s["d"][-1][0] >= ult[0] else None}
    try:
        prov = coleta.proventos_fii(cache, cvm["cnpj"], tk[:4]) if cvm else {}
    except Exception as e:
        print(f"  aviso: proventos de {tk} indisponíveis na B3 ({e})", file=sys.stderr)
        prov = {}
    ev = eventos_de(prov, {s["isin"]})
    d["precos"] = precos_ajustados(s, ev)
    recentes, _, _ = proventos_de(prov, s["isin"], ev, 0.0, fii=True)
    # B3 traz ~12 meses com data-com exata; antes disso, o rendimento mensal oficial do informe da CVM
    inicio_b3 = recentes[0][0] if recentes else time.time()
    antigos = [(t, v / fator_ajuste(t, ev)) for t, v in (cvm or {}).get("rendimentos_mensais", [])
               if t < inicio_b3 - 10 * analise.DIA]
    d["dividendos"] = antigos + recentes
    d["listado_desde"] = listado(prov.get("quotedPer"))
    return d


# --------------------------------------------------------------------------- ações

def setor_besst(cfg, classificacao: str | None) -> str | None:
    for prefixo, rotulo in cfg["acoes"]["setores"].items():
        if classificacao and classificacao.startswith(prefixo):
            return rotulo
    return None


def montar_acao(cache, cfg, cot, tk, empresa, detalhe, fund, ult) -> dict:
    s = cot[tk]
    d = {"ticker": tk, "nome": detalhe.get("companyName") or empresa.get("nome", ""),
         "setor_b3": detalhe.get("industryClassification"),
         "setor": setor_besst(cfg, detalhe.get("industryClassification")),
         "liquidez": liquidez(s, ult), "cvm": fund, "code_cvm": empresa.get("codeCVM"),
         "preco": s["d"][-1][1] if s["d"] and s["d"][-1][0] >= ult[0] else None}
    try:
        prov = coleta.proventos_empresa(cache, tk[:4])
    except Exception as e:
        print(f"  aviso: proventos de {tk} indisponíveis na B3 ({e})", file=sys.stderr)
        prov = {}
    isins = {c.get("isin") for c in detalhe.get("otherCodes") or []} | {s["isin"]}
    ev = eventos_de(prov, {s["isin"]}) or eventos_de(prov, isins)
    d["precos"] = precos_ajustados(s, ev)
    # histórico completo (rota de histórico da B3, por classe ON/PN/UNT) + os mais recentes (por ISIN)
    try:
        hist = coleta.historico_proventos_empresa(cache, detalhe.get("tradingName") or "")
    except Exception as e:
        print(f"  aviso: histórico de proventos de {tk} indisponível na B3 ({e})", file=sys.stderr)
        hist = []
    classe = s["esp"].strip()
    extra = [{"isinCode": s["isin"], "label": h.get("corporateAction"), "lastDatePrior": h.get("lastDatePriorEx"),
              "rate": h.get("valueCash")} for h in hist if (h.get("typeStock") or "").strip() == classe]
    prov_total = dict(prov, cashDividends=(prov.get("cashDividends") or []) + extra)
    d["dividendos"], d["dividendos_brutos"], d["jcp_frac"] = proventos_de(
        prov_total, s["isin"], ev, cfg["geral"]["ir_jcp"], fii=False)
    d["proventos_total_12m"] = proventos_totais_12m(prov, ev)
    d["listado_desde"] = listado(prov.get("quotedPerSharSince"))
    return d


def candidatas_acoes(cache, cfg, cot, ult, tickers=None):
    """[(ticker, empresa, detalhe)] — uma ação por empresa, só setores BESST (ou os tickers pedidos)."""
    a = cfg["acoes"]
    empresas = coleta.empresas_b3(cache)
    liq = {}
    for tk, s in cot.items():
        if s["bdi"] != "02" or s["esp"].strip() not in ESPECIES_ACAO:
            continue
        if tickers is not None:
            if tk in tickers:
                liq[tk] = liquidez(s, ult)
        else:
            l = liquidez(s, ult)
            if l >= a["liquidez_minima"]:
                liq[tk] = l
    raizes = sorted({tk[:4] for tk in liq if tk[:4] in empresas})
    detalhes = dict(zip(raizes, em_paralelo(
        lambda rz: _detalhe_seguro(cache, empresas[rz]["codeCVM"]), raizes, cfg["geral"]["workers"])))
    melhor: dict[str, str] = {}
    for tk, l in liq.items():
        rz = tk[:4]
        det = detalhes.get(rz) or {}
        if tickers is None and not setor_besst(cfg, det.get("industryClassification")):
            continue
        if rz not in melhor or l > liq[melhor[rz]]:
            melhor[rz] = tk
    return [(tk, empresas[rz], detalhes.get(rz) or {}) for rz, tk in melhor.items()]


def _detalhe_seguro(cache, code):
    try:
        return coleta.detalhe_empresa(cache, code)
    except Exception:
        return {}


# --------------------------------------------------------------------------- execução

def contexto_mercado(cache, cfg, cot) -> dict:
    agora = dt.datetime.now()
    ctx = {"data": agora.strftime("%d/%m/%Y %H:%M"),
           "ipca": coleta.ipca_12m(cache), "selic_ciclo": coleta.selic_ciclo(cache),
           "focus": coleta.focus(cache, "Selic"), "focus_ipca": coleta.focus(cache, "IPCA")}
    ult = pregoes(cot)
    if ult:
        d = ult[-1]
        ctx["cotacoes_ate"] = f"{d % 100:02d}/{d // 100 % 100:02d}/{d // 10000}"
    proximo = str(agora.year + 1)
    fs, fi = dict(ctx["focus"]), dict(ctx["focus_ipca"])
    ano = proximo if proximo in fs and proximo in fi else str(agora.year)
    if ano in fs and ano in fi:
        ctx["juro_real"] = (1 + fs[ano]) / (1 + fi[ano]) - 1
        ctx["juro_real_ano"] = ano
    ctx["indices"], cfg["_indices"] = [], {}
    for classe, chave, nome in (("FII", "indice_fiis", "IFIX"), ("Ação", "indice_acoes", "Ibovespa")):
        s = cot.get(cfg["sinais"][chave])
        if s and s["d"]:
            ref = s["d"][-1][0] - 10000
            mx = max(h for d, c, h, v in s["d"] if d > ref)
            q = s["d"][-1][1] / mx - 1
            cfg["_indices"][classe] = (nome, q)
            ctx["indices"].append((f"{nome} 12m", q))
    cfg["_ipca"] = ctx["ipca"][0] if ctx["ipca"] else None
    cfg["_juro_real"] = ctx.get("juro_real")
    return ctx


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="Motor de oportunidades Barsi/Zurique/Kiyosaki")
    ap.add_argument("--tickers", help="analisa só estes ativos, separados por vírgula")
    ap.add_argument("--so", choices=["acoes", "fiis"], help="varre só ações ou só FIIs")
    ap.add_argument("--sem-cache", action="store_true", help="baixa de novo os dados do dia")
    ap.add_argument("--abrir", action="store_true", help="abre o relatório no navegador")
    ap.add_argument("--config", default=str(RAIZ / "config.toml"))
    args = ap.parse_args()

    with open(args.config, "rb") as fh:
        cfg = tomllib.load(fh)
    cache = coleta.Cache(RAIZ / cfg["geral"]["pasta_cache"], ativo=not args.sem_cache)
    workers = cfg["geral"]["workers"]
    pedidos = ({t.strip().upper() for t in args.tickers.split(",") if t.strip()}
               if args.tickers else None)

    print("B3: cotações oficiais (COTAHIST)...")
    cot = coleta.cotacoes(cache, cfg["geral"]["anos_cotacao"])
    ult = pregoes(cot)[-42:]
    contexto = contexto_mercado(cache, cfg, cot)
    resultados, fora = [], 0

    if args.so in (None, "fiis"):
        print("CVM: informes dos FIIs...")
        cvm = coleta.fiis_cvm(cache)
        lista = []
        for tk, s in cot.items():
            if s["bdi"] != "12" or (pedidos is not None and tk not in pedidos):
                continue
            info = cvm.get(s["isin"])
            if pedidos is None:
                if liquidez(s, ult) < cfg["fiis"]["liquidez_minima"]:
                    continue
                if fora_do_escopo_fii(tk, info, cfg):
                    fora += 1
                    continue
            lista.append((tk, info))
        print(f"  {len(lista)} FIIs de tijolo líquidos ({fora} fora do escopo: papel, FoF, segmento)")
        dados = em_paralelo(lambda x: montar_fii(cache, cfg, cot, x[0], x[1], ult), lista, workers)
        resultados += [analise.avaliar_fii(d, cfg) for d in dados]

    if args.so in (None, "acoes"):
        print("B3: empresas e classificação setorial...")
        cands = candidatas_acoes(cache, cfg, cot, ult, pedidos)
        print(f"  {len(cands)} empresas{' BESST' if pedidos is None else ''} líquidas; CVM: demonstrações...")
        fund = coleta.cias_cvm(cache, {int(e["codeCVM"]) for _, e, _ in cands})
        dados = em_paralelo(lambda x: montar_acao(cache, cfg, cot, x[0], x[1], x[2],
                                                  fund.get(int(x[1]["codeCVM"]), {}), ult), cands, workers)
        resultados += [analise.avaliar_acao(d, cfg) for d in dados]

    contexto["prefiltrados"] = fora
    agora = dt.datetime.now()
    pasta = RAIZ / cfg["geral"]["pasta_relatorios"] / agora.strftime("%Y-%m-%d_%H%M%S")
    pasta.mkdir(parents=True, exist_ok=True)
    html_path, csv_path = pasta / "relatorio.html", pasta / "dados.csv"
    for r in resultados:   # sinal do mês (Acumular/Aguardar/Pausar/Parar) e meta de renda
        r.extras["sinal"] = sugestoes.sinal(r, cfg)
        r.extras["meta"] = sugestoes.atinge_meta(r, cfg)
        r.extras["renda_sust"] = sugestoes.renda_sustentavel(r)
    sug = sugestoes.gerar(resultados, cfg)
    compra = sugestoes.ranking_compra(resultados, cfg)
    sel = sugestoes.selecao(resultados, cfg)
    web_path = RAIZ / "web" / "painel.html"
    web_path.parent.mkdir(exist_ok=True)
    relatorio.gerar_html(resultados, sug, contexto, html_path, web_path if not pedidos else None,
                         compra, cfg["compra"]["max_por_grupo"], sel)
    relatorio.gerar_csv(resultados, sug, csv_path, compra[0])
    shutil.copyfile(html_path, pasta.parent / "ultimo_relatorio.html")

    print(relatorio.resumo_terminal(sug, compra[0], sel))
    print(f"\nRelatório: {html_path}\nPlanilha:  {csv_path}")
    if args.abrir:
        webbrowser.open(html_path.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
