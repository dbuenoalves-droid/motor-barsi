"""Regras do motor: métricas históricas, filtros, sinais de desconto e classificação.

Escopo: só ativos físicos — FIIs de TIJOLO (imóveis reais) e ações de empresas BESST.
Fundos de papel (CRI), fundos de fundos, híbridos e qualquer outro fundo ficam de fora.

Ideia central (caso TRXF11): bom pagador cuja COTA caiu muito enquanto a RENDA se
manteve. Se a renda caiu junto, não é debandada; é problema real (3º Axioma).
"""
from __future__ import annotations

import datetime as dt
import re
import statistics
import time
from dataclasses import dataclass, field

from .formato import brl, dec, mi, pct

DIA = 86400.0
ANO = 365 * DIA

OPORTUNIDADE = "OPORTUNIDADE"
OBSERVAR = "OBSERVAR"
ESTRUTURAL = "ESTRUTURAL"
VITRINE = "VITRINE"
DESCARTADO = "DESCARTADO"
ORDEM_STATUS = [OPORTUNIDADE, OBSERVAR, ESTRUTURAL, VITRINE, DESCARTADO]


def _lim(x: float | None, escala: float) -> float:
    if x is None:
        return 0.0
    return max(0.0, min(1.0, x / escala))


@dataclass
class Resultado:
    ticker: str
    nome: str
    classe: str                     # "FII" ou "Ação"
    segmento: str
    tipo: str = ""                  # "Tijolo" (FII) ou setor (ação)
    preco: float | None = None
    teto: float | None = None
    teto_bruto: float | None = None # teto pelos proventos pagos, antes do limite da renda recorrente
    margem: float | None = None
    dpa12: float | None = None
    dpa_media: float | None = None
    dy12: float | None = None
    dy_medio: float | None = None
    expansao_dy: float | None = None
    queda52: float | None = None
    queda_rel: float | None = None  # queda 52s menos a queda do índice (negativo = caiu mais)
    cresc_dpa: float | None = None  # crescimento anual dos proventos em 3 anos
    pvp: float | None = None
    tendencia: float | None = None
    liquidez: float | None = None
    renda_mil: float | None = None  # renda mensal líquida por R$ 1.000
    cotas_mil: float | None = None  # quantas cotas/ações R$ 1.000 compram (Barsi: quantidade)
    score: float = 0.0
    status: str = ""
    estrutural: bool = False
    sinais: list[str] = field(default_factory=list)
    alertas_graves: list[str] = field(default_factory=list)
    alertas: list[str] = field(default_factory=list)
    descarte: list[str] = field(default_factory=list)
    leitura: list[str] = field(default_factory=list)
    extras: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- histórico

def metricas_historicas(precos: list, divs: list, anos_media: int = 3, listado_desde: float | None = None,
                        agora: float | None = None) -> dict:
    """precos: [(ts, fechamento, máxima)] ajustados; divs: [(ts data-com, valor líquido por cota/ação)]."""
    agora = agora or time.time()
    if len(precos) < 60:
        return {}

    def soma(ini: float, fim: float) -> float:
        return sum(a for t, a in divs if ini < t <= fim)

    t0_preco = precos[0][0]
    t0 = min(listado_desde or t0_preco, t0_preco)
    anos = (agora - t0) / ANO
    anos_int = int(anos + 0.05)
    m: dict = {"anos_historico": anos}
    m["dpa12"] = soma(agora - ANO, agora)
    n = min(anos_media, anos_int)
    m["anos_media_usados"] = n
    m["dpa_media"] = soma(agora - n * ANO, agora) / n if n >= 1 else None
    janelas = min(5, anos_int)
    m["janelas_anos"] = janelas
    m["anos_com_proventos"] = sum(
        1 for k in range(janelas) if soma(agora - (k + 1) * ANO, agora - k * ANO) > 0)
    m["meses_pagos_12m"] = len({dt.datetime.fromtimestamp(t).strftime("%Y-%m")
                                for t, a in divs if agora - ANO < t <= agora})
    m["consistencia"] = m["anos_com_proventos"] / janelas if janelas else None
    pag12 = [a for t, a in divs if agora - ANO < t <= agora]
    m["ritmo_recente"] = (statistics.fmean(pag12[-3:]) / statistics.fmean(pag12)
                          if len(pag12) >= 4 else None)
    m["cv_rend"] = (statistics.pstdev(pag12) / statistics.fmean(pag12)
                    if len(pag12) >= 6 else None)
    antigo = soma(agora - 4 * ANO, agora - 3 * ANO)
    m["cresc_dpa_3a"] = ((m["dpa12"] / antigo) ** (1 / 3) - 1
                         if antigo > 0 and m["dpa12"] > 0 and anos_int >= 4 else None)
    m["max52"] = max((h for t, c, h in precos if t > agora - ANO), default=None)
    # DY "normal" do ativo: média do DY 12m semana a semana, do 2º ano de cotação até 3 meses atrás
    semanais = precos[::5]
    dys = [soma(t - ANO, t) / c for t, c, h in semanais
           if t - ANO >= t0 and t <= agora - 91 * DIA and c > 0]
    m["dy_medio"] = statistics.fmean(dys) if len(dys) >= 26 else None
    return m


