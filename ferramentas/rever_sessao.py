r"""Rever uma amostra dos sacos de uma sessão da BIapp e pôr a classe real no dataset.

    python -m ferramentas.rever_sessao resultados\20261116_103012 --config configuracoes\bancada_botao.json
    python -m ferramentas.rever_sessao resultados\20261116_103012 --config ... --pasta nok --amostra 0

Para que serve: o sistema separa os originais da sessão em ok/, nok/ e erro/ pelo que ELE achou.
Um falso OK (um defeito que passou) fica escondido em ok/, no meio de centenas de bons; só
revendo uma amostra se dá por ele. E os OK confirmados aqui são os que podem entrar na referência.

Os sacos vêm do registo.csv da sessão (a coluna imagem_original: funciona também com os
originais noutro disco). Mostra a original e a anotada lado a lado. Teclas:
    Enter / o   é OK                     →  dataset/<perfil>/ok/
    1 … 7       o defeito (a lista aparece no ecrã: dimensao, cor, risco...)
    v           vários defeitos           →  dataset/<perfil>/varios/
    espaço      saltar (não sei / imagem má): não vai para o dataset
    q / Esc     sair (o que já foi decidido fica)

Cada decisão fica numa linha do correcoes.csv (o mesmo da BIapp, ao lado das sessões), e os sacos
já revistos são saltados se voltares a correr. No fim, o resumo: quantos estavam errados.
"""

from __future__ import annotations

import argparse
import csv
import random
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):  # corrido como ficheiro: python ferramentas\rever_sessao.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from interface.registos import acrescentar_correcao, pasta_dataset  # noqa: E402
from nucleo.contrato import Estado, TipoDefeito  # noqa: E402
from nucleo.imagens import ler_imagem  # noqa: E402

JANELA = "rever_sessao"
ESTADOS = {"ok": {Estado.OK}, "nok": {Estado.NOK}, "erro": {Estado.ERRO, Estado.SEM_SACO}}
TIPOS = [tipo.value for tipo in TipoDefeito]
TECLAS = {13: "ok", 10: "ok", ord("o"): "ok", ord("v"): "varios",
          **{ord(str(i)): tipo for i, tipo in enumerate(TIPOS, start=1)}}
SALTAR, SAIR = {ord(" ")}, {ord("q"), 27}
ECRA_PX = (1600, 820)  # o espaço para as duas imagens lado a lado


@dataclass(frozen=True)
class Saco:
    """Um saco da sessão, como ficou no registo.csv."""

    sessao: str  # o nome da pasta da sessão (AAAAMMDD_HHMMSS)
    data_hora: str
    estado: str  # o veredito do sistema
    tipos: str  # os defeitos que o sistema viu, p.ex. "rugas:2"
    original: Path
    anotada: Path | None


def _caminho(sessao: Path, texto: str) -> Path | None:
    texto = texto.strip()
    if not texto:
        return None
    caminho = Path(texto.replace("\\", "/"))
    return caminho if caminho.is_absolute() else sessao / caminho


def sacos(sessao: Path, pasta: str) -> list[Saco]:
    """Os sacos da sessão com o veredito de `pasta` ("ok", "nok" ou "erro") e com o original ainda no disco
    (o limite dos originais pode já ter apagado os mais antigos)."""
    with (sessao / "registo.csv").open(encoding="utf-8-sig", newline="") as ficheiro:
        linhas = list(csv.DictReader(ficheiro, delimiter=";"))
    escolhidos = []
    for linha in linhas:
        original = _caminho(sessao, linha.get("imagem_original", ""))
        if linha["estado"] in ESTADOS[pasta] and original is not None and original.is_file():
            anotada = _caminho(sessao, linha.get("imagem_anotada", ""))
            escolhidos.append(Saco(sessao.name, linha["data_hora"], linha["estado"], linha.get("tipos", ""), original,
                                   anotada if anotada is not None and anotada.is_file() else None))
    return escolhidos


def ja_revistos(registo: Path) -> set[str]:
    """As origens que já estão no correcoes.csv (para não rever o mesmo saco duas vezes)."""
    if not registo.is_file():
        return set()
    with registo.open(encoding="utf-8-sig", newline="") as ficheiro:
        return {linha["origem"] for linha in csv.DictReader(ficheiro, delimiter=";")}


def amostra(lista: list[Saco], n: int, semente: int = 0) -> list[Saco]:
    """n sacos ao acaso (todos com n = 0 ou n ≥ len), pela ordem em que passaram."""
    if n <= 0 or n >= len(lista):
        return list(lista)
    escolhidos = set(random.Random(semente).sample(range(len(lista)), n))
    return [saco for i, saco in enumerate(lista) if i in escolhidos]


def classificar(saco: Saco, classe: str, dataset: Path, registo: Path) -> Path:
    """Copia o original (já é PNG sem perdas) para <dataset>/<classe>/ e regista a decisão.
    O nome leva a sessão à frente, para não chocar com o mesmo número de outra sessão."""
    if classe not in ("ok", "varios", *TIPOS):
        raise ValueError(f"classe desconhecida: {classe}")
    nome = saco.original.name.removesuffix("_original.png") + ".png"
    destino = dataset / classe / f"{saco.sessao}_{nome}"
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(saco.original, destino)
    acrescentar_correcao(registo, [saco.data_hora, str(saco.original), saco.estado, saco.tipos, classe, destino])
    return destino


