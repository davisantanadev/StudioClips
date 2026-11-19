@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Instalando o Clipador GTA 6

echo.
echo  ===== CLIPADOR GTA 6 - instalacao =====
echo.

REM ---------- Python ----------
set "PY="
where py >nul 2>nul && (py -3.12 -V >nul 2>nul && set "PY=py -3.12")
if not defined PY (where py >nul 2>nul && (py -3.11 -V >nul 2>nul && set "PY=py -3.11"))
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY (
  echo [1/4] Instalando Python 3.12...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  echo.
  echo  Python instalado. FECHE esta janela e rode o install.bat de novo.
  pause & exit /b
)
echo [1/4] Python ok: %PY%

REM ---------- FFmpeg ----------
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [2/4] Instalando FFmpeg...
  winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
  echo.
  echo  FFmpeg instalado. FECHE esta janela e rode o install.bat de novo.
  pause & exit /b
)
echo [2/4] FFmpeg ok

REM ---------- Ambiente Python ----------
if not exist .venv (
  echo [3/4] Criando ambiente...
  %PY% -m venv .venv || (echo Falhou ao criar o ambiente & pause & exit /b 1)
)
echo [3/4] Instalando dependencias (pode levar alguns minutos)...
.venv\Scripts\python -m pip install -q --upgrade pip
.venv\Scripts\python -m pip install -q -e . yt-dlp || (echo Falhou ao instalar dependencias & pause & exit /b 1)
echo      Instalando Whisper local (plano B se o Groq cair)...
.venv\Scripts\python -m pip install -q faster-whisper || echo      (Whisper local nao instalou - tudo bem, o Groq faz a legenda)

REM ---------- Configuracao ----------
REM streamers.yaml, hashtags e chave tem padrao embutido; nada de criar arquivo vazio aqui.
if not exist .env if exist .env.example copy .env.example .env >nul
echo [4/4] Pronto!
echo.
echo  De dois cliques em INICIAR.bat. No painel, va em Ferramentas ^> Configuracoes:
echo   - cole a chave GRATIS do Groq (https://console.groq.com ^> API Keys ^> Create API Key)
echo   - coloque o seu @
echo  Isso fica salvo no seu usuario do Windows: atualizar o Clipador nao apaga.
echo.
pause
