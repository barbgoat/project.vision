"""Servidor do dashboard: envia o estado do dashboard por SSE (server-sent events), em tempo real.

    GET /eventos   text/event-stream: um evento "estado" (o JSON de nucleo.dashboard.estado) ao
                   ligar e sempre que a base de dados muda; um comentário a cada PING_S segundos
                   mantém a ligação viva. Se a ligação cair, o cliente volta a ligar sozinho.
    GET /estado    o mesmo estado, uma vez, em JSON (para ver no browser ou com curl)

Só lê a base de dados, por isso corre com ou sem a BIapp aberta. Um só vigia
(Difusor) vê se a base de dados mudou (Repositorio.versao) e refaz o estado uma vez
para todos os clientes. Só a biblioteca do Python (http.server).

Do lado de quem recebe: eventos(url) liga-se a /eventos e devolve cada evento.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request
from collections.abc import Iterable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from nucleo.dashboard import estado
from nucleo.especificacoes import ErroRepositorio, Repositorio

log = logging.getLogger("dashboard")

PING_S = 15.0  # comentário para manter a ligação viva (e o cliente saber que o servidor está lá)
RETRY_MS = 3000  # o cliente volta a ligar ao fim disto, se a ligação cair


class Difusor:
    """Vigia a base de dados e guarda o último estado (já em JSON); acorda os clientes quando muda."""

    def __init__(self, repo: Repositorio, intervalo_s: float) -> None:
        self.repo, self.intervalo_s = repo, intervalo_s
        self.numero = 0  # sobe a cada estado novo
        self.json = ""
        self._versao: tuple | None = None
        self._condicao = threading.Condition()
        self._parar = threading.Event()

    def verificar(self) -> bool:
        """Se a base de dados mudou, refaz o estado e acorda os clientes. True se mudou."""
        versao = self.repo.versao()
        if versao == self._versao:
            return False
        texto = json.dumps(estado(self.repo), ensure_ascii=False)  # uma linha: cabe num "data:"
        with self._condicao:
            self._versao, self.json, self.numero = versao, texto, self.numero + 1
            self._condicao.notify_all()
        return True

    def vigiar(self) -> None:
        """Ciclo do vigia (numa thread), até parar()."""
        while not self._parar.is_set():
            try:
                self.verificar()
            except ErroRepositorio as erro:  # p.ex. o ficheiro ocupado: tenta outra vez a seguir
                log.warning("%s", erro)
            self._parar.wait(self.intervalo_s)

    def esperar(self, numero: int, segundos: float) -> tuple[int, str]:
        """Espera até haver um estado diferente de `numero` (ou `segundos`); devolve (número, JSON)."""
        with self._condicao:
            self._condicao.wait_for(lambda: self.numero != numero or self._parar.is_set(), segundos)
            return self.numero, self.json

    @property
    def parado(self) -> bool:
        return self._parar.is_set()

    def parar(self) -> None:
        self._parar.set()
        with self._condicao:
            self._condicao.notify_all()


class _Pedido(BaseHTTPRequestHandler):
    """Um pedido HTTP: /eventos (SSE) ou /estado (JSON)."""

    server: Servidor

    def do_GET(self) -> None:  # noqa: N802 (nome do http.server)
        if self.path == "/eventos":
            self._eventos()
        elif self.path == "/estado":
            dados = self.server.difusor.json.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)
        else:
            self.send_error(404, "Só há /eventos (SSE) e /estado (JSON)")

    def _eventos(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        difusor, numero = self.server.difusor, 0
        try:
            self._escrever(f"retry: {RETRY_MS}\n\n")
            while not difusor.parado:
                novo, texto = difusor.esperar(numero, PING_S)
                if novo != numero:
                    self._escrever(f"event: estado\nid: {novo}\ndata: {texto}\n\n")
                    numero = novo
                elif not difusor.parado:
                    self._escrever(": ping\n\n")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # o cliente fechou a janela

    def _escrever(self, texto: str) -> None:
        self.wfile.write(texto.encode("utf-8"))
        self.wfile.flush()

    def log_message(self, formato: str, *argumentos) -> None:
        log.debug("%s %s", self.address_string(), formato % argumentos)


class Servidor(ThreadingHTTPServer):
    """O servidor do dashboard: um vigia da base de dados e um cliente por thread."""

    daemon_threads = True
    allow_reuse_address = False  # no Windows deixaria dois servidores na mesma porta

    def __init__(self, repo: Repositorio, endereco: str, porta: int, intervalo_s: float) -> None:
        """OSError se a porta estiver ocupada (p.ex. por outro servidor do dashboard)."""
        super().__init__((endereco, porta), _Pedido)
        self.difusor = Difusor(repo, intervalo_s)

    @property
    def url(self) -> str:
        """O endereço para quem está neste PC, p.ex. "http://127.0.0.1:8765"."""
        endereco, porta = self.server_address[:2]
        return f"http://{'127.0.0.1' if endereco in ('0.0.0.0', '') else endereco}:{porta}"

    def iniciar(self) -> Servidor:
        """Começa a vigiar e a servir, em threads; devolve-se a si próprio."""
        self.difusor.verificar()  # o primeiro estado já está pronto quando o primeiro cliente chegar
        threading.Thread(target=self.difusor.vigiar, name="dashboard-vigia", daemon=True).start()
        threading.Thread(target=self.serve_forever, name="dashboard-servidor", daemon=True).start()
        log.info("Dashboard em %s/eventos (SSE) e %s/estado", self.url, self.url)
        return self

    def parar(self) -> None:
        self.difusor.parar()
        self.shutdown()
        self.server_close()


# --- Do lado de quem recebe ------------------------------------------------------------

def eventos(url: str, tempo_max_s: float = 2 * PING_S) -> Iterator[tuple[str, str]]:
    """Liga-se a <url>/eventos e devolve (evento, dados) à medida que chegam.

    OSError se não conseguir ligar, ou se não chegar nada (nem o ping) em tempo_max_s.
    """
    with urllib.request.urlopen(f"{url}/eventos", timeout=tempo_max_s) as resposta:
        yield from interpretar(resposta)


def interpretar(linhas: Iterable[bytes]) -> Iterator[tuple[str, str]]:
    """As linhas de um text/event-stream → (evento, dados). Os comentários (": ping") não contam."""
    evento, dados = "message", []
    for bruta in linhas:
        linha = bruta.decode("utf-8").rstrip("\r\n")
        if not linha:  # linha em branco: fim de um evento
            if dados:
                yield evento, "\n".join(dados)
            evento, dados = "message", []
        elif not linha.startswith(":"):
            campo, _, valor = linha.partition(":")
            valor = valor.removeprefix(" ")
            if campo == "event":
                evento = valor
            elif campo == "data":
                dados.append(valor)
