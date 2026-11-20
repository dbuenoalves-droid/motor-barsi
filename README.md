# Motor de Oportunidades — Barsi · Zurique · Kiyosaki

Varre **FIIs de tijolo** e **ações de empresas BESST** procurando bons pagadores de proventos
cuja cota caiu por debandada do mercado, não por piora da renda — o padrão do TRXF11: cota
despencando (vendedores de imóveis recebendo cotas e despejando no mercado) enquanto o
rendimento mensal continua sendo pago.

**Só ativos físicos.** Fundos de papel (CRI), fundos de fundos, híbridos, fundos de ações e
qualquer outro fundo ficam de fora. O único intermediário aceito é o FII que é dono de imóveis.

## Fontes — só as primárias

Cada informação vem de **uma única fonte: a que produz o dado**. Sites agregadores
(Fundamentus, Status Invest, Yahoo, Investidor10...) só repassam esses números, com atraso e
janelas diferentes, e geravam divergências — por isso foram removidos.

| Informação | Fonte oficial |
|---|---|
| Cotações diárias, máximas, volume, IFIX/Ibovespa (ETFs XFIX11 e BOVA11) | **B3** — arquivo COTAHIST |
| Proventos (valor, tipo, data-com), desdobramentos, bonificações, grupamentos, nº de cotas/ações | **B3** — sistema de empresas e fundos listados |
| Classificação setorial das empresas | **B3** — classificação oficial |
| FII: patrimônio, valor patrimonial da cota, composição da carteira, dívida, renda recorrente, vacância, vencimento dos contratos, histórico de rendimentos | **CVM** — informes mensais e trimestrais |
| Empresas: lucro, receita, EBIT, depreciação, patrimônio, dívida, caixa | **CVM** — demonstrações ITR/DFP |
| Selic, IPCA, projeções Focus (Selic e IPCA) | **Banco Central** |

Rendimentos de FII: últimos 12 meses pela B3 (data-com exata); meses anteriores pelo informe
mensal da CVM (DY do mês × valor patrimonial da cota) — janelas que não se sobrepõem.

**Sem fonte de reserva automática, de propósito.** Quando a CVM não tem o dado (ex.: o fundo
repetiu um trimestre no informe), o motor marca "não conferido" em vez de buscar em outro
site: os sites independentes (Clube FII, Funds Explorer, Status Invest...) leem esse mesmo
informe, então repetiriam o erro, e o Clube FII proíbe coleta automática por IA no robots.txt.
Cada card de FII tem links para o relatório gerencial (B3 FundosNET) e para o Clube FII, para
consulta manual — análises e comentários de investidores ajudam a entender o motivo da queda.

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
| `--sem-cache` | baixa de novo os dados do dia (proventos, juros) |

Saída em `relatorios/AAAA-MM-DD_HHMM/`: `relatorio.html` e `dados.csv` (abre no Excel).
Requer só Python 3.11+ — nenhuma biblioteca para instalar. A primeira execução baixa ~300 MB
das fontes oficiais; depois, só o pregão do dia e os dados que mudaram.

## Acesso pela web (atualização automática)

**https://dbuenoalves-droid.github.io/motor-barsi/**

O GitHub Actions roda a varredura de segunda a sexta às 19:00 (Cuiabá) e republica o painel.

- Atualizar na hora: GitHub → aba **Actions** → *Atualizar painel* → **Run workflow**.
- Planilha do dia: `https://dbuenoalves-droid.github.io/motor-barsi/dados.csv`.

## Como o motor decide

1. **Escopo** — FII só entra se tiver pelo menos 67% do total investido em imóveis físicos
   (direto ou via SPE), pelo informe mensal da CVM; segmentos de escritórios, shoppings,
   hospital, hotel, residencial e educacional ficam fora. Ações só de Bancos, Energia,
   Saneamento, Seguros e Telecom, pela classificação oficial da B3 — e, entre os bancos, só os
   grandes (patrimônio de R$ 50 bi ou mais: Itaú, Bradesco, BB, Santander, BTG).
2. **DY líquido** — proventos de 12 meses ÷ preço, com o IR do JCP descontado (`ir_jcp`).
   Rendimento de FII é isento para pessoa física. Amortização de cotas não conta como renda.
3. **Preço teto** — menor entre proventos de 12m e média de 3 anos ÷ yield exigido.
   Ações: 6% (Barsi). FII de tijolo: 8%. Fixos, sem ligação com os juros: o objetivo é
   acumular renda por anos, não acertar o ciclo da Selic.
4. **Renda recorrente do FII** — resultado de caixa dos últimos 4 trimestres (CVM) menos
   ganhos com venda de imóveis e títulos, mesma fonte e mesma janela. Se cobrir menos de 90%
   do distribuído, o teto usa a recorrente. Informe repetido ou incompleto na CVM vira alerta
   e tira o fundo do Ranking de compra e da lista de Dividendos até ser corrigido.
5. **Dívida** — FII: securitização + compras de imóveis a prazo sobre o ativo (acima de 20%
   perde nota, acima de 40% sai). Empresa não financeira: dívida líquida/EBITDA (3x / 4x).
6. **Ruído x estrutural (3º Axioma)** — proventos de 12m abaixo de 80% da média de 3 anos:
   a cota caiu junto com a renda → **Estrutural**.
7. **Debandada específica (10º Axioma)** — caiu 10+ p.p. a mais que o próprio índice em 52
   semanas, com DY 15%+ acima da média histórica e, no FII, P/VP ≤ 0,90.

| Grupo | Significado |
|---|---|
| **Oportunidade** | passou em tudo, sem alerta grave |
| **Observar** | abaixo do teto, mas caiu junto com o mercado ou tem alerta grave |
| **Estrutural** | renda caindo junto com a cota — corte, não reze |
| **Vitrine** | bom ativo, mas caro agora |
| **Descartado** | reprovado em liquidez, lucro ou histórico |

Correções manuais (rótulo de segmento ou exclusão de um fundo) em `config.toml` → `[fiis.manual]`.
Todos os limites ficam em `config.toml`, comentados.

## Limites (leia antes de usar)

- O motor **não lê notícias nem relatórios gerenciais**. Ele aponta *onde olhar*; o motivo
  da queda precisa ser confirmado nos links oficiais de cada card.
- Os informes da CVM chegam com atraso de ~1 a 2 meses e são preenchidos pelos próprios
  fundos: erros de preenchimento (trimestre repetido, CNPJ trocado) são detectados e
  sinalizados, mas não corrigidos.
- A alíquota de IR sobre JCP muda por lei; confira e ajuste `ir_jcp` no `config.toml`.
- Ferramenta de triagem com as suas regras. Não é recomendação de investimento.
