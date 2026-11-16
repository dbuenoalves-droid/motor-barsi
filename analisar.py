"""Motor de Oportunidades — Barsi + Axiomas de Zurique + Kiyosaki.

Varre ações (setores BESST) e FIIs (logística, papel, renda urbana) em busca de bons
pagadores de proventos cuja cota caiu por debandada do mercado, não por piora da renda.

Uso:
    python analisar.py                     # varredura completa
    python analisar.py --abrir             # varre e abre o relatório no navegador
    python analisar.py --tickers TRXF11,BBAS3,HGLG11
    python analisar.py --so fiis
    python analisar.py --sem-cache         # ignora o cache do dia e baixa tudo de novo
"""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
import tomllib
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from motor import analise, coleta, relatorio, sugestoes

RAIZ = Path(__file__).resolve().parent


def baixar_historicos(cache: coleta.Cache, tickers: list[str], workers: int) -> dict:
    def tarefa(t):
        try:
            return t, coleta.historico(cache, t)
        except Exception as e:  # ticker sem dados no Yahoo, rede etc.
            print(f"\n  aviso: sem histórico para {t} ({e})", file=sys.stderr)
            return t, None

    saida = {}
    if not tickers:
        return saida
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (t, h) in enumerate(ex.map(tarefa, tickers), 1):
            saida[t] = h
            if sys.stdout.isatty():
                print(f"\r  histórico {i}/{len(tickers)}", end="", flush=True)
    print(f"\r  histórico {len(tickers)}/{len(tickers)} ok")
    return saida


def um_por_empresa(linhas: list[dict]) -> list[dict]:
    """Mantém só o ticker mais líquido de cada empresa (ex.: TAEE11 em vez de TAEE3/TAEE4)."""
    melhor: dict[str, dict] = {}
    for l in linhas:
        raiz = l["Papel"][:4]
        liq = analise.num(l.get("Liq.2meses")) or 0
        if raiz not in melhor or liq > (analise.num(melhor[raiz].get("Liq.2meses")) or 0):
            melhor[raiz] = l
    return list(melhor.values())


def varrer(cache, cfg, so: str | None) -> tuple[list, int]:
    workers = cfg["geral"]["workers"]
    resultados, prefiltrados = [], 0

    if so in (None, "fiis"):
        print("FIIs: lendo Fundamentus...")
        linhas = coleta.fiis(cache)
        cand = [l for l in linhas if not analise.prefiltro_fii(l, cfg)]
        prefiltrados += len(linhas) - len(cand)
        print(f"  {len(linhas)} FIIs, {len(cand)} passam no pré-filtro")
        hist = baixar_historicos(cache, [l["Papel"] for l in cand], workers)
        resultados += [analise.avaliar_fii(l, hist.get(l["Papel"]), cfg) for l in cand]

    if so in (None, "acoes"):
        print("Ações: lendo Fundamentus por setor...")
        linhas = []
        for sid in cfg["acoes"]["setores"]:
            for l in coleta.acoes(cache, sid):
                l["_setor"] = sid
                linhas.append(l)
        linhas = um_por_empresa(linhas)
        cand = [l for l in linhas if not analise.prefiltro_acao(l, cfg)]
        prefiltrados += len(linhas) - len(cand)
        print(f"  {len(linhas)} empresas nos setores escolhidos, {len(cand)} passam no pré-filtro")
        hist = baixar_historicos(cache, [l["Papel"] for l in cand], workers)
        resultados += [analise.avaliar_acao(l, hist.get(l["Papel"]), cfg, l["_setor"]) for l in cand]

    return resultados, prefiltrados


def analisar_tickers(cache, cfg, tickers: list[str]) -> list:
    fiis = {l["Papel"]: l for l in coleta.fiis(cache)}
    acoes = {l["Papel"]: l for l in coleta.acoes(cache)}
    hist = baixar_historicos(cache, tickers, cfg["geral"]["workers"])
    resultados = []
    for t in tickers:
        if t in fiis:
            resultados.append(analise.avaliar_fii(fiis[t], hist.get(t), cfg))
        elif t in acoes:
            setor = coleta.setor_da_acao(cache, t) or (None, "")
            resultados.append(analise.avaliar_acao(acoes[t], hist.get(t), cfg, setor[0], setor[1]))
        else:
            print(f"  aviso: {t} não encontrado no Fundamentus", file=sys.stderr)
    return resultados


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="Motor de oportunidades Barsi/Zurique/Kiyosaki")
    ap.add_argument("--tickers", help="analisa só estes ativos, separados por vírgula")
    ap.add_argument("--so", choices=["acoes", "fiis"], help="varre só ações ou só FIIs")
    ap.add_argument("--sem-cache", action="store_true", help="baixa tudo de novo")
    ap.add_argument("--abrir", action="store_true", help="abre o relatório no navegador")
    ap.add_argument("--config", default=str(RAIZ / "config.toml"))
    args = ap.parse_args()

    with open(args.config, "rb") as f:
        cfg = tomllib.load(f)

    cache = coleta.Cache(RAIZ / cfg["geral"]["pasta_cache"], ativo=not args.sem_cache)
    agora = dt.datetime.now()
    contexto = {
        "data": agora.strftime("%d/%m/%Y %H:%M"),
        "selic": coleta.indicador_bcb(cache, 432),
        "ipca": coleta.indicador_bcb(cache, 13522),
        "selic_ciclo": coleta.selic_ciclo(cache),
        "focus": coleta.focus_selic(cache),
    }
    cfg["_ipca"] = contexto["ipca"][0] if contexto["ipca"] else None

    cfg["_indices"] = {}
    contexto["indices"] = []
    for classe, chave, nome in (("FII", "indice_fiis", "IFIX"), ("Ação", "indice_acoes", "Ibovespa")):
        try:
            h = coleta.historico(cache, cfg["sinais"][chave]) or {}
            m = analise.metricas_historicas(h)
            if m.get("max52"):
                queda = h["precos"][-1][1] / m["max52"] - 1
                cfg["_indices"][classe] = (nome, queda)
                contexto["indices"].append((f"{nome} 52s", queda))
        except Exception as e:
            print(f"  aviso: índice {nome} indisponível ({e}); usando queda absoluta", file=sys.stderr)

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        resultados = analisar_tickers(cache, cfg, tickers)
    else:
        resultados, contexto["prefiltrados"] = varrer(cache, cfg, args.so)

    pasta = RAIZ / cfg["geral"]["pasta_relatorios"] / agora.strftime("%Y-%m-%d_%H%M")
    pasta.mkdir(parents=True, exist_ok=True)
    html_path, csv_path = pasta / "relatorio.html", pasta / "dados.csv"
    sug = sugestoes.gerar(resultados, cfg)
    web_path = RAIZ / "web" / "painel.html"
    web_path.parent.mkdir(exist_ok=True)
    relatorio.gerar_html(resultados, sug, contexto, html_path, web_path if not args.tickers else None)
    relatorio.gerar_csv(resultados, sug, csv_path)
    shutil.copyfile(html_path, pasta.parent / "ultimo_relatorio.html")

    print(relatorio.resumo_terminal(sug))
    print(f"\nRelatório: {html_path}\nPlanilha:  {csv_path}")
    if args.abrir:
        webbrowser.open(html_path.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