def _aplicar_historico(r: Resultado, m: dict, yield_alvo: float, cfg: dict,
                       base_recorrente: float | None = None, tolerancia: float = 1.0) -> None:
    """base_recorrente: renda anual por cota que se repete (resultado recorrente da CVM no FII).
    Se cobrir menos que `tolerancia` dos proventos pagos, ela limita o teto."""
    s = cfg["sinais"]
    r.dpa12, r.dpa_media = m.get("dpa12"), m.get("dpa_media")
    r.cresc_dpa = m.get("cresc_dpa_3a")
    for k in ("consistencia", "ritmo_recente", "cv_rend", "meses_pagos_12m",
              "anos_historico", "anos_com_proventos", "janelas_anos"):
        r.extras[k] = m.get(k)
    if r.preco:
        r.cotas_mil = 1000 / r.preco
        if r.dpa12 is not None:
            r.dy12 = r.dpa12 / r.preco
            r.renda_mil = 1000 * r.dy12 / 12
        if m.get("max52"):
            r.queda52 = r.preco / m["max52"] - 1
    r.dy_medio = m.get("dy_medio")
    if r.dy12 and r.dy_medio:
        r.expansao_dy = r.dy12 / r.dy_medio - 1
    if r.dpa12 is not None and r.dpa_media:
        r.tendencia = r.dpa12 / r.dpa_media

    # Preço teto Barsi, conservador: menor entre o DPA de 12m e a média de N anos
    base = r.dpa12 if r.dpa_media is None else min(r.dpa12 or 0, r.dpa_media)
    if base and base > 0 and base_recorrente is not None and base_recorrente < base * tolerancia:
        r.teto_bruto = base / yield_alvo
        base = max(base_recorrente, 0)
    if base and base > 0:
        r.extras["dpa_base"] = base     # renda anual conservadora/recorrente por cota
        r.teto = base / yield_alvo
        if r.preco:
            r.margem = r.teto / r.preco - 1
    r.extras["yield_alvo"] = yield_alvo

    if r.tendencia is not None and r.tendencia < s["tendencia_proventos_minima"]:
        r.estrutural = True
        r.alertas_graves.append(
            f"Proventos de 12 meses em {pct(r.tendencia, 0)} da média de "
            f"{m['anos_media_usados']} anos: a renda caiu junto com a cota")


def yield_minimo(piso: float, premio: float | None, juro_real: float | None) -> float:
    """Yield exigido = maior entre o piso da classe e (juro real esperado + prêmio).
    Aluguel e dividendo crescem com a inflação, então competem com o juro REAL."""
    if premio is None or juro_real is None:
        return piso
    return max(piso, juro_real + premio)


# --------------------------------------------------------------------------- FII de tijolo

