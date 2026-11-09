r"""Avalia o sistema no dataset de uma montagem: por saco, por módulo e por classe.

    python -m ferramentas.avaliar --dataset dataset\bancada_botao --config configuracoes\bancada_botao.json
    python -m ferramentas.avaliar --dataset dataset\bancada_botao --config ... --produto Garnier --guardar-erros

A classe real é a pasta onde está a imagem (as que a BIapp cria em "Guardar no dataset"):
ok/ = OK; uma pasta por tipo de defeito (dimensao/, risco/, rugas/, mancha/, cor/...) = NOK com
esse tipo; varios/ = NOK com mais do que um defeito. Imagens fora destas pastas (p.ex. o
fundo.png) ficam de fora. Com --produto, o nominal vem da base de dados (como na BIapp) e os
sacos são comparados com a referência dos sacos OK desse produto, se existir.

1. Por saco: matriz de confusão real (OK/NOK) × sistema (OK/NOK/ERRO/SEM_SACO).
   - Falsas rejeições: sacos OK dados como NOK, ERRO ou SEM_SACO (desperdício). Com o
     intervalo de confiança de 95 % (Wilson): com poucos sacos OK, "0 falsas rejeições"
     ainda quer dizer pouco (0 em 30 → até ~11 %).
   - Defeitos escapados: sacos NOK dados como OK (o erro mais grave).
   - Os OK que ficaram por verificar por algum módulo e os atípicos face aos sacos OK.
2. Por módulo: OK, NOK, ERRO, POR_IMPLEMENTAR e SEM_SACO; o tempo médio, o percentil 95 e o
   máximo; e de quantas falsas rejeições é responsável: o módulo a afinar.
3. Por classe de defeito: quantos o módulo responsável apanhou, quantos escaparam e
   quantos não se podem avaliar (o módulo ainda está por implementar ou desligado).
4. Tempo total por saco face a geral.orcamento_ms.

Escreve em --saida: avaliacao_sacos.csv (uma linha por imagem), avaliacao_modulos.csv e
avaliacao_classes.csv (";", UTF-8). Com --guardar-erros, a imagem anotada de cada erro em
falsas_rejeicoes/, escapados/ e falhados/ (NOK dado como ERRO ou SEM_SACO).

Ainda por fazer: a avaliação por defeito com anotações (um .json do LabelMe com o nome da
imagem): verificar se cada defeito anotado foi encontrado no sítio certo (com a folga
--tolerancia-mm) e com o tipo certo, e os fundidos e partidos. Por agora só se contam as
imagens anotadas.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

if __package__ in (None, ""):  # corrido como ficheiro: python ferramentas\avaliar.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ferramentas.referencia import configuracao_do_produto  # noqa: E402
from ferramentas.validar import _escrever, _tabela  # noqa: E402
from mestre import Mestre  # noqa: E402
from modulos.m7_saida.desenho import desenhar  # noqa: E402
from nucleo.config import CAMINHO_POR_OMISSAO, RAIZ  # noqa: E402
from nucleo.contrato import CLASSES_DATASET, Estado, Resultado, TipoDefeito  # noqa: E402
from nucleo.imagens import guardar_imagem, listar_imagens, ler_original  # noqa: E402

CLASSES = CLASSES_DATASET  # as pastas do dataset (nucleo/contrato.py)

# O módulo que tem de encontrar cada tipo de defeito (os comentários de TipoDefeito).
RESPONSAVEL: dict[str, str] = {
    TipoDefeito.DIMENSAO: "dimensoes", TipoDefeito.COR: "cor",
    TipoDefeito.RISCO: "relevo", TipoDefeito.DOBRA: "relevo", TipoDefeito.RUGAS: "relevo",
    TipoDefeito.AMASSO: "relevo", TipoDefeito.MANCHA: "manchas",
}


@dataclass
class Avaliado:
    """Uma imagem do dataset, analisada."""

    ficheiro: str
    classe: str  # a pasta: "ok", "dimensao", ..., "varios"
    estado: str  # o do sistema: OK, NOK, ERRO, SEM_SACO
    modulos: dict[str, str]  # módulo → estado
    tempos_ms: dict[str, float]  # módulo → tempo
    tempo_ms: float
    tipos: Counter[str] = field(default_factory=Counter)  # defeitos encontrados, por tipo
    nao_verificado: list[str] = field(default_factory=list)
    atipicas: list[str] = field(default_factory=list)  # Atipica.texto
    anotada: bool = False  # tem um .json do LabelMe ao lado
    mensagem: str = ""

    @property
    def real(self) -> str:
        return "OK" if self.classe == "ok" else "NOK"

    @property
    def falsa_rejeicao(self) -> bool:
        return self.real == "OK" and self.estado != "OK"

    @property
    def escapado(self) -> bool:
        return self.real == "NOK" and self.estado == "OK"

    @property
    def culpados(self) -> list[str]:
        """Os módulos responsáveis por o saco não ser OK: os NOK; senão os com ERRO; senão o M2."""
        for estados in ((Estado.NOK,), (Estado.ERRO,), (Estado.SEM_SACO,)):
            culpados = [m for m, e in self.modulos.items() if e in estados]
            if culpados:
                return culpados
        return [] if self.estado == "OK" else ["mestre"]  # p.ex. geral.exigir_todos

    def linha(self) -> dict[str, object]:
        return {"ficheiro": self.ficheiro, "classe": self.classe, "real": self.real, "sistema": self.estado,
                "erro": "falsa rejeição" if self.falsa_rejeicao else "escapado" if self.escapado else "",
                "culpados": ",".join(self.culpados), "tipos": ",".join(f"{t}:{n}" for t, n in sorted(self.tipos.items())),
                "nao_verificado": ",".join(self.nao_verificado), "atipico": "; ".join(self.atipicas),
                "tempo_ms": self.tempo_ms, **{f"{m}": e for m, e in self.modulos.items()},
                **{f"t_{m}_ms": t for m, t in self.tempos_ms.items()}, "anotada": self.anotada,
                "mensagem": self.mensagem}


def classe_de(caminho: Path, dataset: Path) -> str | None:
    """A classe de uma imagem: a primeira pasta abaixo do dataset, se for uma de CLASSES."""
    partes = caminho.relative_to(dataset).parts
    return partes[0] if len(partes) > 1 and partes[0] in CLASSES else None


def intervalo_wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """O intervalo de confiança (95 %) de uma proporção k/n, pelo método de Wilson (bom com k = 0)."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    centro = (p + z * z / (2 * n)) / (1 + z * z / n)
    meia = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centro - meia), min(1.0, centro + meia)


