r"""Corre UM módulo numa imagem (ou numa pasta) e mostra o relatório completo.

    python -m ferramentas.testar_modulo localizacao dataset\telemovel\ok\saco_001.jpg --config configuracoes\telemovel.json
    python -m ferramentas.testar_modulo relevo dataset\telemovel\rugas --config configuracoes\telemovel.json
    python -m ferramentas.testar_modulo dimensoes dataset\telemovel --config configuracoes\telemovel.json
    python -m ferramentas.testar_modulo localizacao dataset\telemovel\ok --config configuracoes\telemovel.json --guardar resultados\verificacao_m2

Corre sempre o M2 Localização (os outros precisam do Saco) e depois só o
módulo pedido, mesmo que esteja desligado no config. Mostra o estado, o tempo,
as medidas, cada defeito e, se falhar, o ficheiro e a linha onde falhou.

Com --guardar, grava nessa pasta a depuração de cada imagem (a mesma do
main.py --depurar): uma pasta por imagem com o painel.jpg (todas as etapas
lado a lado e o resumo), a imagem de cada etapa e o resultado.json.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):  # corrido como ficheiro: python ferramentas\testar_modulo.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mestre import Mestre  # noqa: E402
from modulos.m1_aquisicao import FontePasta  # noqa: E402
from modulos.m2_localizacao import Localizador  # noqa: E402
from modulos.m7_saida import Depuracao  # noqa: E402
from nucleo.config import CAMINHO_POR_OMISSAO, carregar_configuracao  # noqa: E402
from nucleo.contrato import Captura, Relatorio  # noqa: E402
from nucleo.imagens import ler_imagem  # noqa: E402


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Correr um só módulo e ver o relatório completo.")
    parser.add_argument("modulo", choices=[Localizador.nome, *Mestre.disponiveis()], help="módulo a testar")
    parser.add_argument("caminho", type=Path, help="imagem ou pasta de imagens (inclui subpastas)")
    parser.add_argument(
        "--config", type=Path, default=CAMINHO_POR_OMISSAO,
        help="valores a sobrepor aos defines (omissão: config.json, se existir)",
    )
    parser.add_argument("--guardar", type=Path, help="pasta onde guardar a depuração de cada imagem (painel...)")
    return parser


def capturas(caminho: Path) -> list[Captura]:
    """As imagens a testar: uma só, ou todas as de uma pasta."""
    if caminho.is_dir():
        with FontePasta(caminho) as fonte:
            return list(fonte)
    imagem = ler_imagem(caminho)
    if imagem is None:
        raise SystemExit(f"Não é uma imagem válida: {caminho}")
    return [Captura(imagem, str(caminho), 0.0)]


def imprimir(relatorio: Relatorio) -> None:
    """Escreve um relatório de forma legível."""
    print(f"  {relatorio.modulo:<12} {relatorio.resumo:<16} {relatorio.tempo_ms:7.1f} ms")
    if relatorio.mensagem:
        print(f"      mensagem: {relatorio.mensagem}")
    for nome, valor in relatorio.medidas.items():
        print(f"      {nome} = {valor:.2f}")
    for numero, d in enumerate(relatorio.defeitos, start=1):
        print(f"      {numero}. {d.tipo}: x={d.x_mm:.1f} y={d.y_mm:.1f} mm, "
              f"{d.comprimento_mm:.1f} x {d.largura_mm:.1f} mm, {d.descricao}")


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    try:
        cfg = carregar_configuracao(args.config)
        apenas = [] if args.modulo == Localizador.nome else [args.modulo]
        mestre = Mestre(cfg, apenas=apenas)
    except (ValueError, FileNotFoundError) as erro:
        logging.getLogger("testar_modulo").error("%s", erro)
        return 2
    depuracao = Depuracao(cfg, args.guardar, "todas") if args.guardar else None

    for captura in capturas(args.caminho):
        resultado = mestre.inspecionar(captura.imagem, guardar_intermedios=True)
        resultado.captura = captura
        print(captura.origem)
        for relatorio in resultado.relatorios:
            imprimir(relatorio)
        if depuracao is not None:
            pasta = depuracao.processar(resultado)
            print(f"      painel: {pasta / 'painel.jpg' if pasta else '(falhou; ver o log)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