def avaliar_fii(d: dict, cfg: dict) -> Resultado:
    f, s = cfg["fiis"], cfg["sinais"]
    cvm = d.get("cvm") or {}
    r = Resultado(ticker=d["ticker"], nome=cvm.get("nome") or d.get("nome", ""), classe="FII",
                  segmento=cvm.get("segmento", "?"), tipo="Tijolo")
    manual = (f.get("manual") or {}).get(r.ticker) or {}
    if manual.get("segmento"):
        r.segmento = manual["segmento"]
    r.preco, r.liquidez = d.get("preco"), d.get("liquidez")
    nome_up = r.nome.upper()

    # ---- escopo: só tijolo (imóveis físicos), pela composição oficial da carteira na CVM
    ti = cvm.get("total_investido") or 0
    fisico = cvm.get("imoveis_fisicos", 0) / ti if ti else None
    credito = cvm.get("credito", 0) / ti if ti else None
    fundos = cvm.get("cotas_fundos", 0) / ti if ti else None
    r.extras.update(fisico=fisico, credito=credito, cotas_fundos=fundos)
    if not cvm:
        r.descarte.append("Sem informe na CVM para este ISIN")
    elif fisico is None:
        r.descarte.append("Informe da CVM sem composição da carteira")
    elif fisico < f["tijolo_fisico_minimo"]:
        r.descarte.append(f"Não é tijolo: só {pct(fisico, 0)} em imóveis físicos "
                          f"({pct(credito, 0)} em CRI/crédito, {pct(fundos, 0)} em cotas de outros fundos)")
    if re.search(r"\bFOF\b|FUNDOS? DE FUNDOS", nome_up):
        r.descarte.append("Fundo de fundos: intermediário a mais entre você e o imóvel")
    if cvm.get("segmento") in f["segmentos_excluidos"] and not manual.get("segmento"):
        r.descarte.append(f"Segmento '{cvm.get('segmento')}' fora do escopo perene")
    proibida = next((p for p in f["palavras_excluidas"] if p in nome_up), None)
    if proibida:
        r.descarte.append(f"Nome indica segmento fora do escopo ('{proibida.title()}')")
    if manual.get("excluir"):
        r.descarte.append(f"Fora do escopo: {manual.get('motivo') or 'excluído em config.toml'}")
    if (r.liquidez or 0) < f["liquidez_minima"]:
        r.descarte.append(f"Liquidez baixa ({brl(r.liquidez, 0)}/dia)")
    if not r.preco:
        r.descarte.append("Sem cotação recente na B3")

    vp = cvm.get("vp_cota")
    r.pvp = r.preco / vp if r.preco and vp else None
    va = cvm.get("valor_ativo") or 0
    alav = cvm.get("divida", 0) / va if va else None
    rec = cvm.get("recorrente") or {}
    r.extras.update(alavancagem=alav, securitizacao=cvm.get("securitizacao"),
                    obrig_aquisicao=cvm.get("obrig_aquisicao"), vacancia=cvm.get("vacancia"),
                    imoveis=cvm.get("qtd_imoveis"), venc_12m=cvm.get("venc_12m"),
                    acima_36m=cvm.get("acima_36m"), vp_cota=vp, cvm_ref=cvm.get("data_ref"),
                    recorrente=rec.get("por_cota"), recorrente_motivo=rec.get("motivo"),
                    ganhos_venda=rec.get("ganhos_por_cota"), cnpj=cvm.get("cnpj"))

    m = metricas_historicas(d.get("precos") or [], d.get("dividendos") or [],
                            cfg["geral"]["anos_media_proventos"], d.get("listado_desde"))
    if not m:
        r.descarte.append("Sem histórico de cotações suficiente na B3")
    else:
        y = yield_minimo(f["yield_minimo_tijolo"], f.get("premio_sobre_juro_real"), cfg.get("_juro_real"))
        _aplicar_historico(r, m, y, cfg, rec.get("por_cota"), f["cobertura_recorrente_minima"])
        if r.dpa12 and rec.get("por_cota") is not None:
            r.extras["cobertura"] = rec["por_cota"] / r.dpa12
        if r.teto_bruto:
            r.alertas.append(
                f"Renda recorrente de {brl(rec['por_cota'])}/cota em 12m (CVM) cobre só "
                f"{pct(r.extras.get('cobertura'), 0)} do distribuído: o teto usa a recorrente — "
                f"pelos proventos pagos seria {brl(r.teto_bruto)}")
        elif rec.get("por_cota") is None and rec.get("motivo"):
            r.alertas.append(f"Renda recorrente não conferida: {rec['motivo']}")
        if (rec.get("ganhos_por_cota") or 0) > 0 and r.dpa12 and rec["ganhos_por_cota"] / r.dpa12 > 0.10:
            r.alertas.append(f"Ganho com venda de ativos de {brl(rec['ganhos_por_cota'])}/cota em 12m: "
                             "parte da distribuição não se repete")
        if m["anos_historico"] < f["anos_minimos_historico"]:
            r.descarte.append(f"Histórico curto ({dec(m['anos_historico'], 1)} anos)")
        if m["meses_pagos_12m"] < f["meses_minimos_com_rendimento"]:
            r.descarte.append(f"Pagou rendimento em só {m['meses_pagos_12m']} dos últimos 12 meses")
        ritmo = m.get("ritmo_recente")
        if ritmo is not None and ritmo < s["tendencia_proventos_minima"]:
            r.alertas_graves.append(
                f"Corte recente: últimos 3 rendimentos em {pct(ritmo, 0)} da média de 12m "
                "(saída de inquilino, venda de imóvel ou fim de renda garantida?)")

    vac = cvm.get("vacancia")
    if vac is not None and vac > f["vacancia_maxima"]:
        r.alertas_graves.append(f"Vacância de {pct(vac)} (ponderada pela receita, CVM)")
    if alav is not None:
        txt = (f"Dívida de {pct(alav, 0)} do ativo (securitização {mi(cvm.get('securitizacao'))} + "
               f"compras a prazo {mi(cvm.get('obrig_aquisicao'))}): sofre com juros altos")
        if alav > f["alavancagem_maxima"]:
            r.alertas_graves.append(txt)
        elif alav > f["alavancagem_alerta"]:
            r.alertas.append(txt)
    v12 = cvm.get("venc_12m")
    if v12 is not None and v12 > f["contratos_venc_12m_alerta"]:
        r.alertas.append(f"{pct(v12, 0)} da receita com contratos vencendo em 12 meses: risco de revisão ou saída")
    if r.pvp is not None and r.pvp > 0 and r.pvp < f["pvp_minimo_suspeito"]:
        r.alertas.append(f"P/VP {dec(r.pvp)}: desconto extremo — confira se o laudo dos imóveis está atualizado")
    if r.dy12 and r.dy12 > s["dy_suspeito"]:
        r.alertas_graves.append(f"DY de {pct(r.dy12)}: alto demais para ser sustentável")

    _concluir(r, cfg, fii=True)
    return r


