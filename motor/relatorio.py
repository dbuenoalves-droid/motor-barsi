"""Painel de análise (HTML autocontido), planilha CSV e resumo no terminal."""
from __future__ import annotations

import csv
import html
from pathlib import Path

from .analise import DESCARTADO, ESTRUTURAL, ORDEM_STATUS, Resultado
from .formato import brl, dec, pct
from .sugestoes import CATEGORIAS, Sugestao, limpo, listas_por_ticker

COR = {"compra": "cmp", "debandada": "deb", "juros": "jur", "dividendos": "div", "longo": "lon"}


def _e(v) -> str:
    return html.escape(str(v))


def _links(r: Resultado) -> str:
    """Só links oficiais (documentos na B3/CVM) e uma busca de notícias para checar o motivo da queda."""
    t = r.ticker
    if r.classe == "FII":
        cnpj = "".join(ch for ch in (r.extras.get("cnpj") or "") if ch.isdigit())
        doc = (f'<a href="https://fnet.bmfbovespa.com.br/fnet/publico/abrirGerenciadorDocumentosCVM?cnpjFundo={cnpj}" '
               f'target="_blank">Relatórios (B3 FundosNET)</a>' if cnpj else "")
        # consulta manual (análises e comentários de investidores); o motor não lê dados de lá
        doc += f'<a href="https://www.clubefii.com.br/fiis/{t}" target="_blank">Clube FII</a>'
    else:
        cod = r.extras.get("code_cvm")
        doc = (f'<a href="https://sistemaswebb3-listados.b3.com.br/listedCompaniesPage/main/{cod}/{t[:4]}/overview?language=pt-br" '
               f'target="_blank">Empresa na B3</a>' if cod else "")
    return doc + f'<a href="https://news.google.com/search?q={t}&hl=pt-BR" target="_blank">Notícias</a>'


def _sinal(v: float | None, bom_positivo: bool = True) -> str:
    if v is None:
        return ""
    return "pos" if (v >= 0) == bom_positivo else "neg"


def _metrica(rotulo: str, valor: str, cls: str = "", sub: str = "") -> str:
    s = f'<small>{_e(sub)}</small>' if sub else ""
    return f'<div class="m"><span>{_e(rotulo)}</span><b class="{cls}">{valor}</b>{s}</div>'


def _mes(dy_ano: float | None) -> float | None:
    """Taxa mensal equivalente à anual (12 pagamentos iguais)."""
    return None if dy_ano is None else dy_ano / 12


def _selo_ir(r: Resultado) -> str:
    if r.extras.get("isento", True):
        return '<span class="ir isento">ISENTO DE IR</span>'
    return f'<span class="ir trib">TEM IR · JCP {pct(r.extras.get("ir_jcp"), 1)}</span>'


def _conta_ir(r: Resultado) -> str:
    """Linha com o que cai na conta: sempre o líquido; se tiver IR, a conta bruto − IR = líquido."""
    liq = r.dy12
    if liq is None:
        return ""
    if r.extras.get("isento", True):
        motivo = ("rendimento de FII é isento para pessoa física" if r.classe == "FII"
                  else "só dividendos, sem JCP")
        return (f'<div class="conta ok"><b>Líquido: {pct(liq)} ao ano · {pct(_mes(liq), 2)} ao mês</b>'
                f'<span>Isento — {motivo}. O que aparece é o que cai na conta.</span></div>')
    bruto = r.extras.get("dy_bruto") or liq
    ir = bruto - liq
    jcp = r.extras.get("jcp_frac") or 0
    return (f'<div class="conta trib"><b>Líquido: {pct(liq)} ao ano · {pct(_mes(liq), 2)} ao mês</b>'
            f'<span>Bruto {pct(bruto)} − IR {dec(ir * 100, 1)} p.p. = líquido {pct(liq)} ao ano '
            f'({pct(_mes(bruto), 2)} − {dec(ir * 100 / 12, 2)} p.p. = {pct(_mes(liq), 2)} ao mês). '
            f'{pct(jcp, 0)} dos proventos vieram como JCP, com {pct(r.extras.get("ir_jcp"), 1)} de IR retido '
            f'na fonte no próprio pagamento; dividendos são isentos.</span></div>')


def _tags(r: Resultado, listas: list[str], atual: str | None) -> str:
    t = [f'<span class="tag">{_e(r.classe)} · {_e(r.tipo if r.classe == "FII" else r.segmento)}</span>']
    if r.classe == "FII" and r.segmento:
        t.append(f'<span class="tag">{_e(r.segmento)}</span>')
    outras = [c for c in listas if c != atual]
    for c in outras:
        t.append(f'<span class="tag t-{COR[c]}">também em {_e(CATEGORIAS[c][0])}</span>')
    return "".join(t)


