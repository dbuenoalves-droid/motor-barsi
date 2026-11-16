@echo off
REM Duplo clique: varre o mercado e abre o relatório no navegador.
REM Para analisar ativos específicos:  buscar.bat --tickers TRXF11,HGLG11
cd /d "%~dp0"
python analisar.py --abrir %*
pause