# --------------------------------------------------------------------------- ações

def avaliar_acao(d: dict, cfg: dict) -> Resultado:
    a, s = cfg["acoes"], cfg["sinais"]
    k = d.get("cvm") or {}
    r = Resultado(ticker=d["ticker"], nome=d.get("nome", ""), classe="Ação",
                  segmento=d.get("setor") or "?", tipo=d.get("setor") or "?")
    r.preco, r.liquidez = d.get("preco"), d.get("liquidez")
    financeira = r.segmento in a["setores_financeiros"]

    if not d.get("setor"):
        r.descarte.append(f"Setor '{d.get('setor_b3') or '?'}' fora do BESST (perenes)")
    if (r.liquidez or 0) < a["liquidez_minima"]:
        r.descarte.append(f"Liquidez baixa ({brl(r.liquidez, 0)}/dia)")
    if not r.preco:
        r.descarte.append("Sem cotação recente na B3")
    lucro, pl = k.get("lucro_12m"), k.get("pl")
    if k.get("erro") or lucro is None:
        r.descarte.append(f"Sem lucro de 12 meses na CVM ({k.get('erro') or 'demonstrações incompletas'})")
    elif lucro <= 0:
        r.descarte.append("Empresa com prejuízo nos últimos 12 meses (CVM)")
    roe = lucro / pl if lucro is not None and pl and pl > 0 else None
    ebitda = ((k.get("ebit_12m") or 0) + (k.get("da_12m") or 0)) if k.get("ebit_12m") is not None else None
    div_liq = ((k.get("divida_bruta") or 0) - (k.get("caixa") or 0)
               if k.get("divida_bruta") is not None and not financeira else None)
    div_ebitda = div_liq / ebitda if div_liq is not None and ebitda and ebitda > 0 else None
    payout = d.get("proventos_total_12m") / lucro if d.get("proventos_total_12m") and lucro and lucro > 0 else None
    r.extras.update(roe=roe, payout=payout, divida_ebitda=div_ebitda, lucro_12m=lucro,
                    cresc_receita_3a=k.get("cresc_receita_3a"), cvm_ref=k.get("data_ref"),
                    jcp_frac=d.get("jcp_frac"), code_cvm=d.get("code_cvm"))

    m = metricas_historicas(d.get("precos") or [], d.get("dividendos") or [],
                            cfg["geral"]["anos_media_proventos"], d.get("listado_desde"))
    if not m:
        r.descarte.append("Sem histórico de cotações suficiente na B3")
    else:
        y = yield_minimo(a["yield_minimo"], a.get("premio_sobre_juro_real"), cfg.get("_juro_real"))
        _aplicar_historico(r, m, y, cfg)
        exigido = min(a["anos_minimos_com_proventos"], m["janelas_anos"])
        if m["janelas_anos"] < 2:
            r.descarte.append("Histórico curto")
        elif m["anos_com_proventos"] < exigido:
            r.descarte.append(
                f"Pagou proventos em só {m['anos_com_proventos']} dos últimos {m['janelas_anos']} anos")

    if (d.get("jcp_frac") or 0) > 0.05:
        r.alertas.append(f"{pct(d['jcp_frac'], 0)} dos proventos em JCP: o DY mostrado já desconta "
                         f"{pct(cfg['geral']['ir_jcp'], 0)} de IR na fonte")
    if roe is not None and roe < a["roe_minimo"]:
        r.alertas_graves.append(f"ROE baixo ({pct(roe)})")
    if div_ebitda is not None:
        txt = f"Dívida líquida de {dec(div_ebitda, 1)}x o EBITDA"
        if div_ebitda > a["divida_liq_ebitda_maxima"]:
            r.alertas_graves.append(txt + ": alavancagem alta para um pagador de dividendos")
        elif div_ebitda > a["divida_liq_ebitda_alerta"]:
            r.alertas.append(txt + ": dividendo disputa caixa com o serviço da dívida")
    cresc = k.get("cresc_receita_3a")
    if cresc is not None and cresc < 0:
        r.alertas.append(f"Receita encolheu nos últimos 3 anos ({pct(cresc)} ao ano)")
    if r.dy12 and r.dy12 > s["dy_suspeito"]:
        r.alertas_graves.append(f"DY de {pct(r.dy12)}: provável provento extraordinário")
    if payout is not None and payout > a["payout_maximo"]:
        r.alertas_graves.append(f"Paga mais do que lucra (payout {pct(payout, 0)}): dividendo insustentável")

    _concluir(r, cfg, fii=False)
    return r


