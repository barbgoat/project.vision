r"""Afinar os parâmetros de UM módulo numa imagem, com barras deslizantes do OpenCV.

    python -m ferramentas.afinar relevo dataset\telemovel\rugas\saco_007.png
    python -m ferramentas.afinar localizacao "D:\Ensaios de iluminação\saco 3.png" --saida config_afinada.json

Mexer numa barra volta a correr o M2 e o módulo escolhido e mostra a imagem
anotada e uma etapa intermédia à escolha. Só a secção desse módulo é alterada.
Teclas: 1–9 escolhem a etapa intermédia, s guarda a configuração, r repõe os
valores do ficheiro, q/Esc sai.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):  # corrido como ficheiro: python ferramentas\afinar.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nucleo.config import AFINACAO, CAMINHO_POR_OMISSAO  # noqa: E402

# Barras deslizantes por módulo: (parâmetro da secção, mínimo, máximo, passo), em nucleo/config.py.
# O cv2.createTrackbar só trabalha com inteiros 0..N, por isso:
#   posição = round((valor − mínimo) / passo)   e   valor = mínimo + posição · passo
PARAMETROS = AFINACAO


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Afinar os parâmetros de um módulo numa imagem.")
    parser.add_argument("modulo", choices=list(PARAMETROS), help="módulo a afinar")
    parser.add_argument("imagem", type=Path, help="imagem a usar (aceita espaços e acentos)")
    parser.add_argument(
        "--config", type=Path, default=CAMINHO_POR_OMISSAO,
        help="valores a sobrepor aos defines (omissão: config.json, se existir)",
    )
    parser.add_argument(
        "--saida", type=Path,
        help="onde a tecla s guarda a configuração (omissão: o próprio ficheiro de --config)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)

    # Fluxo previsto:
    # 1. cfg = carregar_configuracao(args.config); img = nucleo.imagens.ler_imagem(args.imagem)
    #    (o cv2.imread falha com acentos); erro claro se a imagem não abrir.
    # 2. Janelas "parametros" (com as barras) e "resultado", com
    #    cv2.WINDOW_NORMAL para se poderem redimensionar. Uma barra por
    #    entrada de PARAMETROS[args.modulo], posicionada no valor atual de
    #    getattr(cfg, args.modulo). Nomes só em ASCII: o highgui não mostra acentos.
    # 3. Ciclo até q/Esc, com cv2.waitKey(50):
    #    a. Ler as posições das barras e convertê-las em valores.
    #    b. Se algo mudou: nova secção = dataclasses.replace(secção, **valores)
    #       e nova = dataclasses.replace(cfg, **{args.modulo: nova secção}),
    #       que valida a configuração; se for inválida (p.ex. limiar baixo >
    #       alto), mostrar o erro na imagem e não inspecionar.
    #    c. mestre = Mestre(nova, apenas=[args.modulo]) (para "localizacao",
    #       apenas=[]); resultado = mestre.inspecionar(img, guardar_intermedios=True).
    #    d. Mostrar a imagem anotada (M7 Saida.anotar) e, ao lado, a etapa
    #       intermédia escolhida com as teclas 1–9, tirada de
    #       resultado.relatorio(args.modulo).intermedios; escrever por cima o
    #       estado, o nº de defeitos por tipo e os tempos por etapa. No relevo,
    #       desenhar cada linha numa cor mostra logo se dois defeitos se
    #       fundiram ou se um se partiu.
    #    e. Teclas: s → nova.guardar(args.saida or args.config), que escreve só
    #       os valores que diferem dos defines (depois, se quiseres fixá-los,
    #       passa-os para nucleo/defines.py e apaga-os do JSON);
    #       r → recarregar o ficheiro e repor as barras; q/Esc → sair.
    # 4. cv2.destroyAllWindows().
    raise NotImplementedError("afinar.py: fluxo por implementar (ver os comentários em main()).")


if __name__ == "__main__":
    raise SystemExit(main())