def taxa(k: int, n: int) -> str:
    """P.ex. "2 de 120 (1.7 %; 95 %: 0.5 a 5.9 %)"."""
    if n == 0:
        return "sem sacos"
    baixo, alto = intervalo_wilson(k, n)
    return f"{k} de {n} ({100 * k / n:.1f} %; com 95 % de confiança entre {100 * baixo:.1f} e {100 * alto:.1f} %)"


def analisar(dataset: Path, mestre: Mestre, px_por_mm: float, erros: Path | None) -> list[Avaliado]:
    """Cada imagem das pastas das classes, pelo Mestre (a imagem a geral.px_por_mm, câmara fixa)."""
    imagens = listar_imagens(dataset)
    fora = [p for p in imagens if classe_de(p, dataset) is None]
    if fora:
        print(f"{len(fora)} imagens fora das pastas das classes ficam de fora (p.ex. {fora[0].name})")
    avaliados = []
    validas = [p for p in imagens if p not in fora]
    for i, caminho in enumerate(validas, start=1):
        img = ler_original(caminho)
        if img is None:
            print(f"  {i}/{len(validas)} {caminho.name}: não é uma imagem; salto")
            continue
        r = mestre.inspecionar(img)
        a = Avaliado(str(caminho.relative_to(dataset)), classe_de(caminho, dataset), str(r.estado),
                     {x.modulo: str(x.estado) for x in r.relatorios}, {x.modulo: x.tempo_ms for x in r.relatorios},
                     r.tempo_ms, r.contagem_tipos, r.nao_verificado, [x.texto for x in r.atipicas],
                     caminho.with_suffix(".json").exists(), r.mensagem)
        avaliados.append(a)
        print(f"  {i}/{len(validas)} {a.ficheiro}: {a.real} -> {a.estado}"
              + (" FALSA REJEIÇÃO" if a.falsa_rejeicao else " ESCAPADO" if a.escapado else ""))
        if erros is not None:
            _guardar_erro(erros, caminho, a, r, px_por_mm)
    return avaliados