def _card(s: Sugestao, rank: int, cat: str, mapa: dict) -> str:
    r = s.r
    fii = r.classe == "FII"
    quinta = (_metrica("P/VP", dec(r.pvp), "pos" if (r.pvp or 9) < 1 else "") if fii
              else _metrica("ROE", pct(r.extras.get("roe")), ""))
    metricas = "".join([
        _metrica("Preço", brl(r.preco)),
        _metrica("Teto Barsi", brl(r.teto)),
        _metrica("Margem", pct(r.margem, 0, True), _sinal(r.margem)),
        _metrica("DY líquido 12m", pct(r.dy12), "pos", f"{pct(_mes(r.dy12), 2)} ao mês"),
        quinta,
        _metrica("Queda 52s", pct(r.queda52, 0), "neg" if (r.queda52 or 0) < -0.10 else ""),
    ])
    motivos = "".join(f"<li>{_e(m)}</li>" for m in s.motivos)
    alertas = "".join(f'<div class="alerta">{_e(a)}</div>' for a in r.alertas)
    if s.partes:
        barras = "".join(
            f'<div class="pt"><span>{_e(k)}</span><i><u style="--w:{max(0, v) / mx * 100:.0f}%"></u></i>'
            f'<b>{dec(v, 0)}/{mx}</b></div>' if mx else
            f'<div class="pt"><span>{_e(k)}</span><i></i><b class="neg">{dec(v, 0)}</b></div>'
            for k, (v, mx) in s.partes.items())
        motivos = f'</ul><div class="partes">{barras}</div><ul class="why">' + motivos
    return f"""<article class="card">
  <div class="card-h">
    <span class="rank c-{COR[cat]}">{rank}</span>
    <div class="id"><div class="tk">{_e(r.ticker)} {_selo_ir(r)}</div><div class="nm">{_e(r.nome.title()[:44])}</div></div>
    <div class="nota"><b>{dec(s.nota, 0)}</b><span>nota</span><i style="--w:{s.nota:.0f}%" class="c-{COR[cat]}"></i></div>
  </div>
  <div class="tags">{_tags(r, mapa.get(r.ticker, []), cat)}</div>
  {_conta_ir(r)}
  <div class="mgrid">{metricas}</div>
  <ul class="why">{motivos}</ul>
  {alertas}
  <div class="lnk">{_links(r)}</div>
</article>"""


def _cabecalho_categoria(cat: str, contexto: dict, qtd: int) -> str:
    titulo, sub, desc = CATEGORIAS[cat]
    extra = ""
    if cat == "juros":
        extra = _linha_juros(contexto)
    return f"""<div class="hero h-{COR[cat]}">
  <div><div class="eyebrow">{_e(sub)}</div><h2>{_e(titulo)}</h2><p>{_e(desc)}</p></div>
  <div class="hero-n"><b>{qtd}</b><span>sugestões</span></div>
  {extra}
</div>"""


def _linha_juros(contexto: dict) -> str:
    ciclo, focus = contexto.get("selic_ciclo"), contexto.get("focus") or []
    passos = []
    if ciclo:
        passos.append(("Há 12 meses", ciclo["ha_12m"]))
        passos.append(("Hoje", ciclo["atual"]))
    passos += [(f"Focus {ano}", v) for ano, v in focus[1:4]]
    if not passos:
        return ""
    maior = max(v for _, v in passos)
    base = min(v for _, v in passos) * 0.7
    barras = "".join(
        f'<div class="step"><i style="--h:{(v - base) / (maior - base) * 100:.0f}%"></i><b>{pct(v, 2)}</b><span>{_e(n)}</span></div>'
        for n, v in passos)
    return f'<div class="steps">{barras}</div>'


def _pagina_categoria(cat: str, sug: list[Sugestao], contexto: dict, mapa: dict) -> str:
    if sug:
        cards = "".join(_card(s, i, cat, mapa) for i, s in enumerate(sug, 1))
        corpo = f'<div class="grid">{cards}</div>'
    else:
        corpo = '<div class="vazio">Nenhum ativo limpo atende a todos os critérios desta lista hoje.</div>'
    return f'<section class="page" id="p-{cat}" hidden>{_cabecalho_categoria(cat, contexto, len(sug))}{corpo}</section>'


def _composicao(itens: list[Sugestao]) -> str:
    grupos: dict[str, int] = {}
    for s in itens:
        g = s.r.classe if s.r.classe == "Ação" else f"FII {s.r.tipo.lower()}"
        grupos[g] = grupos.get(g, 0) + 1
    return " · ".join(f"{n} {g}" for g, n in sorted(grupos.items(), key=lambda x: -x[1]))


def _pagina_compra(compra: list[Sugestao], fora: list[Sugestao], contexto: dict, mapa: dict,
                   max_grupo: int) -> str:
    if compra:
        cards = "".join(_card(s, i, "compra", mapa) for i, s in enumerate(compra, 1))
        corpo = f'<div class="grid">{cards}</div>'
    else:
        corpo = '<div class="vazio">Nenhum ativo passa hoje em todos os filtros do ranking de compra.</div>'
    nota_fora = ""
    if fora:
        nomes = ", ".join(f"{s.r.ticker} ({dec(s.nota, 0)})" for s in fora[:12])
        nota_fora = (f'<div class="panel mut">Fora pelo limite de {max_grupo} por setor/segmento '
                     f'(para não concentrar a carteira): {_e(nomes)}</div>')
    info = (f'<div class="panel"><b>Composição:</b> {_e(_composicao(compra))}. '
            '<span class="mut">Nota = Renda recorrente líquida (35) + Estabilidade dos proventos (30) + '
            'Qualidade (20) + Preço de entrada (15) − alertas. Qualidade é medida por números oficiais da CVM '
            '(ROE, payout, dívida, vacância, prazo dos contratos) — o motor não avalia o gestor '
            'em si: leia o relatório gerencial antes de comprar.</span></div>')
    return (f'<section class="page" id="p-compra" hidden>{_cabecalho_categoria("compra", contexto, len(compra))}'
            f'{info}{corpo}{nota_fora}</section>')


