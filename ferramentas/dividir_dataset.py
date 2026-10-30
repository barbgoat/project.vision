r"""Dividir as anotações em treino, validação e teste sem pôr o mesmo saco físico em dois conjuntos.

    python -m ferramentas.dividir_dataset
    python -m ferramentas.dividir_dataset --fora-fundo inox          (teste = só as fotos no inox)
    python -m ferramentas.dividir_dataset --fora-tipo Ben --copiar   (e as pastas para treinar)

Lê --pasta (omissão: dataset\segmentacao, a saída de ferramentas/anotar.py): mascaras/,
imagens/, metadados.csv (saco, tipo_saco, fundo) e anotacoes_coco.json. As fotos de um saco
físico ficam todas no mesmo conjunto (senão o teste "já viu" aquele saco); sem metadados,
cada foto conta como um saco.

    --fora-fundo X / --fora-tipo Y   validação "deixar de fora": o teste é só esse fundo (ou
                                     tipo de saco); as outras fotos dos sacos que lá estão
                                     ficam de fora de tudo, para não haver fugas.

Escreve em --pasta:
    divisao.csv                        nome; conjunto; saco; tipo_saco; fundo
    treino.txt, validacao.txt, teste.txt   os caminhos das imagens
    coco_treino.json, ...              o COCO de cada conjunto
e, com --copiar, as pastas prontas a treinar:
    rfdetr/{train,valid,test}/         imagens + _annotations.coco.json (RF-DETR-Seg)
    yolo/{images,labels}/{train,val,test}/ + yolo/data.yaml   (YOLO-seg)
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # corrido como ficheiro
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ferramentas.anotar import ler_coco, ler_metadados  # noqa: E402
from nucleo.config import RAIZ  # noqa: E402

CONJUNTOS = ("treino", "validacao", "teste")
NOMES_RFDETR = {"treino": "train", "validacao": "valid", "teste": "test"}
NOMES_YOLO = {"treino": "train", "validacao": "val", "teste": "test"}


def dividir(fotos: dict[str, dict[str, str]], fracoes: tuple[float, float, float] = (0.7, 0.15, 0.15),
            semente: int = 0, fora_fundo: str = "", fora_tipo: str = "") -> dict[str, str]:
    """nome → conjunto ("treino", "validacao", "teste" ou "fora").

    fotos: nome → {saco, tipo_saco, fundo}. Os sacos físicos são sorteados (com `semente`) e
    cada um vai inteiro para o conjunto que está mais abaixo da sua fração.
    """
    saco_de = {nome: m.get("saco") or nome for nome, m in fotos.items()}
    divisao: dict[str, str] = {}
    if fora_fundo or fora_tipo:
        teste = {n for n, m in fotos.items()
                 if (fora_fundo and m.get("fundo") == fora_fundo) or (fora_tipo and m.get("tipo_saco") == fora_tipo)}
        sacos_teste = {saco_de[n] for n in teste}
        for n in fotos:
            if n in teste:
                divisao[n] = "teste"
            elif saco_de[n] in sacos_teste:
                divisao[n] = "fora"  # o mesmo saco, noutro fundo: nem treino nem teste
        fracoes = (fracoes[0], fracoes[1], 0.0)
    grupos: dict[str, list[str]] = defaultdict(list)
    for n in fotos:
        if n not in divisao:
            grupos[saco_de[n]].append(n)
    sacos = sorted(grupos)
    random.Random(semente).shuffle(sacos)
    total = sum(len(g) for g in grupos.values())
    contagem = dict.fromkeys(CONJUNTOS, 0)
    alvo = {c: f / sum(fracoes) * total for c, f in zip(CONJUNTOS, fracoes)}
    for saco in sacos:
        conjunto = max((c for c in CONJUNTOS if alvo[c] > 0), key=lambda c: alvo[c] - contagem[c])  # o mais em falta
        for n in grupos[saco]:
            divisao[n] = conjunto
        contagem[conjunto] += len(grupos[saco])
    return divisao


def _subconjunto_coco(coco: dict[str, Any], nomes: set[str]) -> dict[str, Any]:
    imagens = [i for i in coco["images"] if i["nome"] in nomes]
    ids = {i["id"] for i in imagens}
    return {**coco, "images": imagens, "annotations": [a for a in coco["annotations"] if a["image_id"] in ids]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dividir as anotações sem repetir sacos físicos entre conjuntos.")
    parser.add_argument("--pasta", type=Path, default=RAIZ / "dataset" / "segmentacao")
    parser.add_argument("--fracoes", type=float, nargs=3, default=(0.7, 0.15, 0.15), metavar=("TREINO", "VAL", "TESTE"))
    parser.add_argument("--semente", type=int, default=0)
    parser.add_argument("--fora-fundo", default="", help="o teste é só este fundo")
    parser.add_argument("--fora-tipo", default="", help="o teste é só este tipo de saco")
    parser.add_argument("--copiar", action="store_true", help="criar também rfdetr/ e yolo/ prontos a treinar")
    args = parser.parse_args(argv)

    pasta = args.pasta
    metadados = ler_metadados(pasta)
    nomes = sorted(f.stem for f in (pasta / "mascaras").glob("*.png"))
    if not nomes:
        print(f"Sem anotações em {pasta}/mascaras.")
        return 1
    fotos = {n: metadados.get(n, {}) for n in nomes}
    divisao = dividir(fotos, tuple(args.fracoes), args.semente, args.fora_fundo, args.fora_tipo)
    imagem_de = {n: next((pasta / "imagens").glob(f"{n}.*")) for n in nomes}
    coco = ler_coco(pasta)

    with (pasta / "divisao.csv").open("w", encoding="utf-8", newline="") as ficheiro:
        escritor = csv.writer(ficheiro, delimiter=";")
        escritor.writerow(["nome", "conjunto", "saco", "tipo_saco", "fundo"])
        for n in nomes:
            escritor.writerow([n, divisao[n], *(fotos[n].get(c, "") for c in ("saco", "tipo_saco", "fundo"))])
    for conjunto in CONJUNTOS:
        membros = {n for n in nomes if divisao[n] == conjunto}
        (pasta / f"{conjunto}.txt").write_text("".join(f"{imagem_de[n].resolve()}\n" for n in sorted(membros)),
                                               encoding="utf-8")
        (pasta / f"coco_{conjunto}.json").write_text(
            json.dumps(_subconjunto_coco(coco, membros), ensure_ascii=False), encoding="utf-8")
        if args.copiar:
            _copiar(pasta, conjunto, membros, imagem_de, coco)
    if args.copiar:
        (pasta / "yolo" / "data.yaml").write_text(
            f"path: {(pasta / 'yolo').resolve().as_posix()}\ntrain: images/train\nval: images/val\n"
            "test: images/test\nnames:\n  0: saco\n", encoding="utf-8")

    for conjunto in (*CONJUNTOS, "fora"):
        membros = [n for n in nomes if divisao[n] == conjunto]
        if membros:
            sacos = {fotos[n].get("saco") or n for n in membros}
            print(f"{conjunto:10s} {len(membros):4d} fotos, {len(sacos):3d} sacos físicos")
    print(f"Divisão em {pasta / 'divisao.csv'}")
    return 0


def _copiar(pasta: Path, conjunto: str, membros: set[str], imagem_de: dict[str, Path], coco: dict[str, Any]) -> None:
    """As pastas no formato do RF-DETR (COCO) e do YOLO-seg."""
    rfdetr = pasta / "rfdetr" / NOMES_RFDETR[conjunto]
    imagens_yolo = pasta / "yolo" / "images" / NOMES_YOLO[conjunto]
    etiquetas_yolo = pasta / "yolo" / "labels" / NOMES_YOLO[conjunto]
    for destino in (rfdetr, imagens_yolo, etiquetas_yolo):
        shutil.rmtree(destino, ignore_errors=True)
        destino.mkdir(parents=True)
    for n in membros:
        shutil.copy2(imagem_de[n], rfdetr / imagem_de[n].name)
        shutil.copy2(imagem_de[n], imagens_yolo / imagem_de[n].name)
        shutil.copy2(pasta / "etiquetas" / f"{n}.txt", etiquetas_yolo / f"{imagem_de[n].stem}.txt")
    sub = _subconjunto_coco(coco, membros)
    sub["images"] = [{**i, "file_name": Path(i["file_name"]).name} for i in sub["images"]]
    (rfdetr / "_annotations.coco.json").write_text(json.dumps(sub, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
