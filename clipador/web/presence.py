"""Fecha o Clipador quando a aba do painel é fechada (nada fica rodando escondido).

A aba fala com o servidor a cada 5 s. Ao fechar, ela manda um "tchau" (sendBeacon):
- "tchau" e nenhuma aba voltou a falar em GRACE_S → fecha (recarregar a página não fecha: ela volta a falar);
- nenhuma conversa em IDLE_S (navegador fechado à força, PC dormiu) → fecha;
- ninguém abriu o painel em FIRST_S depois de ligar → fecha.
"""
from __future__ import annotations

import threading
import time

GRACE_S = 8
IDLE_S = 150     # aba em segundo plano pode falar só 1x por minuto (economia do navegador)
FIRST_S = 300


class Presence:
    def __init__(self, clock=time.time) -> None:
        self.clock = clock
        self.started = clock()
        self.last_seen: float | None = None
        self.bye_at: float | None = None
        self._lock = threading.Lock()

    def seen(self) -> None:
        with self._lock:
            self.last_seen = self.clock()

    def bye(self) -> None:
        with self._lock:
            self.bye_at = self.clock()

    def reason_to_exit(self) -> str | None:
        now = self.clock()
        with self._lock:
            if self.last_seen is None:
                return "ninguém abriu o painel" if now - self.started > FIRST_S else None
            if self.bye_at is not None and self.last_seen <= self.bye_at and now - self.bye_at > GRACE_S:
                return "aba do painel fechada"
            if now - self.last_seen > IDLE_S:
                return "painel sem resposta"
        return None