def _visao_geral(sug: dict, mapa: dict, kpis: str, compra: list[Sugestao] | None = None) -> str:
    consenso = sorted(((t, c) for t, c in mapa.items() if len(c) >= 2), key=lambda x: -len(x[1]))
    chips = "".join(
        f'<div class="cons"><b>{_e(t)}</b><span>{" · ".join(_e(CATEGORIAS[x][0]) for x in c)}</span></div>'
        for t, c in consenso) or '<span class="mut">Nenhum ativo aparece em mais de uma lista hoje.</span>'
    paineis = []
    for cat, itens in sug.items():
        titulo, sub, _ = CATEGORIAS[cat]
        linhas = "".join(
            f'<a class="mini" href="#{cat}"><span class="rank sm c-{COR[cat]}">{i}</span>'
            f'<b>{_e(s.r.ticker)}</b><span class="mut">{_e(s.r.classe)} · DY líq. {pct(s.r.dy12)} · '
            f'{"isento" if s.r.extras.get("isento", True) else "tem IR"}</span><em>{dec(s.nota, 0)}</em></a>'
            for i, s in enumerate(itens[:5], 1)) or '<div class="mut pad">Sem sugestões hoje.</div>'
        paineis.append(f"""<div class="panel b-{COR[cat]}">
  <div class="panel-h"><div><div class="eyebrow">{_e(sub)}</div><h3>{_e(titulo)}</h3></div>
  <a class="ver" href="#{cat}">Ver lista ({len(itens)}) →</a></div>{linhas}</div>""")
    destaque = ""
    if compra:
        linhas = "".join(
            f'<a class="mini" href="#compra"><span class="rank sm c-cmp">{i}</span>'
            f'<b>{_e(s.r.ticker)}</b><span class="mut">{_e(s.r.classe)} · {_e(s.r.tipo)} · DY líq. {pct(s.r.dy12)} '
            f'({pct(_mes(s.r.dy12), 2)}/mês) · {"isento" if s.r.extras.get("isento", True) else "tem IR"}'
            f'</span><em>{dec(s.nota, 0)}</em></a>'
            for i, s in enumerate(compra[:10], 1))
        destaque = f"""<div class="panel b-cmp topo">
  <div class="panel-h"><div><div class="eyebrow">Só compra · foco em dividendos</div><h3>Ranking de compra — top 10</h3></div>
  <a class="ver" href="#compra">Ver ranking ({len(compra)}) →</a></div><div class="duas">{linhas}</div></div>"""
    return f"""<section class="page" id="p-visao">
<div class="kpis">{kpis}</div>{destaque}
<div class="panel"><div class="panel-h"><div><div class="eyebrow">Consenso</div>
<h3>Aparecem em mais de uma lista</h3></div></div><div class="conswrap">{chips}</div></div>
<div class="quad">{''.join(paineis)}</div></section>"""


def _td(valor: str, chave: float | None, cls: str = "") -> str:
    v = "" if chave is None else f"{chave:.6f}"
    return f'<td class="n {cls}" data-v="{v}">{valor}</td>'


def _radar(resultados: list[Resultado], mapa: dict) -> str:
    aprovados = sorted((r for r in resultados if limpo(r)), key=lambda r: -r.score)
    linhas = []
    for r in aprovados:
        listas = " ".join(f'<span class="dot c-{COR[c]}" title="{_e(CATEGORIAS[c][0])}"></span>'
                          for c in mapa.get(r.ticker, []))
        linhas.append(
            f'<tr data-busca="{_e((r.ticker + " " + r.nome + " " + r.segmento + " " + r.tipo).lower())}">'
            f'<td><b>{_e(r.ticker)}</b><div class="sub">{_e(r.nome.title()[:34])}</div></td>'
            f'<td>{_e(r.classe)}</td><td>{_e(r.tipo)}</td>'
            + _td(brl(r.preco), r.preco) + _td(brl(r.teto), r.teto)
            + _td(pct(r.margem, 0, True), r.margem, _sinal(r.margem))
            + _td(f'<b class="pos">{pct(r.dy12)}</b>', r.dy12) + _td(pct(_mes(r.dy12), 2), _mes(r.dy12))
            + f'<td>{_selo_ir(r)}</td>'
            + _td(pct(r.extras.get("dy_bruto") or r.dy12), r.extras.get("dy_bruto") or r.dy12)
            + _td(pct(r.dy_medio), r.dy_medio)
            + _td(dec(r.pvp), r.pvp) + _td(pct(r.extras.get("roe")), r.extras.get("roe"))
            + _td(pct(r.queda52, 0), r.queda52)
            + _td(f"{dec(r.queda_rel * 100, 0)} p.p." if r.queda_rel is not None else "—", r.queda_rel)
            + _td(pct(r.tendencia, 0), r.tendencia)
            + _td(pct(r.cresc_dpa, 1, True), r.cresc_dpa, _sinal(r.cresc_dpa))
            + _td(f'<span class="score">{dec(r.score, 0)}</span>', r.score)
            + f"<td>{listas}</td></tr>")
    cab = ["Ativo", "Classe", "Tipo", "Preço", "Teto", "Margem", "DY líquido", "Líquido/mês", "IR", "DY bruto",
           "DY médio", "P/VP", "ROE",
           "Queda 52s", "vs índice", "Renda vs média", "Cresc. proventos", "Score", "Listas"]
    ths = "".join(f"<th>{c}</th>" for c in cab)
    return f"""<section class="page" id="p-radar" hidden>
<div class="hero h-neu"><div><div class="eyebrow">Radar</div><h2>Todos os ativos aprovados</h2>
<p>Passaram em todos os filtros (perenidade, renda preservada, sem alerta grave, abaixo do teto). Clique no cabeçalho para ordenar.</p></div>
<div class="hero-n"><b>{len(aprovados)}</b><span>limpos</span></div></div>
<div class="panel"><input id="busca" class="busca" placeholder="Filtrar por ticker, nome, segmento...">
<div class="tw"><table class="sort" id="tabRadar"><thead><tr>{ths}</tr></thead><tbody>{''.join(linhas)}</tbody></table></div></div>
</section>"""


