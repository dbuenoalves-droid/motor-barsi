"""Regras do motor: métricas históricas, filtros, sinais de desconto e classificação.

Ideia central (caso TRXF11): bom pagador cuja COTA caiu muito enquanto a RENDA se
manteve. Se a renda caiu junto, não é debandada; é problema real (3º Axioma).
"""
from __future__ import annotations

import datetime as dt
import re
import statistics
import time
from dataclasses import dataclass, field

from .formato import brl, dec, pct

DIA = 86400.0
ANO = 365 * DIA

OPORTUNIDADE = "OPORTUNIDADE"
OBSERVAR = "OBSERVAR"
ESTRUTURAL = "ESTRUTURAL"
VITRINE = "VITRINE"
DESCARTADO = "DESCARTADO"
ORDEM_STATUS = [OPORTUNIDADE, OBSERVAR, ESTRUTURAL, VITRINE, DESCARTADO]


def num(txt) -> float | None:
    """'1.234,56' -> 1234.56 ; '20,89%' -> 0.2089 ; '-' -> None"""
    if txt is None:
        return None
    s = str(txt).strip()
    eh_pct = s.endswith("%")
    s = s.replace("%", "").replace(".", "").replace(",", ".").strip()
    if s in ("", "-"):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v / 100 if eh_pct else v


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
    tipo: str = ""                  # Tijolo/Papel (FII) ou setor (ação)
    preco: float | None = None
    teto: float | None = None
    margem: float | None = None
    dpa12: float | None = None
    dpa_media: float | None = None
    dy12: float | None = None
    dy_medio: float | None = None
    expansao_dy: float | None = None
    queda52: float | None = None
    queda3a: float | None = None
    queda_rel: float | None = None  # queda 52s menos a queda do índice (negativo = caiu mais)
    cresc_dpa: float | None = None  # crescimento anual dos proventos em 3 anos
    pvp: float | None = None
    tendencia: float | None = None
    liquidez: float | None = None
    renda_mil: float | None = None  # renda mensal estimada por R$ 1.000
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

def metricas_historicas(hist: dict, anos_media: int = 3, agora: float | None = None) -> dict:
    agora = agora or time.time()
    precos = hist.get("precos") or []
    divs = hist.get("dividendos") or []
    if len(precos) < 10:
        return {}

    def soma(ini: float, fim: float) -> float:
        return sum(a for t, a in divs if ini < t <= fim)

    t0 = precos[0][0]
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
    antigo = soma(agora - 4 * ANO, agora - 3 * ANO) if anos_int >= 4 else 0
    m["cresc_dpa_3a"] = ((m["dpa12"] / antigo) ** (1 / 3) - 1
                         if antigo > 0 and m["dpa12"] > 0 else None)

    m["max52"] = max((h for t, c, h in precos if t > agora - ANO), default=None)
    m["max3a"] = max((h for t, c, h in precos if t > agora - 3 * ANO), default=None)
    fech3a = [c for t, c, h in precos if t > agora - 3 * ANO]
    m["media_preco_3a"] = statistics.fmean(fech3a) if fech3a else None

    # DY "normal" do ativo: média do DY 12m em cada semana, do 2º ano até 3 meses atrás
    dys = [soma(t - ANO, t) / c for t, c, h in precos
           if t - ANO >= t0 and t <= agora - 91 * DIA]
    m["dy_medio"] = statistics.fmean(dys) if len(dys) >= 26 else None
    return m


def _aplicar_historico(r: Resultado, m: dict, yield_alvo: float, cfg: dict) -> None:
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
        if m.get("max3a"):
            r.queda3a = r.preco / m["max3a"] - 1
    r.dy_medio = m.get("dy_medio")
    if r.dy12 and r.dy_medio:
        r.expansao_dy = r.dy12 / r.dy_medio - 1
    if r.dpa12 is not None and r.dpa_media:
        r.tendencia = r.dpa12 / r.dpa_media

    # Preço teto Barsi, conservador: menor entre o DPA de 12m e a média de N anos
    base = r.dpa12 if r.dpa_media is None else min(r.dpa12 or 0, r.dpa_media)
    if base and base > 0:
        r.teto = base / yield_alvo
        if r.preco:
            r.margem = r.teto / r.preco - 1

    if r.tendencia is not None and r.tendencia < s["tendencia_proventos_minima"]:
        r.estrutural = True
        r.alertas_graves.append(
            f"Proventos de 12 meses em {pct(r.tendencia, 0)} da média de "
            f"{m['anos_media_usados']} anos: a renda caiu junto com a cota")


