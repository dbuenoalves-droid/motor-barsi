"""Listas de sugestões do motor.

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


CATEGORIAS = {
    "debandada": ("Debandada", "Padrão TRXF11",
                  "Bons pagadores que caíram bem mais que o próprio índice enquanto a renda continuou "
                  "sendo paga. O mercado vendeu a cota, não o negócio."),
    "juros": ("Queda de juros", "Ganham com Selic menor",
              "Ativos de prazo longo que tendem a se valorizar com o ciclo de cortes: FIIs de tijolo e "
              "FoFs com desconto patrimonial e utilities de contratos longos. Papel CDI fica de fora "
              "(perde renda com juros caindo)."),
    "dividendos": ("Dividendos", "Maior renda hoje",
                   "Maior DY sustentável agora: renda estável, paga todo mês/ano, recorrente e sem "
                   "sinais de crédito questionado."),
    "longo": ("Longo prazo", "Acumular por anos",
              "Qualidade para acumular quantidade (Barsi) e formar a coluna de ativos (Kiyosaki): "
              "proventos crescendo há 3+ anos, rentabilidade alta, histórico completo."),
}


def limpo(r: Resultado) -> bool:
    return (r.status not in (DESCARTADO, ESTRUTURAL) and not r.alertas_graves
            and r.margem is not None and r.margem >= 0)


def _renda(r: Resultado) -> str:
    return f"R$ 1.000 → ~{dec(r.cotas_mil, 0)} cotas e ~{brl(r.renda_mil)}/mês"


def _queda(r: Resultado) -> float:
    return -(r.queda_rel if r.queda_rel is not None else (r.queda52 or 0))


# --------------------------------------------------------------------------- categorias

def _debandada(r: Resultado, cfg: dict) -> Sugestao | None:
    if r.status != OPORTUNIDADE or not limpo(r):
        return None
    return Sugestao(r, r.score, r.sinais[:] + [_renda(r)])


def _juros(r: Resultado, cfg: dict) -> Sugestao | None:
    sg = cfg["sugestoes"]
    if not limpo(r):
        return None
    if r.classe == "FII":
        if r.tipo == "Papel" or r.pvp is None or r.pvp > sg["juros_pvp_maximo"]:
            return None
        nota = (40 * _lim(1 - r.pvp, 0.40) + 20 * _lim(_queda(r), 0.30)
                + 20 * _lim(r.margem, 0.50) + 20 * _lim(r.tendencia, 1.0))
        mot = [f"{r.tipo} a P/VP {dec(r.pvp)}: com juros menores, o desconto de "
               f"{pct(1 - r.pvp, 0)} sobre o patrimônio tende a fechar"]
        if r.tipo == "FoF":
            mot.append("FoF: carrega cotas de outros FIIs descontados (duplo desconto)")
    else:
        if r.segmento not in sg["setores_sensiveis_juros"]:
            return None
        nota = (30 * _lim(-(r.queda52 or 0), 0.30) + 30 * _lim(r.margem, 0.50)
                + 20 * _lim(r.expansao_dy, 0.50) + 20 * _lim(r.extras.get("roe"), 0.25))
        mot = [f"{r.segmento}: receita regulada e de contratos longos — o valor sobe quando a curva de juros cai"]
    if r.queda52 is not None and r.queda52 < -0.05:
        mot.append(f"Ainda {pct(-r.queda52, 0)} abaixo da máxima de 52 semanas")
    mot += [f"Teto Barsi {brl(r.teto)} (margem {pct(r.margem, 0, True)})", _renda(r)]
    return Sugestao(r, nota, mot)


def _dividendos(r: Resultado, cfg: dict) -> Sugestao | None:
    sg, s = cfg["sugestoes"], cfg["sinais"]
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
        ffo = r.extras.get("ffo_yield")
        if r.tipo == "Tijolo" and ffo and r.dy12 > ffo * s["dy_vs_ffo_alerta"]:
            return None   # renda pode vir de venda de imóveis
        if r.tipo == "Papel" and (r.pvp or 0) < sg["div_pvp_minimo_papel"]:
            return None   # mercado desconfia do crédito
    elif r.dy12 < sg["div_dy_minimo_acao"]:
        return None
    cv = r.extras.get("cv_rend")
    estab = 1 - min(1.0, cv / 0.30) if cv is not None else 0.5
    nota = (50 * _lim(r.dy12 - 0.06, 0.12) + 20 * estab
            + 15 * _lim((r.tendencia or 0) - 0.80, 0.40) + 15 * _lim(r.margem, 0.50))
    mot = [f"DY de {pct(r.dy12)} " + (f"pago em {r.extras.get('meses_pagos_12m')}/12 meses" if fii else
                                       f"com proventos em {r.extras.get('anos_com_proventos')}/"
                                       f"{r.extras.get('janelas_anos')} anos")]
    if cv is not None:
        mot.append(f"Renda estável: variação de {pct(cv, 0)} entre os pagamentos")
    mot.append(f"Proventos 12m em {pct(r.tendencia, 0)} da média de 3 anos")
    if fii and r.tipo == "Tijolo":
        mot.append("Distribuição coberta pelo FFO (renda recorrente de aluguel)")
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
        if r.tipo != "Tijolo" or (r.pvp or 9) > sg["longo_pvp_maximo_fii"]:
            return None
        vac = r.extras.get("vacancia")
        if vac is not None and vac > sg["longo_vacancia_maxima"]:
            return None
        imoveis = r.extras.get("imoveis") or 0
        nota = (30 * _lim(r.cresc_dpa, 0.10) + 25 * _lim(1 - r.pvp, 0.30) + 20 * _lim(r.margem, 0.50)
                + 15 * (1 - min(1.0, (vac or 0) / 0.15)) + 10 * _lim(imoveis, 30))
        mot.append(f"Imóveis reais: {dec(imoveis, 0)} ativos, vacância {pct(vac)}, P/VP {dec(r.pvp)}")
    else:
        roe, cresc = r.extras.get("roe"), r.extras.get("cresc_receita_5a")
        if roe is None or roe < sg["longo_roe_minimo"] or (cresc is not None and cresc < 0):
            return None
        nota = (30 * _lim(roe, 0.25) + 25 * _lim(r.cresc_dpa, 0.15) + 20 * _lim(r.margem, 0.50)
                + 15 * _lim(cresc, 0.15) + 10 * _lim(-(r.queda52 or 0), 0.30))
        mot.append(f"ROE de {pct(roe)} e receita {pct(cresc, 0, True)} ao ano em 5 anos")
    mot += [f"Proventos em todos os últimos {r.extras.get('janelas_anos')} anos",
            f"Teto Barsi {brl(r.teto)} (margem {pct(r.margem, 0, True)})"]
    return Sugestao(r, nota, mot)


_FUNCOES = {"debandada": _debandada, "juros": _juros, "dividendos": _dividendos, "longo": _longo}


def gerar(resultados: list[Resultado], cfg: dict) -> dict[str, list[Sugestao]]:
    top = cfg["sugestoes"]["top"]
    saida = {}
    for chave, f in _FUNCOES.items():
        itens = [s for s in (f(r, cfg) for r in resultados) if s]
        for s in itens:
            s.nota = round(max(0.0, min(100.0, s.nota)), 0)
        saida[chave] = sorted(itens, key=lambda s: -s.nota)[:top]
    return saida


def listas_por_ticker(sug: dict[str, list[Sugestao]]) -> dict[str, list[str]]:
    mapa: dict[str, list[str]] = {}
    for chave, itens in sug.items():
        for s in itens:
            mapa.setdefault(s.r.ticker, []).append(chave)
    return mapa