def _descartes(resultados: list[Resultado]) -> str:
    furadas = [r for r in resultados if not limpo(r)]
    ordem = {ESTRUTURAL: 0, DESCARTADO: 2}
    furadas.sort(key=lambda r: (ordem.get(r.status, 1), r.ticker))
    linhas = []
    for r in furadas:
        if r.status == ESTRUTURAL:
            rot, cls = "Renda caindo", "neg"
        elif r.status == DESCARTADO:
            rot, cls = "Reprovado", "mut"
        elif r.alertas_graves:
            rot, cls = "Alerta grave", "warn"
        else:
            rot, cls = "Acima do teto", "mut"
        motivos = r.descarte + r.alertas_graves
        if r.status != DESCARTADO and r.margem is not None and r.margem < 0:
            bruto = f" (pelos proventos pagos seria {brl(r.teto_bruto)})" if r.teto_bruto else ""
            motivos = motivos + [f"Preço {brl(r.preco)} acima do teto {brl(r.teto)}{bruto}"]
        if not motivos:
            motivos = [f"Preço {brl(r.preco)} acima do teto {brl(r.teto)}"]
        linhas.append(
            f"<tr><td><b>{_e(r.ticker)}</b><div class='sub'>{_e(r.nome.title()[:34])}</div></td>"
            f"<td>{_e(r.classe)}</td><td><span class='pill {cls}'>{rot}</span></td>"
            f"<td class='n'>{pct(r.dy12)} {_selo_ir(r)}</td><td class='n'>{pct(r.queda52, 0)}</td>"
            f"<td class='mot'>{'<br>'.join(_e(m) for m in motivos)}</td></tr>")
    return f"""<section class="page" id="p-furadas" hidden>
<div class="hero h-neg"><div><div class="eyebrow">3º Axioma de Zurique</div><h2>Furadas e descartes</h2>
<p>FIIs de tijolo e ações BESST que chegaram à análise mas foram barrados: renda caindo junto com a cota,
prejuízo, payout insustentável, dívida ou vacância altas, corte recente ou preço acima do teto. Nenhum deles entra em lista
de sugestão. Fundos de papel, de fundos e híbridos nem entram na análise.</p></div>
<div class="hero-n"><b>{len(furadas)}</b><span>barrados</span></div></div>
<div class="panel"><div class="tw"><table><thead><tr><th>Ativo</th><th>Classe</th><th>Situação</th><th>DY líquido</th>
<th>Queda 52s</th><th>Motivo</th></tr></thead><tbody>{''.join(linhas)}</tbody></table></div></div></section>"""


