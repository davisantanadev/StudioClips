@echo off
cd /d "%~dp0"
if not exist .venv (
  echo Rode o install.bat primeiro.
  pause & exit /b
)
REM abre sem janela preta; fechar a aba do painel desliga o Clipador (gravacoes inclusas)
REM se ja estiver ligado, so abre o painel no navegador
start "" ".venv\Scripts\pythonw.exe" -m clipador abrir