# --------------------------------------------------------------------------- pré-filtros

def prefiltro_fii(linha: dict, cfg: dict) -> list[str]:
    f = cfg["fiis"]
    motivos = []
    if linha.get("Segmento", "") not in f["segmentos_permitidos"]:
        motivos.append("segmento")
    if (num(linha.get("Liquidez")) or 0) < f["liquidez_minima"]:
        motivos.append("liquidez")
    if (num(linha.get("Dividend Yield")) or 0) < f["dy_pre_filtro"]:
        motivos.append("dy")
    if not num(linha.get("P/VP")):
        motivos.append("pvp")
    return motivos


def prefiltro_acao(linha: dict, cfg: dict) -> list[str]:
    a = cfg["acoes"]
    motivos = []
    if (num(linha.get("Liq.2meses")) or 0) < a["liquidez_minima"]:
        motivos.append("liquidez")
    if (num(linha.get("Div.Yield")) or 0) < a["dy_pre_filtro"]:
        motivos.append("dy")
    if (num(linha.get("P/L")) or 0) <= 0:
        motivos.append("prejuizo")
    return motivos


# --------------------------------------------------------------------------- avaliação

def avaliar_fii(linha: dict, hist: dict | None, cfg: dict) -> Resultado:
    f, s = cfg["fiis"], cfg["sinais"]
    r = Resultado(ticker=linha.get("Papel", "?"), nome=linha.get("_nome", ""),
                  classe="FII", segmento=linha.get("Segmento", ""))
    r.preco, r.pvp = num(linha.get("Cotação")), num(linha.get("P/VP"))
    r.liquidez = num(linha.get("Liquidez"))
    ffo, vac = num(linha.get("FFO Yield")), num(linha.get("Vacância Média"))
    imoveis = num(linha.get("Qtd de imóveis"))
    papel = r.segmento == "Títulos e Val. Mob." or not imoveis
    nome_up = r.nome.upper()
    fof = bool(re.search(r"\bFOF|FUNDOS? DE FUNDOS", nome_up))
    r.tipo = "FoF" if fof else ("Papel" if papel else "Tijolo")
    proibida = next((p for p in f.get("palavras_excluidas", []) if p in nome_up), None)
    if proibida:
        r.descarte.append(f"Nome indica segmento fora do escopo ('{proibida.title()}')")
    r.extras.update(ffo_yield=ffo, vacancia=vac, imoveis=imoveis,
                    valor_mercado=num(linha.get("Valor de Mercado")))

    if r.segmento not in f["segmentos_permitidos"]:
        r.descarte.append(f"Segmento '{r.segmento}' fora do escopo perene")
    if (r.liquidez or 0) < f["liquidez_minima"]:
        r.descarte.append(f"Liquidez baixa ({brl(r.liquidez, 0)}/dia)")
    if not r.preco or not r.pvp:
        r.descarte.append("Sem cotação ou P/VP no Fundamentus")

    m = metricas_historicas(hist, cfg["geral"]["anos_media_proventos"]) if hist else {}
    if not m:
        r.descarte.append("Sem histórico de cotações/proventos")
    else:
        yield_alvo = f["yield_minimo_papel"] if papel else f["yield_minimo_tijolo"]
        _aplicar_historico(r, m, yield_alvo, cfg)
        if m["anos_historico"] < f["anos_minimos_historico"]:
            r.descarte.append(f"Histórico curto ({dec(m['anos_historico'], 1)} anos)")
        if m["meses_pagos_12m"] < f["meses_minimos_com_rendimento"]:
            r.descarte.append(f"Pagou rendimento em só {m['meses_pagos_12m']} dos últimos 12 meses")
        ritmo = m.get("ritmo_recente")
        if ritmo is not None and ritmo < s["tendencia_proventos_minima"]:
            causa = ("em papel indexado à inflação pode ser só IPCA mensal baixo — confira se é "
                     "inflação ou inadimplência" if papel else
                     "confira se foi saída de inquilino, venda de imóvel ou fim de renda garantida")
            r.alertas_graves.append(
                f"Corte recente: últimos 3 rendimentos em {pct(ritmo, 0)} da média de 12m ({causa})")

    if not papel and vac is not None and vac > f["vacancia_maxima"]:
        r.alertas_graves.append(f"Vacância de {pct(vac)}")
    if papel and r.pvp and r.pvp < f["pvp_minimo_papel"]:
        r.alertas_graves.append(
            f"P/VP {dec(r.pvp)} em fundo de papel: mercado precifica risco de crédito nos CRIs")
    if not papel and ffo and r.dy12 and r.dy12 > ffo * s["dy_vs_ffo_alerta"]:
        r.alertas.append(
            f"Distribui {pct(r.dy12)} com FFO yield de {pct(ffo)}: parte da renda pode vir de "
            "venda de imóveis (não recorrente) — confira os relatórios gerenciais")
    if r.dy12 and r.dy12 > s["dy_suspeito"]:
        r.alertas_graves.append(f"DY de {pct(r.dy12)}: alto demais para ser sustentável")
    if r.segmento in f["segmentos_genericos"]:
        r.alertas.append(f"Segmento '{r.segmento}' é genérico no Fundamentus: confira o portfólio")

    _concluir(r, cfg, fii=True)
    return r


