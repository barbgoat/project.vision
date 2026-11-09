r"""Validar a medição do M2 com medidas reais (paquímetro): exatidão, repetibilidade e escala.

    python -m ferramentas.validar --config configuracoes\linha.json
    python -m ferramentas.validar --config configuracoes\linha.json --backends delta_e aprendido

Ensaio na bancada (um operador): 10 sacos × 5 posições fixas, mais um objeto rígido de medida
conhecida (o padrão) nas mesmas 5 posições, tudo numa sessão da BIapp com as originais guardadas:

    1. preparar: copia as originais da sessão, diz a cada foto o saco e a posição (pela ordem em
       que foram tiradas: o padrão primeiro, depois o saco 1, o 2, ..., cada um em POSICOES) e faz
       conferir.jpg, uma linha de miniaturas por saco, para confirmar antes de medir:
           python -m ferramentas.validar --preparar resultados\20261110_090000 --pasta dataset\teste_garnier
    2. o paquímetro: preencher paquimetro.csv (saco; comprimento_mm; largura_mm), medido a meio de
       cada lado, como o M2. Uma linha por leitura: várias leituras do mesmo saco dão a média.
    3. medir, com o produto da base de dados e as resoluções a comparar:
           python -m ferramentas.validar --pasta dataset\teste_garnier --config configuracoes\bancada_botao.json
               --produto garnier --resolucao 5 20

O conjunto (--pasta, omissão: dataset\teste):
    imagens/            as fotos, tiradas com a montagem do perfil (px_por_mm medido)
    mascaras/           (opcional) PNG com o saco anotado, mesmo nome da foto: dá o IoU
    ground_truth.csv    ficheiro; saco; tipo_saco; fundo; comprimento_mm; largura_mm [; posicao]
                        (saco = o saco físico: as fotos do mesmo saco dão a repetibilidade)
    paquimetro.csv      (opcional) saco; comprimento_mm; largura_mm: se existe, as medidas reais
                        vêm daqui (a média das leituras de cada saco), não do ground_truth.csv

Para cada backend e resolução corre o M2 em cada foto e reporta, no terminal e em CSV (--saida,
omissão resultados\validacao.csv, mais _resumo.csv, _repetibilidade.csv e _escala.csv ao lado):
    viés (erro médio com sinal), erro médio absoluto, máximo e desvio-padrão (mm), face ao paquímetro;
    separados por tipo de saco e por posição (centro vs cantos, e cada posição);
    repetibilidade de cada saco (desvio-padrão das suas fotos, reposto entre fotos) e a
    repetibilidade (um operador) do conjunto: 6σ em % da faixa de tolerância do produto;
    com o padrão: o fator medido/real e o px_por_mm que o corrigiria;
    IoU da máscara (quando há máscara anotada); taxa de ERRO ou sem saco; tempo médio por foto.
"""

from __future__ import annotations

import argparse
import csv
import math
import shutil
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from time import perf_counter
from typing import get_args

import cv2
import numpy as np

if __package__ in (None, ""):  # corrido como ficheiro: python ferramentas\validar.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mestre import Mestre  # noqa: E402
from modulos.m2_localizacao.segmentadores import iou, sam3  # noqa: E402
from nucleo.config import CAMINHO_POR_OMISSAO, RAIZ, Config, TipoSegmentador, carregar_configuracao  # noqa: E402
from nucleo.contrato import Estado  # noqa: E402
from nucleo.imagens import guardar_imagem, ler_imagem, ler_original  # noqa: E402

COLUNAS_VERDADE = ("ficheiro", "saco", "tipo_saco", "fundo", "comprimento_mm", "largura_mm")
POSICOES = ("centro", "sup-esq", "sup-dir", "inf-esq", "inf-dir")  # a ordem das fotos de cada saco
PADRAO = "padrao"  # o saco e o tipo_saco do objeto rígido de medida conhecida
MEDIDAS = ("comprimento", "largura")


