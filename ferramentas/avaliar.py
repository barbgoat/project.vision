"""Avalia o sistema no dataset inteiro: por saco, por módulo e, com anotações, por defeito.

    python -m ferramentas.avaliar
    python -m ferramentas.avaliar --dataset dataset --config config.json --guardar-erros

Níveis de avaliação:

1. Por saco (OK/NOK), sempre: a classe real é a pasta onde está a imagem.
   ok/ = OK; uma pasta por tipo de defeito (risco/, rugas/, mancha/, cor/...)
   = NOK com esse tipo; varios/ = NOK com mais do que um defeito.

2. Por módulo: quantas vezes cada módulo deu OK, NOK, ERRO ou
   POR_IMPLEMENTAR, o seu tempo, e que módulo é responsável por cada falsa
   rejeição. É assim que se descobre qual módulo afinar.

3. Por defeito, para as imagens que têm anotação: um ficheiro .json com o
   mesmo nome da imagem (saco_007.jpg → saco_007.json), no formato do LabelMe
   (https://github.com/wkentaro/labelme). Cada defeito é uma forma com o
   rótulo do tipo: as linhas (risco, dobra) desenham-se com "linestrip" ao
   longo do defeito; as zonas (rugas, amasso, mancha) com "polygon". Os
   defeitos do saco todo (dimensao, cor) anotam-se com um "rectangle" à volta
   do saco. Verifica-se se cada defeito foi encontrado, no sítio certo e com
   o tipo certo.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):  # corrido como ficheiro: python ferramentas\avaliar.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nucleo.config import CAMINHO_POR_OMISSAO, RAIZ  # noqa: E402
from nucleo.contrato import TipoDefeito  # noqa: E402

# Pastas do dataset: "ok", uma por tipo de defeito e "varios" (mais do que um defeito).
CLASSES = ("ok", *(tipo.value for tipo in TipoDefeito), "varios")


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Avaliar o sistema no dataset (por saco, por módulo e por defeito).")
    parser.add_argument(
        "--dataset", type=Path, default=RAIZ / "dataset" / "telemovel",
        help=f"pasta de uma montagem, com uma subpasta por classe: {', '.join(CLASSES)} "
             "(omissão: dataset/telemovel; usar com a configuração da mesma montagem)",
    )
    parser.add_argument(
        "--config", type=Path, default=CAMINHO_POR_OMISSAO,
        help="valores a sobrepor aos defines (omissão: config.json, se existir)",
    )
    parser.add_argument(
        "--saida", type=Path, default=RAIZ / "resultados" / "avaliacao",
        help="pasta para os CSV e as imagens dos erros (omissão: resultados/avaliacao/)",
    )
    parser.add_argument(
        "--tolerancia-mm", type=float, default=2.0,
        help="folga, em mm, ao comparar a posição de um defeito com a anotação (omissão: 2.0)",
    )
    parser.add_argument(
        "--guardar-erros", action="store_true",
        help="guardar a imagem anotada (deteções + anotações) de cada imagem com erros",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)

    # Fluxo previsto:
    # 1. cfg = carregar_configuracao(args.config); mestre = Mestre(cfg);
    #    fonte = FontePasta(args.dataset) (inclui as subpastas).
    # 2. Para cada captura (mostrar o progresso i/len(fonte)):
    #    a. Classe real = Path(captura.origem).parent.name; ignorar, com aviso,
    #       imagens que não estejam numa das pastas de CLASSES.
    #    b. resultado = mestre.inspecionar(captura.imagem).
    #    c. Se existir a anotação .json: ler as formas (rótulo + pontos, em
    #       píxeis da imagem original); avisar se um rótulo não for um
    #       TipoDefeito ou se o tipo não bater com a pasta.
    #    d. Guardar por imagem: ficheiro, classe real, estado final, estado e
    #       tempo de cada módulo, nº de defeitos anotados e detetados.
    #
    # 3. Avaliação por saco (o "positivo" é o saco com defeito):
    #    - matriz de confusão: real (OK/NOK) × previsto (OK/NOK/ERRO/SEM_SACO);
    #    - defeitos escapados: NOK dado como OK (o erro mais grave);
    #    - falsas rejeições: OK dado como NOK ou ERRO (desperdício);
    #    - exatidão global; SEM_SACO conta como falha do M2.
    #
    # 4. Avaliação por módulo:
    #    - por módulo: nº de OK, NOK, ERRO e POR_IMPLEMENTAR; tempo médio,
    #      percentil 95 e máximo;
    #    - falsas rejeições por módulo: nos sacos OK rejeitados, que módulo
    #      disse NOK (aponta diretamente o módulo a afinar);
    #    - defeitos escapados por módulo: para cada pasta de defeito, se o
    #      módulo responsável (TipoDefeito → módulo) o encontrou.
    #
    # 5. Avaliação por defeito (só imagens anotadas):
    #    a. Pôr deteções e anotações no mesmo referencial: o contorno de cada
    #       Defeito está no recorte normalizado e passa para a imagem original
    #       com a inversa de saco.matriz; os defeitos sem contorno (dimensao,
    #       cor) emparelham com a anotação do mesmo tipo.
    #    b. Máscaras com folga de args.tolerancia_mm (cfg.mm_para_px): as
    #       linhas anotadas desenhadas com essa espessura, os polígonos
    #       preenchidos e dilatados. Uma linha desenhada à mão nunca coincide
    #       ao píxel com a deteção; a folga evita contar como erro 1 mm de desvio.
    #    c. Emparelhar deteções e anotações um a um: afinidade = fração da
    #       deteção que cai dentro da anotação (e vice-versa); par válido se
    #       ≥ 50 %; atribuição pela maior afinidade (gananciosa, ou o algoritmo
    #       húngaro se houver muitos conflitos).
    #    d. Por tipo: verdadeiros positivos (anotação encontrada), falsos
    #       negativos (anotação sem deteção) e falsos positivos (deteção sem
    #       anotação) → precisão, sensibilidade (recall) e F1.
    #    e. Matriz de confusão dos tipos (anotado × detetado) nos pares válidos.
    #    f. Erros de separação: "fundidos" = uma deteção que cobre duas ou mais
    #       anotações; "partidos" = uma anotação coberta por duas ou mais deteções.
    #    g. Erro de posição: distância entre centros (mm) nos pares válidos.
    #
    # 6. Tempo total por saco: média, percentil 95 e máximo, face a geral.orcamento_ms.
    # 7. Imprimir as métricas em tabelas; escrever em args.saida o
    #    avaliacao_sacos.csv, o avaliacao_modulos.csv e o avaliacao_defeitos.csv
    #    (";" e "utf-8-sig"). Com --guardar-erros, guardar a imagem anotada com
    #    as anotações sobrepostas a tracejado, numa subpasta por tipo de erro
    #    (escapados/, falsas_rejeicoes/, falhados/, a_mais/, fundidos/, partidos/).
    raise NotImplementedError("avaliar.py: fluxo por implementar (ver os comentários em main()).")


if __name__ == "__main__":
    raise SystemExit(main())
