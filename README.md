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
| **Visão geral** | indicadores, top 10 do ranking de compra, ativos em várias listas e o top 5 de cada lista |
| **Ranking de compra** | só compra, foco em dividendos: nota 0–100 = renda recorrente (35) + estabilidade dos proventos (30) + qualidade e gestão (20) + preço de entrada (15), no máximo 4 por setor/segmento |
| **Debandada** | padrão TRXF11: caiu muito mais que o índice com a renda preservada |
| **Queda de juros** | tijolo com desconto patrimonial e utilities de contratos longos (ciclo Selic + Focus) |
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
   (ações 6%, FII tijolo 8%, híbrido 9%, papel 10%). No tijolo e no híbrido, a base também é limitada
   ao **FFO/cota** de 12m: lucro com venda de imóvel não estica o teto. O CSV traz o
   "teto bruto" para comparação.
3. **Balanço** — FII: passivo/PL (obrigações por aquisição, CRIs, compromissadas) acima
   de 20% perde nota nas listas, acima de 50% é alerta grave. Ação não financeira: dívida
   líquida/EBITDA acima de 3x perde nota, acima de 4x é alerta grave.
4. **Ruído x estrutural (3º Axioma)** — se os proventos de 12m caíram abaixo de 80% da
   média, a cota caiu *junto* com a renda → grupo **Estrutural**, não é pechincha.
5. **Debandada específica (10º Axioma)** — caiu 10+ p.p. a mais que o próprio índice
   (IFIX ou Ibovespa) em 52 semanas, com DY 15%+ acima da média histórica do ativo e,
   no FII, P/VP ≤ 0,90.

| Grupo | Significado |
|---|---|
| **Oportunidade** | passou em tudo, sem alerta grave |
| **Observar** | abaixo do teto, mas caiu junto com o mercado ou tem alerta grave |
| **Estrutural** | renda caindo junto com a cota — corte, não reze |
| **Vitrine** | bom ativo, mas caro agora |
| **Descartado** | reprovado em perenidade, liquidez ou histórico |

**Tipo do FII** — definido pela fração do PL em imóveis físicos ("Imóveis/PL" do
Fundamentus), não pelo segmento, que chama de "Multicategoria" tanto fundo de CRI quanto
de tijolo: abaixo de 10% = **Papel**, de 10% a 50% = **Híbrido**, acima = **Tijolo**.
**Fundos de fundos (FoF) ficam fora** (`excluir_fof = true`): são reconhecidos pelo nome;
um FoF sem "FoF"/"Fundo de Fundos" no nome precisa de `tipo = "FoF"` em `[fiis.manual]`.
**Multiestratégia / "hedge fund" também ficam fora** (`nomes_multiestrategia`): carteira
mista que muda a critério do gestor. Qualquer outro fundo sai com
`TICKER = { excluir = true, motivo = "..." }` em `[fiis.manual]`, e `excluir_hibrido = true`
deixa só tijolo puro e papel puro. Correções manuais (tipo e rótulo de segmento) ficam em
`config.toml` → `[fiis.manual]`.

Na lista **Dividendos**, FII de papel é ordenado pelo DY real (descontado o IPCA 12m) e o
híbrido desconta o IPCA só da parte do PL fora de imóveis, porque a correção monetária
distribuída repõe o principal. Papel cujo FFO cobre menos de 60% do distribuído recebe
alerta e fica fora dessa lista.

**Ranking de compra** (`config.toml` → `[compra]`) — parte dos ativos limpos e exige
proventos em todos os anos, renda de 12m ≥ 90% da média, 3+ anos de histórico, FII pagando
11+ meses, papel com P/VP ≥ 0,85, ação com ROE ≥ 10% e payout ≤ 90% (liberado quando o
ROE passa de 25%). "Gestão" é medida por números (ROE, payout, dívida/EBITDA, vacância,
alavancagem, diversificação de imóveis, confiança no crédito) — não é uma avaliação do
gestor. O ranking não tem sinal de venda.

Todos os limites ficam em `config.toml`, comentados.

## Limites (leia antes de usar)

- O motor **não lê notícias nem relatórios gerenciais**. Ele aponta *onde olhar*; o
  motivo da queda precisa ser confirmado nos links do relatório.
- Alerta "DY > 1,5× FFO" em tijolo: parte da renda pode vir de venda de imóveis
  (não recorrente). É o caso a checar no TRXF11.
- Segmentos "Multicategoria/Outros" do Fundamentus misturam escritórios, FoFs etc. O tipo
  vem do Imóveis/PL, mas fundo que tem imóvel via SPE ou cotas de outro FII aparece com
  Imóveis/PL baixo — use `[fiis.manual]` para corrigir.
- Dados: Fundamentus, Yahoo Finance e Banco Central — podem ter atraso ou falhas.
- Ferramenta de triagem educacional com as suas regras. Não é recomendação de investimento.