@dataclass
class Medicao:
    """Uma foto analisada por um backend, a uma resolução da localização."""

    backend: str
    ficheiro: str
    saco: str
    tipo_saco: str
    fundo: str
    estado: str  # OK (mediu em mm), ERRO, SEM_SACO
    comprimento_mm: float | None
    largura_mm: float | None
    erro_comprimento_mm: float | None  # medido − real
    erro_largura_mm: float | None
    iou: float | None
    tempo_ms: float
    mensagem: str = ""
    resolucao_px_por_mm: float = 0.0  # a do mapa da segmentação (no máximo o px_por_mm da imagem)
    posicao: str = ""  # uma de POSICOES, ou "" se o conjunto não as tem
    real_comprimento_mm: float = 0.0
    real_largura_mm: float = 0.0

    @property
    def zona(self) -> str:
        """centro ou cantos (as outras posições), para comparar os dois; "" sem posição."""
        return "" if not self.posicao else "centro" if self.posicao == "centro" else "cantos"


# --- O conjunto: ground_truth.csv e paquimetro.csv ---------------------------------------------

def _numero(texto: str) -> float:
    """Um número escrito com ponto ou vírgula decimal."""
    return float(texto.strip().replace(",", "."))


def ler_paquimetro(pasta: Path) -> dict[str, list[tuple[float, float]]]:
    """As leituras do paquímetro (comprimento, largura) de cada saco, de paquimetro.csv; {} se não há.
    Linhas sem as duas medidas são ignoradas (o modelo vem com elas vazias)."""
    caminho = pasta / "paquimetro.csv"
    if not caminho.exists():
        return {}
    with caminho.open(encoding="utf-8-sig", newline="") as ficheiro:
        linhas = list(csv.DictReader(ficheiro, delimiter=";"))
    leituras: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for numero, linha in enumerate(linhas, start=2):
        c, l = ((linha.get(f"{medida}_mm") or "").strip() for medida in MEDIDAS)
        if not c and not l:
            continue
        try:
            leituras[(linha.get("saco") or "").strip()].append((_numero(c), _numero(l)))
        except ValueError:
            raise ValueError(f"{caminho}, linha {numero}: medidas inválidas ({c!r}, {l!r})") from None
    return dict(leituras)


def ler_verdade(pasta: Path) -> list[dict[str, str]]:
    """As linhas de ground_truth.csv (separador ;). Se há paquimetro.csv, as medidas reais de cada
    saco são a média das suas leituras (ValueError se faltar algum saco)."""
    caminho = pasta / "ground_truth.csv"
    with caminho.open(encoding="utf-8-sig", newline="") as ficheiro:
        linhas = list(csv.DictReader(ficheiro, delimiter=";"))
    faltam = [c for c in COLUNAS_VERDADE if linhas and c not in linhas[0]]
    if faltam:
        raise ValueError(f"{caminho}: faltam as colunas {', '.join(faltam)}")
    paquimetro = ler_paquimetro(pasta)
    if paquimetro:
        sem = sorted({l["saco"] for l in linhas} - set(paquimetro), key=_ordem_saco)
        if sem:
            raise ValueError(f"{pasta / 'paquimetro.csv'}: faltam as medidas dos sacos {', '.join(sem)}")
        for linha in linhas:
            leituras = paquimetro[linha["saco"]]
            linha["comprimento_mm"] = str(statistics.fmean(c for c, _ in leituras))
            linha["largura_mm"] = str(statistics.fmean(l for _, l in leituras))
    vazias = [l["ficheiro"] for l in linhas if not l["comprimento_mm"].strip() or not l["largura_mm"].strip()]
    if vazias:
        raise ValueError(f"{caminho}: sem medidas reais em {len(vazias)} fotos (p.ex. {vazias[0]}); "
                         "preencher o paquimetro.csv")
    return linhas


def _ordem_saco(saco: str) -> tuple[int, int | str]:
    """O padrão primeiro, depois os sacos por número (1, 2, ..., 10), depois os outros nomes."""
    if saco == PADRAO:
        return (0, 0)
    return (1, int(saco)) if saco.isdigit() else (2, saco)


