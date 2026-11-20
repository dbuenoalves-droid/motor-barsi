"""Listas de sugestões do motor (FIIs de tijolo e ações BESST).

Todas partem só de ativos LIMPOS: não descartados, sem renda caindo (estrutural),
sem alerta grave e abaixo do preço teto. Cada lista ordena por uma nota 0-100.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .analise import DESCARTADO, ESTRUTURAL, OPORTUNIDADE, Resultado, _lim
from .formato import brl, dec, pct


@dataclass
class Sugestao:
    r: Resultado
    nota: float
    motivos: list[str] = field(default_factory=list)
    partes: dict = field(default_factory=dict)   # só no ranking de compra: bloco -> (pontos, máximo)


CATEGORIAS = {
    "compra": ("Ranking de compra", "Só compra · foco em dividendos",
               "Os melhores pontos de entrada para acumular e viver de proventos: renda recorrente "
               "alta, paga todo ano sem falha, negócio estável, a preço abaixo do teto. "
               "Não há sinal de venda: a ideia é comprar e segurar."),
    "debandada": ("Debandada", "Padrão TRXF11",
                  "Bons pagadores que caíram bem mais que o próprio índice enquanto a renda continuou "
                  "sendo paga. O mercado vendeu a cota, não o negócio."),
    "juros": ("Queda de juros", "Ganham com Selic menor",
              "Ativos de prazo longo que tendem a se valorizar com o ciclo de cortes: FIIs de tijolo "
              "com desconto patrimonial e empresas de serviços públicos com contratos longos."),
    "dividendos": ("Dividendos", "Maior renda hoje",
                   "Maior DY líquido sustentável agora: renda estável, paga todo mês/ano e coberta "
                   "pela renda recorrente."),
    "longo": ("Longo prazo", "Acumular por anos",
              "Qualidade para acumular quantidade (Barsi) e formar a coluna de ativos (Kiyosaki): "
              "proventos crescendo há 3+ anos, rentabilidade alta, histórico completo."),
}


def limpo(r: Resultado) -> bool:
    return (r.status not in (DESCARTADO, ESTRUTURAL) and not r.alertas_graves
            and r.margem is not None and r.margem >= 0)


def _renda(r: Resultado) -> str:
    return f"R$ 1.000 → ~{dec(r.cotas_mil, 0)} cotas e ~{brl(r.renda_mil)}/mês líquidos"


def _queda(r: Resultado) -> float:
    return -(r.queda_rel if r.queda_rel is not None else (r.queda52 or 0))


def _teto_txt(r: Resultado) -> str:
    txt = f"Teto Barsi {brl(r.teto)} (margem {pct(r.margem, 0, True)}, yield exigido {pct(r.extras.get('yield_alvo'))})"
    if r.teto_bruto:
        txt += f" — pela renda recorrente; pelos proventos pagos seria {brl(r.teto_bruto)}"
    return txt


def _penalidade_balanco(r: Resultado, cfg: dict) -> float:
    """0 a 15 pontos: cresce da faixa de alerta até o limite grave (acima dele já está fora)."""
    if r.classe == "FII":
        v, ini, fim = (r.extras.get("alavancagem"), cfg["fiis"]["alavancagem_alerta"],
                       cfg["fiis"]["alavancagem_maxima"])
    else:
        v, ini, fim = (r.extras.get("divida_ebitda"), cfg["acoes"]["divida_liq_ebitda_alerta"],
                       cfg["acoes"]["divida_liq_ebitda_maxima"])
    if v is None or v <= ini or fim <= ini:
        return 0.0
    return 15 * _lim(v - ini, fim - ini)


# --------------------------------------------------------------------------- categorias

def _debandada(r: Resultado, cfg: dict) -> Sugestao | None:
    if r.status != OPORTUNIDADE or not limpo(r):
        return None
    return Sugestao(r, r.score, r.sinais[:] + [_teto_txt(r), _renda(r)])


def _juros(r: Resultado, cfg: dict) -> Sugestao | None:
    sg = cfg["sugestoes"]
    if not limpo(r):
        return None
    if r.classe == "FII":
        if r.pvp is None or r.pvp > sg["juros_pvp_maximo"]:
            return None
        nota = (40 * _lim(1 - r.pvp, 0.40) + 20 * _lim(_queda(r), 0.30)
                + 20 * _lim(r.margem, 0.50) + 20 * _lim(r.tendencia, 1.0))
        mot = [f"Tijolo a P/VP {dec(r.pvp)}: com juros menores, o desconto de "
               f"{pct(1 - r.pvp, 0)} sobre o patrimônio tende a fechar"]
    else:
        if r.segmento not in sg["setores_sensiveis_juros"]:
            return None
        nota = (30 * _lim(-(r.queda52 or 0), 0.30) + 30 * _lim(r.margem, 0.50)
                + 20 * _lim(r.expansao_dy, 0.50) + 20 * _lim(r.extras.get("roe"), 0.25))
        mot = [f"{r.segmento}: receita regulada e de contratos longos — o valor sobe quando a curva de juros cai"]
    if r.queda52 is not None and r.queda52 < -0.05:
        mot.append(f"Ainda {pct(-r.queda52, 0)} abaixo da máxima de 12 meses")
    mot += [_teto_txt(r), _renda(r)]
    return Sugestao(r, nota, mot)


def _dividendos(r: Resultado, cfg: dict) -> Sugestao | None:
    sg = cfg["sugestoes"]
    if not limpo(r) or r.dy12 is None:
        return None
    if (r.tendencia or 0) < sg["div_tendencia_minima"] or (r.extras.get("consistencia") or 0) < 1:
        return None
    fii = r.classe == "FII"
    if fii:
        if r.dy12 < sg["div_dy_minimo_fii"] or (r.extras.get("meses_pagos_12m") or 0) < 11:
            return None
        if (r.extras.get("ritmo_recente") or 1) < 0.90:
            return None
        cob = r.extras.get("cobertura")
        if cob is None or cob < cfg["fiis"]["cobertura_recorrente_minima"]:
            return None   # renda recorrente não conferida na CVM, ou distribui mais do que ela gera
    elif r.dy12 < sg["div_dy_minimo_acao"]:
        return None
    cv = r.extras.get("cv_rend")
    estab = 1 - min(1.0, cv / 0.30) if cv is not None else 0.5
    nota = (50 * _lim(r.dy12 - 0.06, 0.12) + 20 * estab
            + 15 * _lim((r.tendencia or 0) - 0.80, 0.40) + 15 * _lim(r.margem, 0.50))
    mot = [f"DY líquido de {pct(r.dy12)} " + (f"pago em {r.extras.get('meses_pagos_12m')}/12 meses" if fii else
                                               f"com proventos em {r.extras.get('anos_com_proventos')}/"
                                               f"{r.extras.get('janelas_anos')} anos")]
    if cv is not None:
        mot.append(f"Renda estável: variação de {pct(cv, 0)} entre os pagamentos")
    mot.append(f"Proventos 12m em {pct(r.tendencia, 0)} da média de 3 anos")
    if fii and r.extras.get("cobertura") is not None:
        mot.append(f"Renda recorrente (CVM) cobre {pct(r.extras['cobertura'], 0)} do distribuído")
    mot.append(_renda(r))
    return Sugestao(r, nota, mot)


def _longo(r: Resultado, cfg: dict) -> Sugestao | None:
    sg = cfg["sugestoes"]
    ipca = cfg.get("_ipca")
    if not limpo(r) or (r.extras.get("anos_historico") or 0) < 3.9:
        return None
    if (r.extras.get("consistencia") or 0) < 1 or r.cresc_dpa is None:
        return None
    if r.cresc_dpa < sg["longo_cresc_proventos_minimo"]:
        return None
    acima = f" (acima do IPCA de {pct(ipca)})" if ipca and r.cresc_dpa > ipca else ""
    mot = [f"Proventos crescendo {pct(r.cresc_dpa)} ao ano nos últimos 3 anos{acima}"]
    if r.classe == "FII":
        if (r.pvp or 9) > sg["longo_pvp_maximo_fii"]:
            return None
        vac = r.extras.get("vacancia")
        if vac is not None and vac > sg["longo_vacancia_maxima"]:
            return None
        imoveis = r.extras.get("imoveis") or 0
        longos = r.extras.get("acima_36m")
        nota = (25 * _lim(r.cresc_dpa, 0.10) + 20 * _lim(1 - r.pvp, 0.30) + 20 * _lim(r.margem, 0.50)
                + 15 * (1 - min(1.0, (vac or 0) / 0.15)) + 10 * _lim(imoveis, 30) + 10 * _lim(longos, 0.60))
        mot.append(f"{dec(imoveis, 0)} imóveis, vacância {pct(vac)}, P/VP {dec(r.pvp)}"
                   + (f", {pct(longos, 0)} da receita com contratos acima de 3 anos" if longos else ""))
    else:
        roe, cresc = r.extras.get("roe"), r.extras.get("cresc_receita_3a")
        if roe is None or roe < sg["longo_roe_minimo"] or (cresc is not None and cresc < 0):
            return None
        nota = (30 * _lim(roe, 0.25) + 25 * _lim(r.cresc_dpa, 0.15) + 20 * _lim(r.margem, 0.50)
                + 15 * _lim(cresc, 0.15) + 10 * _lim(-(r.queda52 or 0), 0.30))
        mot.append(f"ROE de {pct(roe)} e receita {pct(cresc, 0, True)} ao ano em 3 anos")
    mot += [f"Proventos em todos os últimos {r.extras.get('janelas_anos')} anos", _teto_txt(r)]
    return Sugestao(r, nota, mot)


_FUNCOES = {"debandada": _debandada, "juros": _juros, "dividendos": _dividendos, "longo": _longo}


def gerar(resultados: list[Resultado], cfg: dict) -> dict[str, list[Sugestao]]:
    top = cfg["sugestoes"]["top"]
    saida = {}
    for chave, f in _FUNCOES.items():
        itens = [s for s in (f(r, cfg) for r in resultados) if s]
        for s in itens:
            pen = _penalidade_balanco(s.r, cfg)
            if pen >= 1:
                s.nota -= pen
                s.motivos.append(f"Nota −{dec(pen, 0)} pela dívida do balanço")
            s.nota = round(max(0.0, min(100.0, s.nota)), 0)
        saida[chave] = sorted(itens, key=lambda s: -s.nota)[:top]
    return saida


# --------------------------------------------------------------------------- ranking de compra

# Alertas leves que já entram na nota por outro bloco, ou só informativos (não descontar)
_JA_PONTUADOS = ("Renda recorrente de", "Dívida de", "Dívida líquida de")


def _grupo(r: Resultado) -> str:
    return r.segmento if r.classe == "Ação" else f"FII {r.segmento}"


def _nota_compra(r: Resultado, cfg: dict) -> Sugestao | None:
    c, f = cfg["compra"], cfg["fiis"]
    x = r.extras
    fii = r.classe == "FII"
    if not limpo(r) or not r.preco or not x.get("dpa_base"):
        return None
    if (x.get("consistencia") or 0) < 0.999 or (r.tendencia or 0) < c["tendencia_minima"]:
        return None
    if (x.get("anos_historico") or 0) < c["anos_minimos_historico"]:
        return None
    if fii:
        if (x.get("meses_pagos_12m") or 0) < 11:
            return None
        if x.get("cobertura") is None:
            return None   # para comprar pela renda, ela precisa estar conferida na CVM
    else:
        if (x.get("roe") or 0) < c["acao_roe_minimo"]:
            return None
        roe_alto = (x.get("roe") or 0) >= c["acao_roe_alto"]
        if not roe_alto and x.get("payout") is not None and x["payout"] > c["acao_payout_maximo"]:
            return None

    mot: list[str] = []
    # ---- 1. Renda recorrente (35): base conservadora do teto ÷ preço (líquida de IR)
    dy_rec = x["dpa_base"] / r.preco
    renda = 35 * _lim(dy_rec - 0.05, 0.09)
    mot.append(f"Renda recorrente líquida de {pct(dy_rec)} ao ano — {_renda(r)}")

    # ---- 2. Estabilidade dos proventos (30)
    anos = x.get("anos_com_proventos") or 0
    cv = x.get("cv_rend")
    regular = (1 - min(1.0, cv / 0.30) if cv is not None else 0.5) if fii else _lim((r.tendencia or 0) - 0.80, 0.40)
    cresc = r.cresc_dpa
    est = 10 * _lim(anos, 5) + 10 * regular + 10 * (_lim(cresc, 0.08) if cresc is not None else 0)
    txt = f"Pagou em todos os últimos {anos} anos"
    if cresc is not None:
        txt += f", proventos {pct(cresc, 1, True)} ao ano em 3 anos"
    if fii and cv is not None:
        txt += f", variação de {pct(cv, 0)} entre pagamentos"
    mot.append(txt)

    # ---- 3. Qualidade (20) — números oficiais (CVM), não opinião sobre o gestor
    if not fii:
        roe, payout, div = x.get("roe") or 0, x.get("payout"), x.get("divida_ebitda")
        q_payout = 1.0 if roe >= c["acao_roe_alto"] else 1 - _lim((payout or 0.6) - 0.60, 0.40)
        if div is None:   # banco/seguradora
            qual = 14 * _lim(roe - 0.08, 0.17) + 6 * q_payout
            mot.append(f"ROE de {pct(roe)}, payout de {pct(payout, 0)}")
        else:
            qual = 8 * _lim(roe - 0.08, 0.17) + 6 * q_payout + 6 * (1 - _lim(div, 4.0))
            mot.append(f"ROE de {pct(roe)}, payout de {pct(payout, 0)}, dívida de {dec(div, 1)}x o EBITDA")
    else:
        vac, alav, imov = x.get("vacancia") or 0, x.get("alavancagem") or 0, x.get("imoveis") or 0
        longos = x.get("acima_36m")
        qual = (6 * (1 - _lim(vac, 0.15)) + 4 * _lim(imov, 20) + 6 * (1 - _lim(alav, f["alavancagem_maxima"]))
                + 4 * (_lim(longos, 0.60) if longos is not None else 0.5))
        mot.append(f"{dec(imov, 0)} imóveis, vacância {pct(vac)}, dívida de {pct(alav, 0)} do ativo"
                   + (f", {pct(longos, 0)} da receita em contratos acima de 3 anos" if longos is not None else ""))

    # ---- 4. Preço de entrada (15)
    preco = 15 * _lim(r.margem, 0.60)
    if fii and r.pvp:
        preco = 9 * _lim(r.margem, 0.60) + 6 * _lim(1 - r.pvp, 0.30)
    mot.append(_teto_txt(r) + (f", P/VP {dec(r.pvp)}" if fii else ""))

    leves = [a for a in r.alertas if not a.startswith(_JA_PONTUADOS) and "JCP" not in a]
    pen = c["penalidade_alerta"] * len(leves)
    nota = renda + est + qual + preco - pen
    partes = {"Renda": (renda, 35), "Estabilidade": (est, 30), "Qualidade": (qual, 20), "Preço": (preco, 15)}
    if pen:
        partes["Alertas"] = (-pen, 0)
    return Sugestao(r, nota, mot, partes)


def ranking_compra(resultados: list[Resultado], cfg: dict) -> tuple[list[Sugestao], list[Sugestao]]:
    """(ranking, barrados pela concentração). Limite de N ativos por grupo (setor/segmento)."""
    c = cfg["compra"]
    itens = [s for s in (_nota_compra(r, cfg) for r in resultados) if s]
    for s in itens:
        s.nota = round(max(0.0, min(100.0, s.nota)), 0)
    itens.sort(key=lambda s: -s.nota)
    ranking, fora, por_grupo = [], [], {}
    for s in itens:
        g = _grupo(s.r)
        if por_grupo.get(g, 0) >= c["max_por_grupo"]:
            fora.append(s)
            continue
        por_grupo[g] = por_grupo.get(g, 0) + 1
        ranking.append(s)
    return ranking[:c["top"]], fora


def listas_por_ticker(sug: dict[str, list[Sugestao]]) -> dict[str, list[str]]:
    mapa: dict[str, list[str]] = {}
    for chave, itens in sug.items():
        for s in itens:
            mapa.setdefault(s.r.ticker, []).append(chave)
    return mapa