# --------------------------------------------------------------------------- classificação

def _concluir(r: Resultado, cfg: dict, fii: bool) -> None:
    s = cfg["sinais"]
    if r.descarte:
        r.status = DESCARTADO
        return

    indice = (cfg.get("_indices") or {}).get(r.classe)   # (nome, queda52 do índice)
    sinal_queda = False
    if r.queda52 is not None and indice:
        r.queda_rel = r.queda52 - indice[1]
        sinal_queda = r.queda_rel <= -s["queda_relativa_minima"]
        if sinal_queda:
            r.sinais.append(f"Cota {pct(-r.queda52, 0)} abaixo da máxima de 52s — "
                            f"{dec(-r.queda_rel * 100, 0)} p.p. a mais que o {indice[0]} ({pct(indice[1], 0)})")
    elif r.queda52 is not None:
        sinal_queda = r.queda52 <= -s["queda_52s_minima"]
        if sinal_queda:
            r.sinais.append(f"Cota {pct(-r.queda52, 0)} abaixo da máxima de 52 semanas")
    sinal_dy = r.expansao_dy is not None and r.expansao_dy >= s["expansao_dy_minima"]
    if sinal_dy:
        r.sinais.append(f"DY {pct(r.dy12)} contra média histórica de {pct(r.dy_medio)}")
    sinal_pvp = bool(fii and r.pvp and r.pvp <= s["pvp_desconto"])
    if sinal_pvp:
        r.sinais.append(f"P/VP {dec(r.pvp)} ({pct(1 - r.pvp, 0)} abaixo do patrimônio)")

    barato = r.margem is not None and r.margem >= s["margem_minima"]
    caro_pvp = fii and r.pvp is not None and r.pvp > cfg["fiis"]["pvp_maximo"]
    todos_sinais = sinal_queda and sinal_dy and (sinal_pvp or not fii)

    if r.estrutural:
        r.status = ESTRUTURAL
    elif barato and todos_sinais and not r.alertas_graves and not caro_pvp:
        r.status = OPORTUNIDADE
    elif not barato or caro_pvp:
        r.status = VITRINE
    else:
        r.status = OBSERVAR

    # Score 0-100 (só para ordenar dentro de cada grupo)
    sc = 30 * _lim(r.margem, 0.30)
    sc += 25 * _lim(r.expansao_dy, 0.50)
    sc += 15 * _lim(-(r.queda_rel if r.queda_rel is not None else (r.queda52 or 0)), 0.30)
    if fii:
        sc += 20 * _lim(1 - (r.pvp or 1), 0.40)
    else:
        sc += 20 * _lim(r.extras.get("roe"), 0.25)
    sc += 10 * _lim(r.tendencia, 1.0)
    sc -= 15 * len(r.alertas_graves) + 3 * len(r.alertas)
    r.score = round(max(0.0, min(100.0, sc)), 1)

    _leitura(r, fii)