CSS = """
:root{--bg:#e6e8ec;--panel:#fff;--panel2:#f3f4f6;--tx:#14181d;--mut:#616975;--ln:#d8dce2;
--top:#18212c;--top2:#222d3a;--toptx:#e8edf3;--topmut:#93a0b1;--pos:#15803d;--neg:#b91c1c;--warn:#b45309;
--deb:#c2410c;--jur:#1d4ed8;--div:#15803d;--lon:#7c3aed;--cmp:#0f766e;--neu:#475569;--sh:0 1px 2px rgba(16,24,40,.06),0 1px 3px rgba(16,24,40,.08)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){%DARK%}}
:root[data-theme="dark"]{%DARK%}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 "Inter","Segoe UI",system-ui,-apple-system,sans-serif}
body::before{content:"";position:fixed;inset:0 0 auto;height:env(safe-area-inset-top,0px);z-index:10;background:var(--top)}
a{color:inherit}.mut{color:var(--mut)}.pos{color:var(--pos)}.neg{color:var(--neg)}.warn{color:var(--warn)}
.top{background:var(--top);color:var(--toptx);position:sticky;top:env(safe-area-inset-top,0px);z-index:5;box-shadow:0 2px 8px rgba(0,0,0,.25)}
.top-in,.ctx-in,main,footer{padding-left:max(20px,env(safe-area-inset-left,0px));padding-right:max(20px,env(safe-area-inset-right,0px))}
.top-in{max-width:1440px;margin:0 auto;padding-block:0;display:flex;align-items:center;gap:22px;height:60px}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:16px;white-space:nowrap}
.logo{width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,#0f766e,#1d4ed8);display:grid;place-items:center;font-size:13px;color:#fff}
.brand small{display:block;font-weight:400;font-size:11px;color:var(--topmut)}
nav{display:flex;gap:2px;overflow-x:auto;flex:1}
nav a{padding:8px 13px;border-radius:7px;text-decoration:none;color:var(--topmut);font-weight:500;white-space:nowrap;font-size:13.5px}
nav a:hover{background:var(--top2);color:var(--toptx)}nav a.on{background:var(--top2);color:#fff;box-shadow:inset 0 -2px 0 #2dd4bf}
.stamp{font-size:12px;color:var(--topmut);white-space:nowrap}
.ctx{background:var(--top2);color:var(--toptx);border-top:1px solid rgba(255,255,255,.06)}
.ctx-in{max-width:1440px;margin:0 auto;padding-block:8px;display:flex;gap:24px;overflow-x:auto;font-size:12.5px}
.ctx-in div{white-space:nowrap}.ctx-in span{color:var(--topmut);margin-right:6px}
.ctx-in .dn{color:#4ade80}.ctx-in .up{color:#f87171}
main{max-width:1440px;margin:0 auto;padding-block:22px 40px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:16px}
.kpi{background:var(--panel);border-radius:12px;padding:14px 16px;box-shadow:var(--sh);border:1px solid var(--ln)}
.kpi span{font-size:12px;color:var(--mut)}.kpi b{display:block;font-size:26px;line-height:1.2;margin-top:2px}
.kpi small{font-size:11.5px;color:var(--mut)}
.panel{background:var(--panel);border:1px solid var(--ln);border-radius:12px;box-shadow:var(--sh);padding:16px;margin-bottom:16px}
.panel-h{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:10px}
.panel h3{margin:0;font-size:16px}.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);font-weight:600}
.ver{font-size:12.5px;text-decoration:none;color:var(--mut);font-weight:600}.ver:hover{color:var(--tx)}
.quad{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
@media (max-width:1000px){.quad{grid-template-columns:1fr}}
.quad .panel{border-top:3px solid var(--neu);margin:0}
.b-deb{border-top-color:var(--deb)!important}.b-jur{border-top-color:var(--jur)!important}
.b-div{border-top-color:var(--div)!important}.b-lon{border-top-color:var(--lon)!important}
.mini{display:grid;grid-template-columns:26px 74px 1fr auto;gap:10px;align-items:center;padding:8px 6px;border-radius:8px;text-decoration:none;border-top:1px solid var(--ln)}
.mini:hover{background:var(--panel2)}.mini em{font-style:normal;font-weight:700;font-size:15px}.mini .mut{font-size:12.5px}
.pad{padding:10px 6px}
.conswrap{display:flex;flex-wrap:wrap;gap:8px}
.cons{background:var(--panel2);border:1px solid var(--ln);border-radius:10px;padding:8px 12px}
.cons b{display:block;font-size:15px}.cons span{font-size:12px;color:var(--mut)}
.rank{display:grid;place-items:center;width:30px;height:30px;border-radius:8px;color:#fff;font-weight:700;font-size:14px;flex:none}
.rank.sm{width:24px;height:24px;font-size:12px;border-radius:6px}
.c-deb{background:var(--deb)}.c-jur{background:var(--jur)}.c-div{background:var(--div)}.c-lon{background:var(--lon)}
.c-cmp{background:var(--cmp)}.h-cmp{border-left-color:var(--cmp)}.b-cmp{border-top:3px solid var(--cmp)!important}.t-cmp{color:var(--cmp)}
.duas{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));column-gap:24px}
@media (max-width:1000px){.duas{grid-template-columns:1fr}}
.partes{display:grid;gap:4px;padding:8px 10px;background:var(--panel2);border:1px solid var(--ln);border-radius:9px}
.pt{display:grid;grid-template-columns:88px 1fr 48px;gap:8px;align-items:center;font-size:12px}
.pt span{color:var(--mut)}.pt b{text-align:right;font-variant-numeric:tabular-nums}
.pt i{display:block;height:6px;background:var(--ln);border-radius:3px;overflow:hidden}
.pt u{display:block;height:100%;width:var(--w);background:var(--cmp);text-decoration:none}
.hero{display:flex;flex-wrap:wrap;gap:18px;justify-content:space-between;align-items:center;background:var(--panel);
border:1px solid var(--ln);border-left:5px solid var(--neu);border-radius:12px;padding:18px 22px;margin-bottom:16px;box-shadow:var(--sh)}
.hero h2{margin:2px 0 4px;font-size:22px}.hero p{margin:0;color:var(--mut);max-width:820px}
.hero-n{text-align:center;min-width:90px}.hero-n b{display:block;font-size:34px;line-height:1}.hero-n span{font-size:12px;color:var(--mut)}
.h-deb{border-left-color:var(--deb)}.h-jur{border-left-color:var(--jur)}.h-div{border-left-color:var(--div)}
.h-lon{border-left-color:var(--lon)}.h-neg{border-left-color:var(--neg)}.h-neu{border-left-color:var(--neu)}
.steps{display:flex;gap:14px;align-items:flex-end;height:110px;width:100%;padding-top:6px;border-top:1px solid var(--ln)}
.step{display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;width:92px}
.step i{display:block;width:34px;height:calc(var(--h)*.62);background:var(--jur);border-radius:5px 5px 0 0;opacity:.85}
.step b{font-size:13px;margin-top:4px}.step span{font-size:11px;color:var(--mut)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:16px}
.card{background:var(--panel);border:1px solid var(--ln);border-radius:12px;padding:16px;box-shadow:var(--sh);display:flex;flex-direction:column;gap:10px}
.card-h{display:flex;align-items:center;gap:12px}.id{flex:1;min-width:0}
.tk{font-size:20px;font-weight:700;letter-spacing:.01em}.nm{font-size:12px;color:var(--mut);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.nota{text-align:right;min-width:62px}.nota b{font-size:24px;line-height:1}.nota span{display:block;font-size:10.5px;color:var(--mut);text-transform:uppercase}
.nota i{display:block;height:4px;border-radius:2px;margin-top:4px;width:var(--w);margin-left:auto}
.tags{display:flex;flex-wrap:wrap;gap:6px}
.tag{font-size:11.5px;background:var(--panel2);border:1px solid var(--ln);border-radius:20px;padding:2px 9px;color:var(--mut)}
.t-deb{color:var(--deb)}.t-jur{color:var(--jur)}.t-div{color:var(--div)}.t-lon{color:var(--lon)}
.mgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--ln);border:1px solid var(--ln);border-radius:9px;overflow:hidden}
.m{background:var(--panel2);padding:7px 10px}.m span{display:block;font-size:11px;color:var(--mut)}
.m b{font-size:14.5px;font-variant-numeric:tabular-nums}.m small{display:block;font-size:11px;color:var(--mut)}
.ir{display:inline-block;vertical-align:middle;font-size:10.5px;font-weight:700;letter-spacing:.04em;border-radius:5px;padding:2px 7px;margin-left:6px;white-space:nowrap}
.ir.isento{background:color-mix(in srgb,var(--pos) 16%,transparent);color:var(--pos);border:1px solid color-mix(in srgb,var(--pos) 45%,transparent)}
.ir.trib{background:color-mix(in srgb,var(--warn) 16%,transparent);color:var(--warn);border:1px solid color-mix(in srgb,var(--warn) 45%,transparent)}
.conta{border-radius:9px;padding:8px 11px;font-size:12.5px;display:grid;gap:2px}
.conta b{font-size:14px}.conta span{color:var(--mut)}
.conta.ok{background:color-mix(in srgb,var(--pos) 9%,transparent);border-left:3px solid var(--pos)}
.conta.trib{background:color-mix(in srgb,var(--warn) 10%,transparent);border-left:3px solid var(--warn)}
.why{margin:0;padding-left:18px;font-size:13px}.why li{margin:2px 0}
.alerta{font-size:12px;color:var(--warn);background:color-mix(in srgb,var(--warn) 10%,transparent);border-radius:7px;padding:5px 9px}
.lnk{display:flex;gap:14px;font-size:12px;margin-top:auto;padding-top:8px;border-top:1px solid var(--ln)}
.lnk a{text-decoration:none;color:var(--mut);font-weight:600}.lnk a:hover{color:var(--tx)}
.vazio{background:var(--panel);border:1px dashed var(--ln);border-radius:12px;padding:30px;text-align:center;color:var(--mut)}
.busca{width:100%;max-width:380px;padding:9px 12px;border:1px solid var(--ln);border-radius:8px;background:var(--panel2);color:var(--tx);font:inherit;margin-bottom:12px}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;min-width:1100px}
th,td{padding:8px 10px;border-bottom:1px solid var(--ln);text-align:left;vertical-align:top}
th{font-size:11.5px;color:var(--mut);font-weight:600;white-space:nowrap;background:var(--panel2);user-select:none}
table.sort th{cursor:pointer}table.sort th:hover{color:var(--tx)}
tbody tr:hover{background:var(--panel2)}td.n{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.sub{font-size:11px;color:var(--mut)}.mot{font-size:12.5px;color:var(--mut);min-width:340px}
.score{display:inline-block;min-width:30px;text-align:center;background:var(--panel2);border:1px solid var(--ln);border-radius:6px;padding:0 6px;font-weight:600}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:3px}
.pill{font-size:11.5px;font-weight:600;border-radius:20px;padding:2px 9px;background:var(--panel2);border:1px solid var(--ln);white-space:nowrap}
footer{max-width:1440px;margin:0 auto;padding-block:0 40px;font-size:12px;color:var(--mut)}
footer .panel{font-size:12.5px}footer ul{margin:6px 0 0;padding-left:18px}footer li{margin:3px 0}
@media (max-width:700px){.top-in{height:auto;flex-wrap:wrap;padding-block:10px}.stamp{display:none}
.top-in,main,footer,.ctx-in{padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
.quad,.grid{grid-template-columns:1fr}.mgrid{grid-template-columns:repeat(2,1fr)}}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
nav,.ctx-in{scrollbar-width:none}nav::-webkit-scrollbar,.ctx-in::-webkit-scrollbar{display:none}
"""
DARK = ("--bg:#0e1115;--panel:#171b21;--panel2:#1f242b;--tx:#e6e9ee;--mut:#98a1ad;--ln:#2a313a;--top:#0a0d11;"
        "--top2:#141920;--pos:#4ade80;--neg:#f87171;--warn:#fbbf24;--deb:#fb923c;--jur:#60a5fa;--div:#4ade80;"
        "--lon:#a78bfa;--cmp:#2dd4bf;--neu:#94a3b8;--sh:none;color-scheme:dark")