# --- Preparar o conjunto a partir de uma sessão da BIapp ---------------------------------------

def preparar(sessao: Path, destino: Path, sacos: int = 10, posicoes: tuple[str, ...] = POSICOES,
             padrao: bool = True, excluir: tuple[int, ...] = (), fundo: str = "bancada") -> Path:
    """Faz o conjunto em `destino` com as originais da sessão: a cada foto o saco e a posição, pela
    ordem em que foram tiradas (o padrão primeiro, se `padrao`; cada saco em todas as `posicoes`).

    ValueError se a sessão não tiver exatamente (sacos [+ padrão]) × posições fotos com original,
    depois de tirar as passagens `excluir` (as fotos a mais, p.ex. um disparo repetido). Escreve
    imagens/, ground_truth.csv, um paquimetro.csv por preencher (se ainda não existe) e
    conferir.jpg, que devolve: uma linha de miniaturas por saco, para confirmar antes de medir."""
    with (sessao / "registo.csv").open(encoding="utf-8-sig", newline="") as ficheiro:
        registo = [r for r in csv.DictReader(ficheiro, delimiter=";")
                   if (r.get("imagem_original") or "").strip() and int(r["passagem"]) not in excluir]
    grupos = ([PADRAO] if padrao else []) + [str(i) for i in range(1, sacos + 1)]
    esperadas = len(grupos) * len(posicoes)
    if len(registo) != esperadas:
        raise ValueError(
            f"{sessao}: {len(registo)} fotos com original, esperava {esperadas} "
            f"({sacos} sacos{' + padrão' if padrao else ''} × {len(posicoes)} posições). "
            f"Passagens: {', '.join(r['passagem'] for r in registo)}. "
            "Tirar as fotos a mais com --excluir <passagem> ...")
    tipo = (registo[0].get("produto") or "").strip() or "saco"
    linhas, fotos = [], []
    for i, r in enumerate(registo):
        saco, posicao = grupos[i // len(posicoes)], posicoes[i % len(posicoes)]
        original = sessao / r["imagem_original"].strip().replace("\\", "/")
        nome = f"{saco if saco == PADRAO else f'saco{int(saco):02d}'}_{posicao}.png"
        (destino / "imagens").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destino / "imagens" / nome)
        linhas.append({"ficheiro": nome, "saco": saco, "tipo_saco": PADRAO if saco == PADRAO else tipo,
                       "fundo": fundo, "posicao": posicao, "comprimento_mm": "", "largura_mm": "",
                       "passagem": r["passagem"], "original": str(original)})
        fotos.append((saco, posicao, destino / "imagens" / nome))
    _escrever(destino / "ground_truth.csv", linhas)
    if not (destino / "paquimetro.csv").exists():
        (destino / "paquimetro.csv").write_text(
            "saco;comprimento_mm;largura_mm\n" + "".join(f"{g};;\n" for g in grupos), encoding="utf-8")
    conferir = destino / "conferir.jpg"
    guardar_imagem(conferir, _folha_de_contacto(fotos, grupos, posicoes), [cv2.IMWRITE_JPEG_QUALITY, 85])
    return conferir