def resumo(pasta: str, decisoes: Counter[str]) -> str:
    """Quantos foram vistos e quantos o sistema tinha errado."""
    vistos = sum(decisoes.values())
    if not vistos:
        return "Nenhum saco decidido."
    linhas = [f"{vistos} sacos decididos: " + ", ".join(f"{c} {n}" for c, n in decisoes.most_common())]
    if pasta == "ok":
        errados = vistos - decisoes["ok"]
        linhas.append(f"Falsos OK (defeitos que passaram): {errados} em {vistos} ({100 * errados / vistos:.1f} %)")
    elif pasta == "nok":
        linhas.append(f"Falsos NOK (bons rejeitados): {decisoes['ok']} em {vistos} "
                      f"({100 * decisoes['ok'] / vistos:.1f} %)")
    return "\n".join(linhas)


# --- Janela ----------------------------------------------------------------------------------

def _cor(imagem: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(imagem, cv2.COLOR_GRAY2BGR) if imagem.ndim == 2 else imagem


def vista(saco: Saco, titulo: str) -> np.ndarray:
    """A original e a anotada lado a lado (só a original se não há anotada), com as teclas por cima."""
    imagens = [_cor(ler_imagem(saco.original))]
    if saco.anotada is not None:
        imagens.append(_cor(ler_imagem(saco.anotada)))
    altura = min(img.shape[0] for img in imagens)
    imagens = [cv2.resize(img, (round(img.shape[1] * altura / img.shape[0]), altura)) for img in imagens]
    lado = np.hstack(imagens)
    escala = min(ECRA_PX[0] / lado.shape[1], ECRA_PX[1] / lado.shape[0], 1.0)
    lado = cv2.resize(lado, (round(lado.shape[1] * escala), round(lado.shape[0] * escala)))
    texto = [titulo, f"sistema: {saco.estado} {saco.tipos}".rstrip(),
             "Enter/o OK | " + " ".join(f"{i} {t}" for i, t in enumerate(TIPOS, start=1))
             + " | v varios | espaco saltar | q sair"]
    barra = np.zeros((22 * len(texto) + 6, lado.shape[1], 3), np.uint8)
    for i, linha in enumerate(texto):
        cv2.putText(barra, linha, (6, 18 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return np.vstack([barra, lado])


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rever uma amostra dos sacos de uma sessão e pôr a classe "
                                                 "real no dataset.")
    parser.add_argument("sessao", type=Path, help="a pasta da sessão (resultados\\AAAAMMDD_HHMMSS)")
    destino = parser.add_mutually_exclusive_group(required=True)
    destino.add_argument("--config", type=Path, help="o perfil da montagem: o dataset é dataset\\<perfil>")
    destino.add_argument("--dataset", type=Path, help="ou a pasta do dataset, diretamente")
    parser.add_argument("--pasta", choices=tuple(ESTADOS), default="ok",
                        help="que veredito do sistema rever (omissão: ok, para apanhar os falsos OK)")
    parser.add_argument("--amostra", type=int, default=50, help="quantos sacos, ao acaso (0 = todos; omissão: 50)")
    parser.add_argument("--semente", type=int, default=0, help="a semente da amostra (a mesma dá os mesmos sacos)")
    parser.add_argument("--registo", type=Path,
                        help="o correcoes.csv (omissão: o da BIapp, ao lado da sessão)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    if not (args.sessao / "registo.csv").is_file():
        print(f"{args.sessao}: não é uma sessão (falta o registo.csv)", file=sys.stderr)
        return 2
    dataset = args.dataset or pasta_dataset(args.config)
    registo = args.registo or args.sessao.parent / "correcoes.csv"
    revistos = ja_revistos(registo)
    todos = sacos(args.sessao, args.pasta)
    por_rever = [s for s in todos if str(s.original) not in revistos]
    lista = amostra(por_rever, args.amostra, args.semente)
    print(f"{args.pasta}/: {len(todos)} sacos com original, {len(todos) - len(por_rever)} já revistos; "
          f"a rever {len(lista)}. Dataset: {dataset}")
    if not lista:
        return 0

    decisoes: Counter[str] = Counter()
    cv2.namedWindow(JANELA, cv2.WINDOW_AUTOSIZE)
    try:
        for i, saco in enumerate(lista, start=1):
            cv2.imshow(JANELA, vista(saco, f"{i}/{len(lista)}  {saco.original.name}"))
            while True:
                tecla = cv2.waitKey(0) & 0xFF
                if tecla in SAIR or cv2.getWindowProperty(JANELA, cv2.WND_PROP_VISIBLE) < 1:
                    print(resumo(args.pasta, decisoes))
                    return 0
                if tecla in SALTAR:
                    break
                if tecla in TECLAS:
                    classe = TECLAS[tecla]
                    destino = classificar(saco, classe, dataset, registo)
                    decisoes[classe] += 1
                    marca = "" if (classe == "ok") == (saco.estado == Estado.OK) else "   <- o sistema errou"
                    print(f"{saco.original.name}: {classe} -> {destino.parent.name}/{marca}")
                    break
    finally:
        cv2.destroyAllWindows()
    print(resumo(args.pasta, decisoes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