CSS = CSS.replace("%DARK%", DARK)
FONTES = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
          '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">')

JS = """
const abas=[...document.querySelectorAll('nav a')];
function mostrar(id){if(!document.getElementById('p-'+id))id='visao';
document.querySelectorAll('.page').forEach(p=>p.hidden=p.id!=='p-'+id);
abas.forEach(a=>a.classList.toggle('on',a.getAttribute('href')==='#'+id));window.scrollTo(0,0);}
document.addEventListener('click',e=>{const a=e.target.closest('a[href^="#"]');if(!a)return;e.preventDefault();
const id=a.getAttribute('href').slice(1);mostrar(id);try{history.replaceState(null,'','#'+id)}catch(_){}});
addEventListener('hashchange',()=>mostrar(location.hash.slice(1)));mostrar(location.hash.slice(1));
document.querySelectorAll('table.sort').forEach(t=>{t.querySelectorAll('th').forEach((th,i)=>{let asc=false;
th.addEventListener('click',()=>{asc=!asc;const tb=t.tBodies[0];const rows=[...tb.rows];
rows.sort((a,b)=>{const x=a.cells[i],y=b.cells[i];const vx=x.dataset.v,vy=y.dataset.v;
if(vx!==undefined&&vy!==undefined){const nx=vx===''?-1e9:+vx,ny=vy===''?-1e9:+vy;return asc?nx-ny:ny-nx;}
return asc?x.innerText.localeCompare(y.innerText):y.innerText.localeCompare(x.innerText);});
rows.forEach(r=>tb.appendChild(r));});});});
const busca=document.getElementById('busca');if(busca)busca.addEventListener('input',()=>{const q=busca.value.toLowerCase();
document.querySelectorAll('#tabRadar tbody tr').forEach(tr=>tr.hidden=!tr.dataset.busca.includes(q));});
"""


