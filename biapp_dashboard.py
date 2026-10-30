r"""BIapp Dashboard: o dashboard numa janela própria, fora da BIapp, em tempo real (SSE).

Exemplos (na raiz do projeto, com o .venv ativo):
    python biapp_dashboard.py                   servidor + janela (é o que o atalho abre)
    python biapp_dashboard.py --so-servidor     só o servidor, sem janela (Ctrl+C para parar)
    python biapp_dashboard.py --atalho          cria o atalho "BIapp Dashboard" no Ambiente de Trabalho

Arranca o servidor (nucleo/servidor_dashboard.py) se ainda não houver um nesta porta;
senão usa o que já está a correr. A janela liga-se a ele por SSE (GET /eventos) e
mostra cada estado novo; se a ligação cair, volta a tentar sozinha. Só mostra: criar e
editar produtos e OFs faz-se na BIapp. "Reiniciar" volta ao início e lê tudo outra vez;
"Limpar dados" (só com SAIDA_DASHBOARD_LIMPAR, em testes) apaga a base de dados, e o
servidor envia logo o estado vazio. A base de dados, o endereço e a porta vêm da
configuração (saida.base_dados, saida.dashboard_*), como no main.py.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from nucleo.config import CAMINHO_POR_OMISSAO, RAIZ, carregar_configuracao
from nucleo.especificacoes import ErroRepositorio, Repositorio, caminho_base_dados
from nucleo.servidor_dashboard import RETRY_MS, Servidor, eventos

log = logging.getLogger("dashboard")

NOME = "BIapp Dashboard"


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{NOME}: o dashboard numa janela própria, em tempo real (SSE).")
    parser.add_argument("--config", type=Path, default=CAMINHO_POR_OMISSAO,
                        help="configuração (base de dados, endereço e porta; omissão: config.json, se existir)")
    parser.add_argument("--saida", type=Path, default=RAIZ / "resultados",
                        help="pasta dos resultados, onde está a base de dados (omissão: resultados/)")
    parser.add_argument("--so-servidor", action="store_true", help="só o servidor, sem janela")
    parser.add_argument("--atalho", action="store_true", help="criar o atalho no Ambiente de Trabalho e sair")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    formato = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    if sys.stderr is None:  # aberto pelo atalho (pythonw, sem consola): o log vai para um ficheiro
        args.saida.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.INFO, format=formato, filename=args.saida / "dashboard.log",
                            encoding="utf-8")
    else:
        logging.basicConfig(level=logging.INFO, format=formato)
    if args.atalho:
        print(f"Atalho criado: {criar_atalho()}")
        return 0

    try:
        cfg = carregar_configuracao(args.config)
        repo = Repositorio(caminho_base_dados(cfg, args.saida))
    except (ValueError, FileNotFoundError, ErroRepositorio) as erro:
        log.error("%s", erro)
        return 2
    s = cfg.saida
    servidor = None
    try:
        servidor = Servidor(repo, s.dashboard_endereco, s.dashboard_porta, s.dashboard_intervalo_ms / 1000).iniciar()
        url = servidor.url
    except OSError:  # a porta está ocupada: já há um servidor do dashboard (outra janela, ou --so-servidor)
        url = f"http://127.0.0.1:{s.dashboard_porta}" if s.dashboard_endereco == "0.0.0.0" else \
            f"http://{s.dashboard_endereco}:{s.dashboard_porta}"
        log.info("Já há um servidor do dashboard em %s: a janela liga-se a ele.", url)

    try:
        if args.so_servidor:
            if servidor is None:
                log.error("A porta %d já está ocupada.", s.dashboard_porta)
                return 1
            print(f"{NOME}: {url}/eventos (SSE) e {url}/estado (JSON). Ctrl+C para parar.")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                return 0
        return _janela(url, repo, s.dashboard_limpar)
    finally:
        if servidor is not None:
            servidor.parar()


def _janela(url: str, repo: Repositorio, permitir_limpar: bool) -> int:
    # Só aqui: o --so-servidor e o --help funcionam mesmo sem o PySide6.
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout

    from interface.estilo import ESTILO, ICONE
    from interface.vista_dashboard import VistaDashboard

    class Ponte(QObject):
        """Leva o que chega por SSE (noutra thread) para a janela."""

        estado = Signal(object)
        problema = Signal(str)

    class JanelaExterna(QMainWindow):
        """A vista do dashboard, alimentada pelo servidor (SSE)."""

        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle(NOME)
            self.vista = VistaDashboard()
            centro = QWidget()
            centro.setObjectName("fundo")
            layout = QVBoxLayout(centro)
            layout.addWidget(self.vista)
            self.setCentralWidget(centro)
            self.ponte = Ponte()
            self.ponte.estado.connect(self._recebido)
            self.ponte.problema.connect(self._problema)
            self.vista.permitir_limpar(permitir_limpar)
            self.vista.reiniciar_pedido.connect(self._reiniciar)
            self.vista.limpar_pedido.connect(self._limpar)
            self.statusBar().showMessage(f"A ligar a {url}…")

        def _recebido(self, estado: dict) -> None:
            self.vista.mostrar(estado)
            self.statusBar().showMessage(f"Ligado a {url} (tempo real, SSE)")

        def _reiniciar(self) -> None:
            try:
                with urllib.request.urlopen(f"{url}/estado", timeout=5) as resposta:
                    self._recebido(json.load(resposta))
            except (OSError, ValueError) as erro:
                self._problema(f"Sem ligação a {url} ({erro})")

        def _limpar(self, tudo: bool) -> None:
            try:
                apagados = repo.limpar(tudo)
            except ErroRepositorio as erro:
                self._problema(str(erro))
                return
            # O servidor vê a mudança e envia o estado vazio por SSE, como em qualquer outra mudança.
            self.statusBar().showMessage(f"{apagados} registo(s) apagado(s): o dashboard atualiza-se num instante.")

        def _problema(self, texto: str) -> None:
            self.vista.erro("sem ligação ao servidor")
            self.statusBar().showMessage(texto)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(NOME)
    app.setWindowIcon(QIcon(str(ICONE)))
    app.setStyle("Fusion")
    app.setStyleSheet(ESTILO)
    janela = JanelaExterna()
    threading.Thread(target=receber, args=(url, janela.ponte.estado.emit, janela.ponte.problema.emit),
                     name="dashboard-sse", daemon=True).start()
    janela.resize(1200, 850)
    janela.show()
    return app.exec()


def receber(url: str, ao_estado, ao_problema) -> None:
    """Cliente SSE (numa thread): cada estado novo vai para ao_estado(dict); sem ligação, tenta outra vez."""
    while True:
        try:
            for evento, dados in eventos(url):
                if evento == "estado":
                    ao_estado(json.loads(dados))
            ao_problema("O servidor fechou a ligação: a voltar a ligar…")
        except (OSError, ValueError) as erro:
            ao_problema(f"Sem ligação a {url} ({erro}): a tentar outra vez…")
        time.sleep(RETRY_MS / 1000)


def criar_atalho() -> Path:
    """O atalho "BIapp Dashboard" no Ambiente de Trabalho: pythonw (sem consola) com este ficheiro."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    comando = (
        "$ambiente = [Environment]::GetFolderPath('Desktop'); "
        f"$caminho = Join-Path $ambiente '{NOME}.lnk'; "
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($caminho); "
        f"$s.TargetPath = '{pythonw}'; $s.Arguments = '\"{Path(__file__).resolve()}\"'; "
        f"$s.WorkingDirectory = '{RAIZ}'; $s.IconLocation = '{RAIZ / 'interface' / 'recursos' / 'biapp.ico'}'; "
        f"$s.Description = '{NOME}: o dashboard em tempo real'; $s.Save(); $caminho"
    )
    resultado = subprocess.run(["powershell", "-NoProfile", "-Command", comando], capture_output=True, text=True,
                               check=True)
    return Path(resultado.stdout.strip())


if __name__ == "__main__":
    sys.exit(main())
