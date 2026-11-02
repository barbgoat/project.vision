r"""BIapp, a interface gráfica: ver tudo o que a inspeção faz, corrigir vereditos e afinar parâmetros.

Exemplos (na raiz do projeto, com o .venv ativo e a câmara ligada):
    python gui.py
    python gui.py --config configuracoes\linha.json --modo trigger

Modos (na barra de cima; pode-se trocar a correr). Nos dois, uma decisão por saco, registada
como no main.py:
    Ao vivo     a câmara capta em contínuo e a barreira virtual separa os sacos
    Trigger     a câmara só capta com o sinal do botão ou do sensor: um saco por sinal
Vistas: Operador (o estado em grande) e Engenharia (etapas, log). Janelas à parte, pelos ícones:
histórico, câmara (exposição, ganho, trigger), definições (perfil, parâmetros) e conta.
Abre com login (interface/login.py): admin faz tudo; operador só vê a vista Operador.
O perfil escolhe-se nas definições e fica lembrado; os argumentos só dão os valores iniciais.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from nucleo.config import CAMINHO_POR_OMISSAO, RAIZ


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BIapp: interface gráfica da inspeção de sacos.")
    parser.add_argument("--config", type=Path,
                        help=f"perfil inicial (omissão: o da última vez, senão {CAMINHO_POR_OMISSAO.name})")
    parser.add_argument("--modo", choices=["ao_vivo", "trigger"],
                        help="modo inicial (omissão: disparo.modo do perfil)")
    parser.add_argument("--vista", choices=["operador", "engenharia"], default="engenharia",
                        help="vista inicial (omissão: %(default)s)")
    parser.add_argument("--saida", type=Path, default=RAIZ / "resultados",
                        help="pasta para os resultados e o correcoes.csv (omissão: resultados/)")
    parser.add_argument("--dataset", type=Path,
                        help="pasta do dataset para as correções (omissão: dataset/<nome do perfil>)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    formato = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    if sys.stderr is None:  # aberto pelo atalho (pythonw, sem consola): o log vai para um ficheiro
        args.saida.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.INFO, format=formato, filename=args.saida / "biapp.log", encoding="utf-8")
    else:
        logging.basicConfig(level=logging.INFO, format=formato)

    # Só aqui: o --help funciona mesmo sem o PySide6 instalado.
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from interface.estilo import ESTILO, ICONE, NOME_APP
    from interface.janela import Janela
    from interface.login import pedir_login

    if sys.platform == "win32":  # a barra de tarefas mostra o ícone do BIapp, não o do Python
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(NOME_APP)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(NOME_APP)
    app.setWindowIcon(QIcon(str(ICONE)))
    app.setStyle("Fusion")
    app.setStyleSheet(ESTILO)
    utilizador = pedir_login()
    if utilizador is None:
        return 0
    janela = Janela(args, utilizador, QSettings(NOME_APP, NOME_APP))  # lembra o perfil
    janela.showMaximized()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