def _faixa_contexto(contexto: dict) -> str:
    itens = []
    ciclo = contexto.get("selic_ciclo")
    if ciclo:
        var = ciclo["variacao"]
        seta = "dn" if var < 0 else "up"
        itens.append(f'<div><span>Selic</span><b>{pct(ciclo["atual"], 2)}</b> '
                     f'<b class="{seta}">{dec(var * 100, 2)} p.p. em 12m</b></div>')
    elif contexto.get("selic"):
        itens.append(f'<div><span>Selic</span><b>{pct(contexto["selic"][0], 2)}</b></div>')
    for ano, v in (contexto.get("focus") or [])[1:3]:
        itens.append(f'<div><span>Focus {ano}</span><b>{pct(v, 2)}</b></div>')
    if contexto.get("ipca"):
        itens.append(f'<div><span>IPCA 12m</span><b>{pct(contexto["ipca"][0], 2)}</b></div>')
    if contexto.get("juro_real") is not None:
        itens.append(f'<div><span>Juro real esperado {contexto.get("juro_real_ano", "")}</span>'
                     f'<b>{pct(contexto["juro_real"], 2)}</b></div>')
    for nome, q in contexto.get("indices", []):
        itens.append(f'<div><span>{_e(nome)}</span><b class="{"up" if q < 0 else "dn"}">{pct(q, 0)}</b></div>')
    if contexto.get("cotacoes_ate"):
        itens.append(f'<div><span>Cotações B3 até</span><b>{_e(contexto["cotacoes_ate"])}</b></div>')
    return "".join(itens)


def gerar_html(resultados: list[Resultado], sug: dict[str, list[Sugestao]], contexto: dict,
               caminho: Path, web_caminho: Path | None = None,
               compra: tuple[list[Sugestao], list[Sugestao]] | None = None, max_grupo: int = 0) -> None:
    mapa = listas_por_ticker(sug)
    ranking, fora = compra or ([], [])
    aprovados = [r for r in resultados if limpo(r)]
    kpis_dados = [
        ("Ativos analisados", len(resultados), f"+{contexto.get('prefiltrados', 0)} fora no pré-filtro"
         if contexto.get("prefiltrados") else "seleção manual"),
        ("Aprovados limpos", len(aprovados), "passaram em todos os filtros"),
        ("Sugestões únicas", len(mapa), "somando as 4 listas"),
        ("Furadas barradas", len(resultados) - len(aprovados), "renda caindo, alertas, teto"),
    ]
    kpis = "".join(f'<div class="kpi"><span>{_e(k)}</span><b>{v}</b><small>{_e(d)}</small></div>'
                   for k, v, d in kpis_dados)

    nav = ([("visao", "Visão geral")] + ([("compra", "Ranking de compra")] if compra else [])
           + [(c, CATEGORIAS[c][0]) for c in sug] + [("radar", "Radar completo"), ("furadas", "Furadas")])
    nav_html = "".join(f'<a href="#{k}">{_e(n)}</a>' for k, n in nav)
    paginas = [_visao_geral(sug, mapa, kpis, ranking)]
    if compra:
        paginas.append(_pagina_compra(ranking, fora, contexto, mapa, max_grupo))
    paginas += [_pagina_categoria(c, itens, contexto, mapa) for c, itens in sug.items()]
    paginas += [_radar(resultados, mapa), _descartes(resultados)]

    corpo = f"""<header class="top"><div class="top-in">
  <div class="brand"><div class="logo">MB</div><div>Motor Barsi<small>Barsi · Zurique · Kiyosaki</small></div></div>
  <nav>{nav_html}</nav>
  <div class="stamp">Atualizado em {_e(contexto['data'])}</div>
</div><div class="ctx"><div class="ctx-in">{_faixa_contexto(contexto)}</div></div></header>
<main>{''.join(paginas)}</main>
<footer><div class="panel"><b>Como o motor decide</b><ul>
<li><b>Escopo</b>: só ativos físicos — FIIs de tijolo (pelo menos 67% do investido em imóveis, pelo informe da CVM) e ações de Bancos, Energia, Saneamento, Seguros e Telecom (classificação oficial da B3). Fundos de papel, de fundos e híbridos não entram.</li>
<li><b>Limpo</b> = liquidez, lucro, histórico completo de proventos, renda de 12m ≥ 80% da média de 3 anos, sem alerta grave (payout &gt; 100%, DY &gt; 25%, vacância alta, dívida alta, corte recente) e preço abaixo do teto.</li>
<li><b>DY líquido</b> (o que cai na conta): proventos de 12 meses ÷ preço, já sem o IR. <b>Isento</b>: rendimento de FII e dividendo de ação. <b>Tem IR</b>: JCP, com 17,5% retido na fonte no próprio pagamento (LC 224/2025, desde 01/01/2026) — o card mostra a conta bruto − IR = líquido. Proventos não têm tabela regressiva (ela é da renda fixa). Amortização de cotas não conta como renda.</li>
<li><b>Teto Barsi</b> = menor entre proventos de 12m e média de 3 anos ÷ yield exigido. Ações: 6% (Barsi). FII de tijolo: o maior entre 8% e o juro real esperado pelo Focus + 1 p.p. No FII, se a renda recorrente da CVM (sem ganho com venda de imóveis) cobrir menos de 90% do distribuído, o teto usa a recorrente.</li>
<li><b>Dívida</b>: FII com securitização + compras a prazo acima de 20% do ativo e empresa com dívida líquida acima de 3x o EBITDA perdem nota; acima de 40% / 4x, saem das listas.</li>
<li><b>Nota</b> 0–100 é específica de cada lista; serve para ordenar, não para comparar listas entre si.</li>
<li>O motor não lê notícias nem relatórios gerenciais: use os links de cada card para confirmar o motivo da queda antes de decidir.</li>
<li><b>Fontes, só as primárias</b>: B3 (cotações oficiais, proventos, desdobramentos, classificação setorial), CVM (informes mensais e trimestrais dos FIIs, balanços ITR/DFP das empresas) e Banco Central (Selic, IPCA, Focus). Regras ajustáveis em <code>config.toml</code>.</li>
</ul></div></footer>
<script>{JS}</script>"""
    local = (f'<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
             f'<meta name="viewport" content="width=device-width,initial-scale=1">'
             f"<title>Motor Barsi</title>{FONTES}<style>{CSS}</style></head><body>{corpo}</body></html>")
    caminho.write_text(local, encoding="utf-8")
    if web_caminho:
        # Versão para a web (Artifact do claude.ai): o host fornece doctype/head/body.
        web = (f'<title>Motor Barsi</title><meta name="claude:viewport-fit-sides" content="cover">'
               f"{FONTES}<style>{CSS}</style>{corpo}")
        web_caminho.write_text(web, encoding="utf-8")