def _leitura(r: Resultado, fii: bool) -> None:
    L = r.leitura
    if r.status == ESTRUTURAL:
        L.append("A cota caiu junto com a renda: o mercado pode ter razão. 3º Axioma — não "
                 "alimente esperança; só reavalie se os proventos voltarem.")
    elif r.status == OPORTUNIDADE:
        L.append(f"Cota caiu {pct(-r.queda52, 0)} em 52 semanas, bem mais que o mercado, enquanto a "
                 f"renda se manteve ({pct(r.tendencia, 0)} da média): queda descolada do caixa — "
                 "padrão de debandada (10º Axioma).")
    elif r.status == OBSERVAR and r.sinais:
        L.append("Descontado, mas em linha com o mercado ou com algum alerta: não é uma "
                 "debandada específica deste ativo.")
    elif r.status == VITRINE:
        L.append("Preço acima do teto: está na vitrine. Barsi espera a baixa para acumular.")
    if r.teto:
        L.append(f"Teto Barsi {brl(r.teto)} (margem {pct(r.margem, 0, sinal=True)}).")
    if fii and r.pvp:
        if r.pvp < 1:
            L.append(f"Compra o patrimônio com {pct(1 - r.pvp, 0)} de desconto.")
        else:
            L.append(f"Paga {pct(r.pvp - 1, 0)} acima do patrimônio.")
    if r.renda_mil:
        L.append(f"R$ 1.000 compram ~{dec(r.cotas_mil, 0)} cotas e rendem ~{brl(r.renda_mil)}/mês.")
