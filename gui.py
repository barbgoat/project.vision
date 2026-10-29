r"""BIapp, a interface gráfica: ver tudo o que a inspeção faz, corrigir vereditos e afinar parâmetros.

Exemplos (na raiz do projeto, com o .venv ativo):
    python gui.py
    python gui.py --pasta dataset\telemovel --config configuracoes\telemovel.json
    python gui.py --fonte video --video "D:\Vídeos\linha.mp4" --config configuracoes\linha.json --modo ao_vivo

Modos (na barra de cima; pode-se trocar a correr):
    Ao vivo     câmara/vídeo em contínuo, sem trigger: centrar o saco, luz, foco e afinar
    Produção    com o trigger (disparo): uma decisão por saco, registada como no main.py
Vistas: Operador (o estado em grande) e Engenharia (etapas, log). Janelas à parte, pelos ícones:
histórico, câmara (exposição, ganho, trigger), definições (perfil, fonte, parâmetros) e conta.
Abre com login (interface/login.py): admin faz tudo; operador só vê a vista Operador.
O perfil, a fonte e a pasta escolhem-se nas definições e ficam lembrados; os argumentos só dão os
valores iniciais.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from nucleo.config import CAMINHO_POR_OMISSAO, RAIZ


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BIapp: interface gráfica da inspeção de sacos.")
    parser.add_argument("--fonte", choices=["pasta", "video", "webcam", "spinnaker"],
                        help="origem das imagens (omissão: a da última vez, senão pasta)")
    parser.add_argument("--pasta", type=Path, help="pasta com as imagens, para --fonte pasta")
    parser.add_argument("--video", type=Path, help="ficheiro de vídeo, para --fonte video")
    parser.add_argument("--config", type=Path,
                        help=f"perfil inicial (omissão: o da última vez, senão {CAMINHO_POR_OMISSAO.name})")
    parser.add_argument("--modo", choices=["ao_vivo", "producao"], default="producao",
                        help="modo inicial (omissão: %(default)s)")
    parser.add_argument("--vista", choices=["operador", "engenharia"], default="engenharia",
                        help="vista inicial (omissão: %(default)s)")
    parser.add_argument("--saida", type=Path, default=RAIZ / "resultados",
                        help="pasta para os resultados e o correcoes.csv (omissão: resultados/)")
    parser.add_argument("--dataset", type=Path,
                        help="montagem do dataset para as correções (omissão: dataset/telemovel com o perfil "
                             "telemovel, senão dataset/blackfly)")
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
    janela = Janela(args, utilizador, QSettings(NOME_APP, NOME_APP))  # lembra o perfil, a fonte e a pasta
    janela.showMaximized()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