CAMPOS_CSV = [
    ("Status", "status"), ("Classe", "classe"), ("Ticker", "ticker"), ("Nome", "nome"),
    ("Segmento", "segmento"), ("Tipo", "tipo"), ("Preço", "preco"), ("Teto", "teto"),
    ("Teto pelos proventos pagos", "teto_bruto"),
    ("Margem", "margem"), ("DPA 12m líquido", "dpa12"), ("DPA média", "dpa_media"), ("DY 12m líquido", "dy12"),
    ("DY médio", "dy_medio"), ("Expansão DY", "expansao_dy"), ("Queda 52s", "queda52"),
    ("Queda vs índice", "queda_rel"), ("P/VP", "pvp"), ("Renda vs média", "tendencia"),
    ("Cresc. proventos 3a", "cresc_dpa"), ("Liquidez", "liquidez"), ("Renda/mês R$1k", "renda_mil"),
    ("Score", "score"),
]
EXTRAS_CSV = [("Isento de IR", "isento"), ("DY 12m bruto (antes do IR)", "dy_bruto"),
              ("Yield exigido", "yield_alvo"), ("FII: % em imóveis", "fisico"),
              ("FII: renda recorrente/cota 12m", "recorrente"), ("FII: cobertura recorrente", "cobertura"),
              ("FII: ganho com vendas/cota 12m", "ganhos_venda"), ("FII: dívida/ativo", "alavancagem"),
              ("FII: vacância", "vacancia"), ("FII: receita vencendo 12m", "venc_12m"),
              ("FII: receita contratos >3 anos", "acima_36m"), ("Ação: ROE", "roe"), ("Ação: payout", "payout"),
              ("Ação: dív.líq/EBITDA", "divida_ebitda"), ("Ação: receita 3a a.a.", "cresc_receita_3a"),
              ("Ação: % em JCP", "jcp_frac"), ("Data do dado CVM", "cvm_ref")]


def gerar_csv(resultados: list[Resultado], sug: dict[str, list[Sugestao]], caminho: Path,
              compra: list[Sugestao] | None = None) -> None:
    mapa = listas_por_ticker(sug)
    pos = {s.r.ticker: (i, s.nota) for i, s in enumerate(compra or [], 1)}

    def br(v):
        if isinstance(v, float):
            return f"{v:.4f}".replace(".", ",")
        return "" if v is None else v

    with caminho.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Ranking compra", "Nota compra", "Listas"] + [c for c, _ in CAMPOS_CSV]
                   + [c for c, _ in EXTRAS_CSV] + ["Sinais", "Alertas graves", "Alertas", "Descarte"])
        for r in sorted(resultados, key=lambda r: (pos.get(r.ticker, (999,))[0],
                                                   ORDEM_STATUS.index(r.status), -r.score)):
            listas = ", ".join(CATEGORIAS[c][0] for c in mapa.get(r.ticker, []))
            rk, nt = pos.get(r.ticker, ("", ""))
            w.writerow([rk, br(nt), listas] + [br(getattr(r, a)) for _, a in CAMPOS_CSV] +
                       [br(r.extras.get(a)) for _, a in EXTRAS_CSV] +
                       [" | ".join(r.sinais), " | ".join(r.alertas_graves),
                        " | ".join(r.alertas), " | ".join(r.descarte)])


def resumo_terminal(sug: dict[str, list[Sugestao]], compra: list[Sugestao] | None = None) -> str:
    linhas = []
    if compra is not None:
        linhas.append(f"\nRANKING DE COMPRA — só compra, foco em dividendos ({len(compra)})")
        for i, s in enumerate(compra, 1):
            r = s.r
            p = " ".join(f"{k[:3]} {dec(v, 0)}" for k, (v, _) in s.partes.items())
            linhas.append(f"  {i:>2}. {r.ticker:<7} {r.classe:<4} {r.tipo[:12]:<12} {brl(r.preco):>11}  "
                          f"DY líq {pct(r.dy12):>6}  margem {pct(r.margem, 0, True):>6}  nota {dec(s.nota, 0):>3}  [{p}]")
        if not compra:
            linhas.append("  (nenhum ativo atende hoje)")
    for cat, itens in sug.items():
        linhas.append(f"\n{CATEGORIAS[cat][0].upper()} — {CATEGORIAS[cat][1]} ({len(itens)})")
        for i, s in enumerate(itens[:5], 1):
            r = s.r
            linhas.append(f"  {i}. {r.ticker:<7} {r.classe:<4} {brl(r.preco):>11}  DY líq {pct(r.dy12):>6}  "
                          f"margem {pct(r.margem, 0, True):>6}  nota {dec(s.nota, 0)}")
        if not itens:
            linhas.append("  (nenhum ativo atende hoje)")
    return "\n".join(linhas)
