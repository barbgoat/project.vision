r"""Fazer a referência dos sacos OK de um produto (nucleo/referencia.py) a partir das suas imagens.

    python -m ferramentas.referencia --config configuracoes\bancada_botao.json --produto Garnier dataset\bancada_botao\ok
    python -m ferramentas.referencia --config configuracoes\bancada_botao.json --produto Garnier --ver

Passa cada imagem pelo Mestre (com a configuração do perfil e o nominal do produto na base de
dados), junta as medidas dos sacos que o sistema também dá como bons e guarda a média, o
desvio-padrão, o mínimo e o máximo de cada uma em <referencia.pasta>/<produto>.json. A partir daí,
a BIapp e o main.py comparam cada saco desse produto com os bons (aviso "atípico").

As imagens: só sacos OK do produto (a pasta ok/ do dataset da montagem, "Guardar no dataset" na
BIapp), pelo menos referencia.sacos_min (melhor 30 a 50), de vários sacos físicos, em posições e
rotações diferentes, com a luz e a câmara da produção. Refazer se a montagem, a luz, a escala
(px_por_mm) ou o nominal do produto mudarem: o Mestre deixa de usar uma referência que não bate certo.

Escreve também, em resultados/referencia/<produto>.csv, as medidas de cada imagem. No fim mostra
os sacos do próprio conjunto que ficam fora: a ver se são mesmo OK.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from pathlib import Path

if __package__ in (None, ""):  # corrido como ficheiro: python ferramentas\referencia.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ferramentas.validar import _escrever, _tabela  # noqa: E402
from mestre import Mestre  # noqa: E402
from nucleo.config import CAMINHO_POR_OMISSAO, RAIZ, Config, carregar_configuracao  # noqa: E402
from nucleo.contrato import Estado  # noqa: E402
from nucleo.especificacoes import ErroRepositorio, Repositorio, caminho_base_dados, carimbo, compor  # noqa: E402
from nucleo.imagens import listar_imagens, ler_original  # noqa: E402
from nucleo.referencia import MEDIDAS, Referencia, medidas_do_saco, nome_ficheiro  # noqa: E402


def configuracao_do_produto(caminho_config: Path, produto: str, resultados: Path) -> tuple[Config, str]:
    """A configuração do perfil com o nominal do produto (da base de dados, como na BIapp) e o nome do
    produto como lá está. Sem base de dados, ou sem o produto nela, fica o nominal do perfil (com aviso)."""
    cfg = carregar_configuracao(caminho_config)
    if not produto.strip():
        return cfg, ""
    caminho = caminho_base_dados(cfg, resultados)
    espec = None
    if caminho.exists():
        try:
            espec = Repositorio(caminho).procurar_produto(produto)
        except ErroRepositorio as erro:
            print(f"Aviso: {erro}")
    if espec is None:
        print(f"Aviso: o produto '{produto}' não está em {caminho}: uso o nominal do perfil "
              f"({cfg.dimensoes.comprimento_nominal_mm:g} × {cfg.dimensoes.largura_nominal_mm:g} mm).")
        return cfg, produto.strip()
    return compor(cfg, espec), espec.produto


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fazer a referência dos sacos OK de um produto.")
    parser.add_argument("pastas", nargs="*", type=Path, help="pastas com as imagens dos sacos OK (e subpastas)")
    parser.add_argument("--config", type=Path, default=CAMINHO_POR_OMISSAO, help="perfil da montagem (JSON)")
    parser.add_argument("--produto", default="", help="o produto (o nome na base de dados); vazio = sem produto")
    parser.add_argument("--resultados", type=Path, default=RAIZ / "resultados",
                        help="a pasta dos resultados, onde está a base de dados (omissão: resultados/)")
    parser.add_argument("--incluir-nok", action="store_true",
                        help="usar também os sacos que o sistema dá como NOK (por omissão ficam de fora)")
    parser.add_argument("--autor", default="", help="quem fez a referência (fica no ficheiro)")
    parser.add_argument("--ver", action="store_true", help="só mostrar a referência guardada do produto")
    return parser


def _linhas_referencia(ref: Referencia, cfg: Config) -> list[dict[str, object]]:
    return [{"medida": nome, "n": d.n, "media": d.media, "desvio_padrao": d.desvio_padrao, "minimo": d.minimo,
             "maximo": d.maximo, "atipico_se_mais_de": d.limite(nome, cfg.referencia.sigmas),
             "lado": "só acima" if MEDIDAS[nome].so_acima else "acima e abaixo"}
            for nome, d in ref.medidas.items()]


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(name)s: %(message)s")
    try:
        cfg, produto = configuracao_do_produto(args.config, args.produto, args.resultados)
    except (ValueError, FileNotFoundError) as erro:
        print(erro)
        return 2
    destino = Referencia.caminho(cfg, produto)

    if args.ver:
        if not destino.exists():
            print(f"Não há referência para '{produto or 'sem produto'}' ({destino}).")
            return 1
        ref = Referencia.carregar(destino)
        print(f"{destino}: {ref.sacos} sacos OK de {ref.origem}, {ref.data_hora} {ref.autor}".rstrip())
        if problema := ref.problema(cfg):
            print(f"NÃO É USADA: {problema}")
        print(_tabela("Sacos OK", _linhas_referencia(ref, cfg)))
        return 0

    imagens = [p for pasta in args.pastas for p in listar_imagens(pasta)]
    if not imagens:
        print("Nenhuma imagem: indicar as pastas dos sacos OK, p.ex. dataset\\bancada_botao\\ok")
        return 1
    # Sem comparar com a referência antiga: esta é a que vai ser feita.
    mestre = Mestre(replace(cfg, referencia=replace(cfg.referencia, ativo=False)), produto=produto)
    print(f"Produto '{produto or 'sem produto'}': nominal {cfg.dimensoes.comprimento_nominal_mm:g} × "
          f"{cfg.dimensoes.largura_nominal_mm:g} mm, px_por_mm {cfg.geral.px_por_mm:g}; {len(imagens)} imagens")

    por_imagem, medicoes, usadas, rejeitadas = [], [], [], []
    for i, caminho in enumerate(imagens, start=1):
        img = ler_original(caminho)
        if img is None:
            print(f"  {i}/{len(imagens)} {caminho.name}: não é uma imagem; salto")
            continue
        r = mestre.inspecionar(img)
        medidas = medidas_do_saco(r.relatorios)
        nok = any(rel.estado is Estado.NOK for rel in r.relatorios)
        usa = "dimensoes" in r.verificado_por and (not nok or args.incluir_nok)
        estado = "NOK" if nok else str(r.estado)
        por_imagem.append({"ficheiro": str(caminho), "estado": estado, "usada": usa,
                           "mensagem": r.mensagem or "; ".join(d.descricao for d in r.defeitos), **medidas})
        if usa:
            medicoes.append(medidas)
            usadas.append(caminho)
        else:
            rejeitadas.append((caminho, estado, por_imagem[-1]["mensagem"]))
        print(f"  {i}/{len(imagens)} {caminho.name}: {estado}{'' if usa else ' (fica de fora)'}")

    _escrever(args.resultados / "referencia" / f"{nome_ficheiro(produto)}.csv", por_imagem)
    if rejeitadas:
        print(f"\n{len(rejeitadas)} imagens ficaram de fora (sem medidas, ou NOK pelo sistema: se são OK, "
              "são falsas rejeições; ver python -m ferramentas.avaliar):")
        for caminho, estado, mensagem in rejeitadas:
            print(f"  {caminho.name}: {estado} {mensagem}")

    ref = Referencia.de_medicoes(produto, medicoes, cfg, ", ".join(str(p) for p in args.pastas),
                                 carimbo()[0][:19], args.autor)
    print(_tabela(f"Sacos OK usados: {len(medicoes)}", _linhas_referencia(ref, cfg)))
    if len(medicoes) < cfg.referencia.sacos_min:
        print(f"\nPoucos sacos: {len(medicoes)} de {cfg.referencia.sacos_min} (referencia.sacos_min). "
              "Juntar mais sacos OK; a referência NÃO foi guardada.")
        return 1
    ref.guardar(destino)

    fora = [(p, a) for p, m in zip(usadas, medicoes)
            if (a := ref.comparar(m, cfg.referencia.sigmas, cfg.referencia.sacos_min))]
    if fora:
        print(f"\n{len(fora)} sacos do próprio conjunto ficam atípicos (ver se são mesmo OK; se não, tirá-los e refazer):")
        for caminho, atipicas in fora:
            print(f"  {caminho.name}: " + "; ".join(a.texto for a in atipicas))
    print(f"\nReferência guardada em {destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