def avaliar_acao(linha: dict, hist: dict | None, cfg: dict,
                 setor_id: str | None, setor_nome: str = "") -> Resultado:
    a, s = cfg["acoes"], cfg["sinais"]
    setores = a["setores"]
    r = Resultado(ticker=linha.get("Papel", "?"), nome=linha.get("_nome", ""), classe="Ação",
                  segmento=setores.get(setor_id or "", setor_nome or "?"))
    r.tipo = r.segmento
    r.preco, r.pvp = num(linha.get("Cotação")), num(linha.get("P/VP"))
    r.liquidez = num(linha.get("Liq.2meses"))
    pl, roe = num(linha.get("P/L")), num(linha.get("ROE"))
    divida, cresc = num(linha.get("Dív.Líq/ Patrim.")), num(linha.get("Cresc. Rec.5a"))
    r.extras.update(pl=pl, roe=roe, divida_pl=divida, cresc_receita_5a=cresc,
                    margem_liquida=num(linha.get("Mrg. Líq.")))

    if setor_id not in setores:
        r.descarte.append(f"Setor '{setor_nome or '?'}' fora do BESST (perenes)")
    if (r.liquidez or 0) < a["liquidez_minima"]:
        r.descarte.append(f"Liquidez baixa ({brl(r.liquidez, 0)}/dia)")
    if pl is None or pl <= 0:
        r.descarte.append("Empresa com prejuízo (P/L negativo)")

    m = metricas_historicas(hist, cfg["geral"]["anos_media_proventos"]) if hist else {}
    if not m:
        r.descarte.append("Sem histórico de cotações/proventos")
    else:
        _aplicar_historico(r, m, a["yield_minimo"], cfg)
        exigido = min(a["anos_minimos_com_proventos"], m["janelas_anos"])
        if m["janelas_anos"] < 2:
            r.descarte.append("Histórico curto")
        elif m["anos_com_proventos"] < exigido:
            r.descarte.append(
                f"Pagou proventos em só {m['anos_com_proventos']} dos últimos {m['janelas_anos']} anos")

    if roe is not None and roe < a["roe_minimo"]:
        r.alertas_graves.append(f"ROE baixo ({pct(roe)})")
    if (setor_id not in a["setores_financeiros"] and divida is not None
            and divida > a["divida_liq_pl_maxima"]):
        r.alertas_graves.append(f"Dívida líquida/PL alta ({dec(divida)})")
    if cresc is not None and cresc < 0:
        r.alertas.append(f"Receita encolheu nos últimos 5 anos ({pct(cresc)})")
    if r.dy12 and r.dy12 > s["dy_suspeito"]:
        r.alertas_graves.append(f"DY de {pct(r.dy12)}: provável provento extraordinário")
    if r.dy12 and pl and pl > 0:
        payout = r.dy12 * pl
        r.extras["payout"] = payout
        if payout > a["payout_maximo"]:
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