def _folha_de_contacto(fotos: list[tuple[str, str, Path]], grupos: list[str], posicoes: tuple[str, ...],
                       largura: int = 220) -> np.ndarray:
    """Uma grelha de miniaturas: uma linha por saco, uma coluna por posição, cada uma com legenda."""
    miniaturas = {}
    for saco, posicao, caminho in fotos:
        img = ler_imagem(caminho)
        altura = round(img.shape[0] * largura / img.shape[1])
        mini = cv2.resize(img, (largura, altura), interpolation=cv2.INTER_AREA)
        cv2.rectangle(mini, (0, 0), (largura, 20), (0, 0, 0), -1)
        rotulo = f"{'padrao' if saco == PADRAO else f'saco {saco}'} - {posicao}"
        cv2.putText(mini, rotulo, (4, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
        miniaturas[saco, posicao] = cv2.copyMakeBorder(mini, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=(128, 128, 128))
    forma = next(iter(miniaturas.values())).shape
    vazia = np.zeros(forma, np.uint8)
    return np.vstack([np.hstack([miniaturas.get((g, p), vazia) for p in posicoes]) for g in grupos])


# --- Medir --------------------------------------------------------------------------------------

def medir(pasta: Path, cfg: Config, backend: str, produto: str = "") -> list[Medicao]:
    """Corre o M2 com o `backend` em cada foto do conjunto."""
    cfg = replace(cfg, localizacao=replace(cfg.localizacao, segmentador=backend))
    resolucao = min(cfg.localizacao.resolucao_px_por_mm, cfg.geral.px_por_mm)
    mestre = Mestre(cfg, apenas=[], produto=produto)
    medicoes = []
    for verdade in ler_verdade(pasta):
        img = ler_original(pasta / "imagens" / verdade["ficheiro"])
        if img is None:
            raise FileNotFoundError(pasta / "imagens" / verdade["ficheiro"])
        inicio = perf_counter()
        resultado = mestre.inspecionar(img)
        tempo = (perf_counter() - inicio) * 1000
        loc = resultado.relatorio("localizacao")
        mediu = loc is not None and loc.estado is Estado.OK and "comprimento_mm" in loc.medidas
        comprimento = loc.medidas["comprimento_mm"] if mediu else None
        largura = loc.medidas["largura_mm"] if mediu else None
        real_c, real_l = _numero(verdade["comprimento_mm"]), _numero(verdade["largura_mm"])
        medicoes.append(Medicao(
            backend, verdade["ficheiro"], verdade["saco"], verdade["tipo_saco"], verdade["fundo"],
            "OK" if mediu else str(loc.estado if loc else resultado.estado),
            comprimento, largura,
            None if comprimento is None else comprimento - real_c,
            None if largura is None else largura - real_l,
            _iou(pasta, verdade["ficheiro"], resultado) if mediu else None, tempo,
            "" if mediu else resultado.mensagem,
            resolucao_px_por_mm=resolucao, posicao=(verdade.get("posicao") or "").strip(),
            real_comprimento_mm=real_c, real_largura_mm=real_l))
    return medicoes


def _iou(pasta: Path, ficheiro: str, resultado) -> float | None:
    """IoU da região do saco com a máscara anotada, se houver uma do tamanho da imagem analisada."""
    caminho = pasta / "mascaras" / f"{Path(ficheiro).stem}.png"
    anotada = ler_imagem(caminho) if caminho.exists() else None
    if anotada is None or resultado.saco is None or anotada.shape[:2] != resultado.imagem.shape[:2]:
        return None  # sem máscara, ou de outro tamanho
    regiao = np.zeros(anotada.shape[:2], np.uint8)
    cv2.fillPoly(regiao, [resultado.saco.contorno], 1)
    return iou(regiao > 0, anotada[..., 0] > 127)


# --- Resumos ------------------------------------------------------------------------------------

def _grupos(medicoes: list[Medicao], chaves: tuple[str, ...]) -> list[tuple[dict[str, object], list[Medicao]]]:
    """As medições agrupadas por backend, resolução e as `chaves` (atributos da Medicao), ordenadas."""
    grupos: dict[tuple, list[Medicao]] = defaultdict(list)
    for m in medicoes:
        grupos[(m.backend, m.resolucao_px_por_mm, *(getattr(m, c) for c in chaves))].append(m)
    return [({"backend": g[0], "resolucao_px_por_mm": g[1], **dict(zip(chaves, g[2:]))}, ms)
            for g, ms in sorted(grupos.items(), key=lambda item: tuple(map(str, item[0])))]


def resumir(medicoes: list[Medicao], *chaves: str) -> list[dict[str, object]]:
    """Estatísticas por grupo (backend, resolução e as `chaves`, p.ex. "tipo_saco", "zona")."""
    linhas = []
    for cabeca, ms in _grupos(medicoes, chaves):
        linha: dict[str, object] = {**cabeca, "fotos": len(ms)}
        for medida in MEDIDAS:
            erros = [getattr(m, f"erro_{medida}_mm") for m in ms if getattr(m, f"erro_{medida}_mm") is not None]
            linha[f"{medida}_vies_mm"] = statistics.fmean(erros) if erros else None
            linha[f"{medida}_erro_medio_mm"] = statistics.fmean(map(abs, erros)) if erros else None
            linha[f"{medida}_erro_max_mm"] = max(map(abs, erros)) if erros else None
            linha[f"{medida}_desvio_padrao_mm"] = statistics.pstdev(erros) if len(erros) > 1 else None
        ious = [m.iou for m in ms if m.iou is not None]
        linha["iou_medio"] = statistics.fmean(ious) if ious else None
        linha["taxa_erro_ou_sem_saco"] = sum(m.estado != "OK" for m in ms) / len(ms)
        linha["tempo_medio_ms"] = statistics.fmean(m.tempo_ms for m in ms)
        linhas.append(linha)
    return linhas


def repetibilidade(medicoes: list[Medicao]) -> list[dict[str, object]]:
    """Por backend, resolução e saco físico com ≥ 2 fotos medidas: o desvio-padrão (amostral) e a
    amplitude das medidas (mm). Com as fotos em posições diferentes, inclui o efeito da posição."""
    linhas = []
    for cabeca, ms in _grupos([m for m in medicoes if m.comprimento_mm is not None], ("tipo_saco", "saco")):
        if len(ms) < 2:
            continue
        linha: dict[str, object] = {**cabeca, "fotos": len(ms)}
        for medida in MEDIDAS:
            valores = [getattr(m, f"{medida}_mm") for m in ms]
            linha[f"{medida}_dp_mm"] = statistics.stdev(valores)
            linha[f"{medida}_amplitude_mm"] = max(valores) - min(valores)
        linhas.append(linha)
    return sorted(linhas, key=lambda l: (l["backend"], l["resolucao_px_por_mm"], _ordem_saco(str(l["saco"]))))


def repetibilidade_conjunto(medicoes: list[Medicao], tolerancias: dict[str, float] | None = None
                            ) -> list[dict[str, object]]:
    """A repetibilidade (um operador) de cada tipo de saco, sem o padrão: σ = raiz da média das
    variâncias de cada saco (a mesma pessoa repõe o mesmo saco), e 6σ em % da faixa de tolerância
    (2 × tolerância) do produto, quando dada. Até 10 % é boa; até 30 % é aceitável."""
    por_saco = [l for l in repetibilidade(medicoes) if l["tipo_saco"] != PADRAO]
    grupos: dict[tuple, list[dict[str, object]]] = defaultdict(list)
    for l in por_saco:
        grupos[(l["backend"], l["resolucao_px_por_mm"], l["tipo_saco"])].append(l)
    linhas = []
    for (backend, resolucao, tipo), ls in sorted(grupos.items(), key=lambda item: tuple(map(str, item[0]))):
        linha: dict[str, object] = {"backend": backend, "resolucao_px_por_mm": resolucao, "tipo_saco": tipo,
                                    "sacos": len(ls), "fotos": sum(int(l["fotos"]) for l in ls)}
        for medida in MEDIDAS:
            sigma = math.sqrt(statistics.fmean(float(l[f"{medida}_dp_mm"]) ** 2 for l in ls))
            linha[f"{medida}_sigma_mm"] = sigma
            tolerancia = (tolerancias or {}).get(medida)
            linha[f"{medida}_6sigma_pct_tolerancia"] = 6 * sigma / (2 * tolerancia) * 100 if tolerancia else None
        linhas.append(linha)
    return linhas


def escala(medicoes: list[Medicao], px_por_mm: float) -> list[dict[str, object]]:
    """Com o padrão (objeto rígido de medida conhecida): por backend, resolução e zona, o fator
    medido/real de cada medida e o px_por_mm que o corrigiria (atual × fator médio)."""
    linhas = []
    padrao = [m for m in medicoes if m.tipo_saco == PADRAO and m.comprimento_mm is not None]
    for cabeca, ms in _grupos(padrao, ("zona",)) + _grupos(padrao, ()):
        cabeca.setdefault("zona", "todas")
        fatores = {medida: statistics.fmean(getattr(m, f"{medida}_mm") / getattr(m, f"real_{medida}_mm")
                                            for m in ms) for medida in MEDIDAS}
        linhas.append({**cabeca, "fotos": len(ms),
                       **{f"{medida}_fator": f for medida, f in fatores.items()},
                       "px_por_mm_atual": px_por_mm,
                       "px_por_mm_sugerido": px_por_mm * statistics.fmean(fatores.values())})
    ordem = {"centro": 0, "cantos": 1, "todas": 2}
    return sorted(linhas, key=lambda l: (l["backend"], l["resolucao_px_por_mm"], ordem.get(str(l["zona"]), 1)))


# --- Saída --------------------------------------------------------------------------------------

def _escrever(caminho: Path, linhas: list[dict[str, object]]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8", newline="") as ficheiro:
        if not linhas:
            return
        colunas = list(dict.fromkeys(c for linha in linhas for c in linha))  # o resumo junta grupos diferentes
        escritor = csv.DictWriter(ficheiro, fieldnames=colunas, delimiter=";", restval="")
        escritor.writeheader()
        escritor.writerows({k: _texto(v) for k, v in linha.items()} for linha in linhas)


def _texto(valor: object) -> object:
    return f"{valor:.3f}" if isinstance(valor, float) else "" if valor is None else valor


def _tabela(titulo: str, linhas: list[dict[str, object]]) -> str:
    """Uma tabela de texto para o terminal."""
    if not linhas:
        return f"\n{titulo}: (nada)"
    colunas = list(linhas[0])
    celulas = [[str(_texto(l[c])) for c in colunas] for l in linhas]
    larguras = [max(len(c), *(len(r[i]) for r in celulas)) for i, c in enumerate(colunas)]
    linha = lambda valores: "  ".join(v.rjust(w) for v, w in zip(valores, larguras))  # noqa: E731
    return "\n".join([f"\n{titulo}", linha(colunas), *(linha(r) for r in celulas)])


def _sem_colunas(linhas: list[dict[str, object]], *fora: str) -> list[dict[str, object]]:
    """As linhas sem as colunas `fora` (para as tabelas do terminal caberem)."""
    return [{k: v for k, v in l.items() if k not in fora} for l in linhas]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validar a medição do M2 com medidas reais (paquímetro).")
    parser.add_argument("--pasta", type=Path, default=RAIZ / "dataset" / "teste", help="o conjunto de teste")
    parser.add_argument("--config", type=Path, default=CAMINHO_POR_OMISSAO, help="perfil da montagem (JSON)")
    parser.add_argument("--backends", nargs="+", default=["delta_e"], choices=list(get_args(TipoSegmentador)))
    parser.add_argument("--saida", type=Path, default=RAIZ / "resultados" / "validacao.csv", help="CSV por foto")
    parser.add_argument("--produto", default="", help="o produto na base de dados (nominal, tolerâncias, resolução)")
    parser.add_argument("--resultados", type=Path, default=RAIZ / "resultados",
                        help="a pasta dos resultados, onde está a base de dados (omissão: resultados/)")
    parser.add_argument("--resolucao", nargs="+", type=float, default=[],
                        help="resoluções da localização a comparar (px/mm); omissão: a do produto ou do perfil")
    preparacao = parser.add_argument_group("preparar o conjunto a partir de uma sessão da BIapp")
    preparacao.add_argument("--preparar", type=Path, metavar="SESSAO",
                            help="a pasta da sessão (resultados\\AAAAMMDD_HHMMSS): faz o conjunto em --pasta")
    preparacao.add_argument("--sacos", type=int, default=10, help="quantos sacos (omissão: 10)")
    preparacao.add_argument("--sem-padrao", action="store_true", help="a sessão não tem o objeto padrão")
    preparacao.add_argument("--excluir", nargs="+", type=int, default=[], metavar="PASSAGEM",
                            help="passagens a deixar de fora (fotos a mais)")
    args = parser.parse_args(argv)

    if args.preparar:
        try:
            conferir = preparar(args.preparar, args.pasta, args.sacos, padrao=not args.sem_padrao,
                                excluir=tuple(args.excluir))
        except ValueError as erro:
            print(f"Erro: {erro}")
            return 1
        print(f"Conjunto em {args.pasta}.\nConfirmar os sacos e as posições em {conferir};"
              f" depois preencher {args.pasta / 'paquimetro.csv'} e medir.")
        return 0

    if args.produto:
        from ferramentas.referencia import configuracao_do_produto  # aqui: referencia importa este módulo
        cfg, produto = configuracao_do_produto(args.config, args.produto, args.resultados)
    else:
        cfg, produto = carregar_configuracao(args.config), ""
    tolerancias = ({"comprimento": cfg.dimensoes.tolerancia_comprimento_mm,
                    "largura": cfg.dimensoes.tolerancia_largura_mm} if produto else None)
    configuracoes = [replace(cfg, localizacao=replace(cfg.localizacao, resolucao_px_por_mm=r))
                     for r in args.resolucao] or [cfg]

    medicoes: list[Medicao] = []
    for backend in args.backends:
        if backend in ("sam3", "duplo") and not sam3.disponivel():
            print(f"{backend}: o SAM 3 não está instalado (ver o README); salto este backend.")
            continue
        for c in configuracoes:
            medicoes += medir(args.pasta, c, backend, produto)
    if not medicoes:
        print("Nada medido.")
        return 1

    com_posicao = any(m.posicao for m in medicoes)
    varios_fundos = len({m.fundo for m in medicoes}) > 1
    por_tipo = resumir(medicoes, "tipo_saco", "zona") if com_posicao else resumir(medicoes, "tipo_saco")
    por_posicao = resumir(medicoes, "tipo_saco", "posicao") if com_posicao else []
    por_fundo = resumir(medicoes, "fundo") if varios_fundos else []
    repet = repetibilidade(medicoes)
    conjunto = repetibilidade_conjunto(medicoes, tolerancias)
    escalas = escala(medicoes, cfg.geral.px_por_mm)

    _escrever(args.saida, [asdict(m) | {"zona": m.zona} for m in medicoes])
    _escrever(args.saida.with_name(f"{args.saida.stem}_resumo.csv"),
              resumir(medicoes) + por_tipo + por_posicao + por_fundo)
    _escrever(args.saida.with_name(f"{args.saida.stem}_repetibilidade.csv"), repet + conjunto)
    if escalas:
        _escrever(args.saida.with_name(f"{args.saida.stem}_escala.csv"), escalas)

    detalhe = ("comprimento_erro_medio_mm", "largura_erro_medio_mm", "iou_medio")
    print(_tabela("Por backend e resolução", _sem_colunas(resumir(medicoes), *detalhe)))
    print(_tabela("Por tipo de saco" + (" e zona (centro vs cantos)" if com_posicao else ""),
                  _sem_colunas(por_tipo, *detalhe, "tempo_medio_ms")))
    if por_posicao:
        print(_tabela("Por tipo de saco e posição", _sem_colunas(por_posicao, *detalhe, "tempo_medio_ms")))
    if por_fundo:
        print(_tabela("Por fundo", _sem_colunas(por_fundo, *detalhe)))
    print(_tabela("Repetibilidade de cada saco (reposto entre fotos)", repet))
    print(_tabela("Repetibilidade (um operador)" + ("" if tolerancias else " (sem --produto: sem tolerância)"),
                  conjunto))
    if escalas:
        print(_tabela("Escala (objeto padrão)", escalas))
    print(f"\nCSV em {args.saida} (e _resumo, _repetibilidade{', _escala' if escalas else ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