def _guardar_erro(erros: Path, caminho: Path, a: Avaliado, r: Resultado, px_por_mm: float) -> None:
    pasta = ("falsas_rejeicoes" if a.falsa_rejeicao else "escapados" if a.escapado
             else "falhados" if a.estado in ("ERRO", "SEM_SACO") else None)
    if pasta is not None and r.imagem is not None:
        guardar_imagem(erros / pasta / f"{a.classe}_{caminho.stem}_{a.estado}.jpg", desenhar(r, px_por_mm))


def por_saco(avaliados: list[Avaliado]) -> list[dict[str, object]]:
    """A matriz de confusão: uma linha por classe real, uma coluna por estado do sistema."""
    estados = ("OK", "NOK", "ERRO", "SEM_SACO")
    return [{"real": real, "sacos": len(grupo), **{e: sum(a.estado == e for a in grupo) for e in estados}}
            for real in ("OK", "NOK") if (grupo := [a for a in avaliados if a.real == real])]


def por_modulo(avaliados: list[Avaliado]) -> list[dict[str, object]]:
    """Os estados e os tempos de cada módulo e as falsas rejeições de que é responsável."""
    modulos = list(dict.fromkeys(m for a in avaliados for m in a.modulos))
    linhas = []
    for m in modulos:
        estados = Counter(a.modulos[m] for a in avaliados if m in a.modulos)
        tempos = [a.tempos_ms[m] for a in avaliados if m in a.tempos_ms]
        linhas.append({
            "modulo": m, **{e: estados[e] for e in ("OK", "NOK", "ERRO", "POR_IMPLEMENTAR", "SEM_SACO")},
            "t_medio_ms": float(np.mean(tempos)), "t_p95_ms": float(np.percentile(tempos, 95)),
            "t_max_ms": max(tempos),
            "falsas_rejeicoes": sum(m in a.culpados for a in avaliados if a.falsa_rejeicao),
        })
    return linhas


