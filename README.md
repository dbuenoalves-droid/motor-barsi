# Motor de Oportunidades — Barsi · Zurique · Kiyosaki

Varre ações (setores BESST) e FIIs procurando **bons pagadores de proventos cuja cota
caiu por debandada do mercado, não por piora da renda** — o padrão do TRXF11: cota
despencando (vendedores de imóveis recebendo cotas e despejando no mercado) enquanto o
rendimento mensal continua sendo pago.

## Como usar

Duplo clique em `buscar.bat`, ou no terminal:

```bash
python analisar.py --abrir
```

```bash
python analisar.py --tickers TRXF11,HGLG11,BBAS3
```

| Opção | O que faz |
|---|---|
| `--abrir` | abre o relatório no navegador |
| `--tickers A,B,C` | analisa só esses ativos (mostra também por que foram descartados) |
| `--so fiis` / `--so acoes` | varre só uma classe |
| `--sem-cache` | baixa tudo de novo (por padrão, o mesmo dia reaproveita os dados) |

Saída em `relatorios/AAAA-MM-DD_HHMM/`: `relatorio.html` e `dados.csv` (abre no Excel).
O último relatório fica também em `relatorios/ultimo_relatorio.html`.

Requer só Python 3.11+ — nenhuma biblioteca para instalar.

## Acesso pela web (atualização automática)

**https://dbuenoalves-droid.github.io/motor-barsi/**

O GitHub Actions ([.github/workflows/atualizar-painel.yml](.github/workflows/atualizar-painel.yml))
roda a varredura de segunda a sexta às 19:00 (Cuiabá), depois do fechamento da B3, e
republica o painel. Não depende do seu computador ligado.

- Atualizar na hora: GitHub → aba **Actions** → *Atualizar painel* → **Run workflow**.
- Planilha do dia: `https://dbuenoalves-droid.github.io/motor-barsi/dados.csv`.
- Mudou alguma regra no `config.toml`? Envie com `git add -A`, `git commit -m "..."`, `git push`;
  a próxima atualização já usa a regra nova.

## O painel

| Aba | O que mostra |
|---|---|
| **Visão geral** | indicadores, ativos que aparecem em várias listas e o top 5 de cada lista |
| **Debandada** | padrão TRXF11: caiu muito mais que o índice com a renda preservada |
| **Queda de juros** | tijolo/FoF com desconto patrimonial e utilities de contratos longos (ciclo Selic + Focus) |
| **Dividendos** | maior DY sustentável hoje: estável, recorrente, sem crédito questionado |
| **Longo prazo** | proventos crescendo há 3+ anos, ROE alto, histórico completo |
| **Radar completo** | todos os aprovados, com ordenação e busca |
| **Furadas** | tudo que foi barrado e o motivo |

Todas as listas partem só de ativos **limpos** (sem descarte, sem renda caindo, sem alerta
grave e abaixo do teto). Critérios de cada lista em `config.toml` → `[sugestoes]`.

## Como o motor decide

1. **Perenidade (Barsi)** — ações só de Bancos, Energia, Saneamento, Seguros, Telecom;
   FIIs de logística, papel, renda urbana. Liquidez mínima, lucro, histórico de proventos.
2. **Preço teto** — menor entre proventos de 12m e média de 3 anos ÷ yield mínimo
   (ações 6%, FII tijolo 8%, FII papel 10%).
3. **Ruído x estrutural (3º Axioma)** — se os proventos de 12m caíram abaixo de 80% da
   média, a cota caiu *junto* com a renda → grupo **Estrutural**, não é pechincha.
4. **Debandada específica (10º Axioma)** — caiu 10+ p.p. a mais que o próprio índice
   (IFIX ou Ibovespa) em 52 semanas, com DY 15%+ acima da média histórica do ativo e,
   no FII, P/VP ≤ 0,90.

| Grupo | Significado |
|---|---|
| **Oportunidade** | passou em tudo, sem alerta grave |
| **Observar** | abaixo do teto, mas caiu junto com o mercado ou tem alerta grave |
| **Estrutural** | renda caindo junto com a cota — corte, não reze |
| **Vitrine** | bom ativo, mas caro agora |
| **Descartado** | reprovado em perenidade, liquidez ou histórico |

Todos os limites ficam em `config.toml`, comentados.

## Limites (leia antes de usar)

- O motor **não lê notícias nem relatórios gerenciais**. Ele aponta *onde olhar*; o
  motivo da queda precisa ser confirmado nos links do relatório.
- Alerta "DY > 1,5× FFO" em tijolo: parte da renda pode vir de venda de imóveis
  (não recorrente). É o caso a checar no TRXF11.
- Segmentos "Multicategoria/Outros" do Fundamentus misturam escritórios, FoFs etc.
- Dados: Fundamentus, Yahoo Finance e Banco Central — podem ter atraso ou falhas.
- Ferramenta de triagem educacional com as suas regras. Não é recomendação de investimento.