def por_classe(avaliados: list[Avaliado]) -> list[dict[str, object]]:
    """Por pasta de defeito: o módulo responsável, quantos apanhou, quantos escaparam, quantos não se avaliam."""
    linhas = []
    for classe in CLASSES[1:]:
        grupo = [a for a in avaliados if a.classe == classe]
        if not grupo:
            continue
        responsavel = RESPONSAVEL.get(classe, "")  # "varios": qualquer módulo
        if responsavel:
            avaliaveis = [a for a in grupo if a.modulos.get(responsavel) in ("OK", "NOK")]
            apanhados = sum(a.modulos[responsavel] == "NOK" for a in avaliaveis)
        else:
            avaliaveis = grupo
            apanhados = sum(a.estado == "NOK" for a in grupo)
        linhas.append({"classe": classe, "responsavel": responsavel or "todos", "sacos": len(grupo),
                       "apanhados_pelo_responsavel": apanhados, "rejeitados": sum(a.estado != "OK" for a in grupo),
                       "escapados": sum(a.escapado for a in grupo), "nao_avaliaveis": len(grupo) - len(avaliaveis)})
    return linhas


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Avaliar o sistema no dataset (por saco, por módulo e por classe).")
    parser.add_argument(
        "--dataset", type=Path, default=RAIZ / "dataset" / "linha",
        help=f"pasta de uma montagem, com uma subpasta por classe: {', '.join(CLASSES)} "
             "(omissão: dataset/linha; usar com a configuração da mesma montagem)",
    )
    parser.add_argument(
        "--config", type=Path, default=CAMINHO_POR_OMISSAO,
        help="valores a sobrepor aos defines (omissão: config.json, se existir)",
    )
    parser.add_argument("--produto", default="",
                        help="o produto (nome na base de dados): o seu nominal e a sua referência dos sacos OK")
    parser.add_argument("--resultados", type=Path, default=RAIZ / "resultados",
                        help="a pasta dos resultados, onde está a base de dados (omissão: resultados/)")
    parser.add_argument(
        "--saida", type=Path, default=RAIZ / "resultados" / "avaliacao",
        help="pasta para os CSV e as imagens dos erros (omissão: resultados/avaliacao/)",
    )
    parser.add_argument(
        "--tolerancia-mm", type=float, default=2.0,
        help="folga, em mm, ao comparar a posição de um defeito com a anotação (avaliação por defeito, por fazer)",
    )
    parser.add_argument(
        "--guardar-erros", action="store_true",
        help="guardar a imagem anotada de cada erro (falsas_rejeicoes/, escapados/, falhados/)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(name)s: %(message)s")
    if not args.dataset.is_dir():
        print(f"Não existe a pasta {args.dataset}")
        return 1
    try:
        cfg, produto = configuracao_do_produto(args.config, args.produto, args.resultados)
        mestre = Mestre(cfg, produto=produto)
    except (ValueError, FileNotFoundError) as erro:
        print(erro)
        return 2
    print(f"Módulos: {', '.join(mestre.nomes)}" + (f"; desligados: {', '.join(mestre.desligados)}"
                                                   if mestre.desligados else "")
          + ("; com a referência dos sacos OK" if mestre.referencia else "; sem referência dos sacos OK"))

    avaliados = analisar(args.dataset, mestre, cfg.geral.px_por_mm, args.saida if args.guardar_erros else None)
    if not avaliados:
        print(f"Nenhuma imagem nas pastas das classes de {args.dataset} ({', '.join(CLASSES)}).")
        return 1
    sacos, modulos, classes = por_saco(avaliados), por_modulo(avaliados), por_classe(avaliados)
    _escrever(args.saida / "avaliacao_sacos.csv", [a.linha() for a in avaliados])
    _escrever(args.saida / "avaliacao_modulos.csv", modulos)
    _escrever(args.saida / "avaliacao_classes.csv", classes)

    print(_tabela("Por saco (real × sistema)", sacos))
    print(_tabela("Por módulo", modulos))
    if classes:
        print(_tabela("Por classe de defeito", classes))

    ok = [a for a in avaliados if a.real == "OK"]
    nok = [a for a in avaliados if a.real == "NOK"]
    print(f"\nFalsas rejeições: {taxa(sum(a.falsa_rejeicao for a in ok), len(ok))}")
    culpados = Counter(m for a in ok if a.falsa_rejeicao for m in a.culpados)
    if culpados:
        print("  responsáveis: " + ", ".join(f"{m} {n}" for m, n in culpados.most_common()))
    if ok and not any(a.falsa_rejeicao for a in ok):
        print(f"  (0 de {len(ok)}: para mostrar que ficam abaixo de 1 % são precisos 380 sacos OK sem nenhuma)")
    if nok:
        print(f"Defeitos escapados: {taxa(sum(a.escapado for a in nok), len(nok))}")
    aprovados = [a for a in avaliados if a.estado == "OK"]
    if aprovados:
        incompletos = Counter(m for a in aprovados for m in a.nao_verificado)
        print(f"OK sem todos os módulos: {sum(bool(a.nao_verificado) for a in aprovados)} de {len(aprovados)}"
              + (f" (não verificado: {', '.join(f'{m} {n}' for m, n in incompletos.most_common())})"
                 if incompletos else ""))
        if mestre.referencia is not None:
            print(f"OK atípicos face aos sacos OK: {sum(bool(a.atipicas) for a in aprovados)} de {len(aprovados)}")
    tempos = [a.tempo_ms for a in avaliados]
    print(f"Tempo por saco: médio {np.mean(tempos):.0f} ms, p95 {np.percentile(tempos, 95):.0f} ms, "
          f"máx. {max(tempos):.0f} ms (orçamento {cfg.geral.orcamento_ms:g} ms)")
    anotadas = sum(a.anotada for a in avaliados)
    if anotadas:
        print(f"{anotadas} imagens com anotação (.json): a avaliação por defeito ainda está por fazer.")
    print(f"\nCSV em {args.saida}" + (" (e as imagens dos erros)" if args.guardar_erros else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
