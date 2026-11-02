"""Segmentador aprendido: o saco pelas cores e texturas aprendidas com as fotos anotadas. CPU.

O modelo (Modelo) conta quantas vezes cada (cor, textura) apareceu no saco e no fundo das
fotos anotadas com ferramentas/anotar.py, que o atualiza a cada foto guardada e o grava em
dataset/segmentacao/modelo.npz. Não precisa de um fundo liso nem conhecido: aprende "o meu
saco tem estas cores e estes fundos têm aquelas". Uma cor nunca vista fica a meio (P ≈ 0,5),
abaixo do limiar: só é saco o que se parece com os sacos anotados. Falha quando o fundo tem
as cores do saco (aí só uma rede, que aprende formas, resolve).

    1. A foto reduzida à resolução em que o modelo aprendeu (M2_APRENDIDO_LADO_PX).
    2. P(saco) de cada píxel, suavizada; acima de M2_APRENDIDO_PROBABILIDADE_MIN é saco.
    3. Fecho, buracos tapados, abertura e maior região (como no ΔE): a região provável.
    4. GrabCut (a M2_APRENDIDO_GRABCUT_LADO_PX): a partir da região provável aprende as cores
       desta foto e decide o que ficou ambíguo para o modelo (um saco branco ou prateado numa
       fundo branco: o modelo só vê a orla, o GrabCut completa o saco).
    5. Confiança = P(saco) média dentro do saco menos a da faixa à volta (quanto o modelo
       separa o saco do que o rodeia); abaixo de aprendido_score_min → ERRO.

O mapa_aresta é a imagem em CIELAB: o contorno subpíxel procura o gradiente da cor.
"""

from __future__ import annotations

from pathlib import Path
from time import perf_counter

import cv2
import numpy as np

from modulos.m2_localizacao import fundo, segmentacao
from modulos.m2_localizacao.segmentadores import ResultadoSegmentacao, SegmentacaoIncerta, Segmentador
from nucleo import defines as D
from nucleo.config import RAIZ, Config
from nucleo.contrato import SemSaco

NIVEIS_COR = D.M2_APRENDIDO_NIVEIS_COR
NIVEIS_TEXTURA = D.M2_APRENDIDO_NIVEIS_TEXTURA


class Modelo:
    """Quantas vezes cada (cor, textura) apareceu no saco e no fundo das fotos anotadas.

    A probabilidade de um píxel ser saco sai destas contagens (Bayes, com as duas classes
    igualmente prováveis), suavizadas para as cores vizinhas. Cada foto aprendida acrescenta
    as suas contagens (as fotos têm de estar à resolução de reduzir()).
    """

    TAMANHO = NIVEIS_COR**3 * NIVEIS_TEXTURA

    def __init__(self, saco: np.ndarray | None = None, fundo_: np.ndarray | None = None, fotos: int = 0) -> None:
        self.saco = np.zeros(self.TAMANHO) if saco is None else saco
        self.fundo = np.zeros(self.TAMANHO) if fundo_ is None else fundo_
        self.fotos = fotos

    @property
    def treinado(self) -> bool:
        return self.saco.sum() > 0 and self.fundo.sum() > 0

    def aprender(self, img: np.ndarray, saco: np.ndarray) -> None:
        """Acrescenta uma foto anotada (img e máscara do saco na mesma resolução)."""
        indices = _caracteristicas(img)
        self.saco += np.bincount(indices[saco], minlength=self.TAMANHO)
        self.fundo += np.bincount(indices[~saco], minlength=self.TAMANHO)
        self.fotos += 1

    def probabilidade(self, img: np.ndarray) -> np.ndarray:
        """P(saco) de cada píxel (float32)."""
        p_saco, p_fundo = ((c + 1) / (c.sum() + self.TAMANHO) for c in (_suavizar(self.saco), _suavizar(self.fundo)))
        return (p_saco / (p_saco + p_fundo)).astype(np.float32)[_caracteristicas(img)]

    def guardar(self, pasta: Path) -> None:
        """Grava modelo.npz em `pasta`."""
        pasta.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(pasta / "modelo.npz", saco=self.saco, fundo=self.fundo, fotos=self.fotos)

    @classmethod
    def carregar(cls, pasta: Path) -> Modelo:
        """O modelo guardado em `pasta` (modelo.npz), ou um vazio."""
        return cls.ler(pasta / "modelo.npz") if (pasta / "modelo.npz").is_file() else cls()

    @classmethod
    def ler(cls, caminho: Path) -> Modelo:
        """O modelo gravado no ficheiro .npz dado."""
        dados = np.load(caminho)
        return cls(dados["saco"], dados["fundo"], int(dados["fotos"]))


def reduzir(img: np.ndarray) -> np.ndarray:
    """A foto à resolução em que o modelo aprende e trabalha (lado maior M2_APRENDIDO_LADO_PX).

    A textura (desvio-padrão numa janela de 7 px) depende da escala: aprender e usar o
    modelo têm de ser à mesma resolução.
    """
    escala = min(1.0, D.M2_APRENDIDO_LADO_PX / max(img.shape[:2]))
    return cv2.resize(img, None, fx=escala, fy=escala, interpolation=cv2.INTER_AREA)


class SegmentadorAprendido(Segmentador):
    """O modelo de localizacao.aprendido_modelo. Usa também fecho_mm, abertura_mm e aprendido_score_min."""

    nome = "aprendido"
    _cache: dict[tuple[Path, float], Modelo] = {}  # (ficheiro, data de modificação) → modelo

    def __init__(self, cfg: Config) -> None:
        """Lê já o modelo (erro claro logo no arranque se não existir ou estiver vazio)."""
        self.cfg = cfg
        caminho = Path(cfg.localizacao.aprendido_modelo)
        caminho = caminho if caminho.is_absolute() else RAIZ / caminho
        if not caminho.is_file():
            raise FileNotFoundError(f"localizacao.aprendido_modelo não encontrado: {caminho} (anota fotos com "
                                    "python -m ferramentas.anotar, que o cria)")
        chave = (caminho, caminho.stat().st_mtime)
        if chave not in self._cache:
            self._cache[chave] = Modelo.ler(caminho)
        self.modelo = self._cache[chave]
        if not self.modelo.treinado:
            raise ValueError(f"o modelo {caminho} ainda não aprendeu nenhuma foto com saco e fundo")

    def segmentar(self, img: np.ndarray, px_por_mm: float, guardar_intermedios: bool = False) -> ResultadoSegmentacao:
        inicio = perf_counter()
        p = self.cfg.localizacao
        pequena = reduzir(img)
        px_mm = px_por_mm * pequena.shape[1] / img.shape[1]
        probabilidade = cv2.GaussianBlur(self.modelo.probabilidade(pequena), (0, 0), D.M2_APRENDIDO_SUAVIZACAO_PX)
        mascara = (probabilidade > D.M2_APRENDIDO_PROBABILIDADE_MIN).astype(np.uint8) * 255
        regiao = segmentacao.maior_regiao(segmentacao.limpar(mascara, p.fecho_mm * px_mm, p.abertura_mm * px_mm))
        if regiao is None:
            raise SemSaco(f"o modelo aprendido não vê nenhum saco (P(saco) máxima {probabilidade.max():.2f})")
        saco = _completar_com_grabcut(pequena, regiao > 0, max(2, round(p.faixa_fundo_mm * px_mm)))
        score = separacao(probabilidade, saco)
        intermedios = {"probabilidade": (probabilidade * 255).astype(np.uint8), "regiao_provavel": regiao,
                       "segmentacao": saco.astype(np.uint8) * 255} if guardar_intermedios else {}
        if score < p.aprendido_score_min:
            raise SegmentacaoIncerta(f"segmentação incerta: o modelo aprendido mal separa o saco do que o rodeia "
                                     f"({score:.2f} < {p.aprendido_score_min:g}); anota mais fotos deste fundo")
        lab = fundo.lab(pequena, D.M2_SUAVIZACAO_MM * px_mm)
        return ResultadoSegmentacao(saco, score, self.nome, (perf_counter() - inicio) * 1000, lab,
                                    {"score_aprendido": score, "fotos_aprendidas": float(self.modelo.fotos)},
                                    intermedios)


def sementes(saco: np.ndarray, faixa: int) -> np.ndarray:
    """Etiquetas iniciais do GrabCut (cv2.GC_*) a partir de uma região provável do saco: o miolo
    é saco, a zona junto à aresta fica por decidir, longe dela e a faixa da borda é fundo.
    Também usada pela proposta do anotador (ferramentas/anotar.py)."""
    lado = max(3, round(D.M2_APRENDIDO_FAIXA_INCERTA * max(saco.shape)))
    s = saco.astype(np.uint8)
    etiquetas = np.full(saco.shape, cv2.GC_BGD, np.uint8)
    etiquetas[cv2.dilate(s, np.ones((4 * lado, 4 * lado), np.uint8)) > 0] = cv2.GC_PR_BGD
    etiquetas[cv2.dilate(s, np.ones((lado, lado), np.uint8)) > 0] = cv2.GC_PR_FGD
    etiquetas[cv2.erode(s, np.ones((4 * lado, 4 * lado), np.uint8)) > 0] = cv2.GC_FGD
    etiquetas[:faixa], etiquetas[-faixa:], etiquetas[:, :faixa], etiquetas[:, -faixa:] = (cv2.GC_BGD,) * 4
    return etiquetas


def separacao(probabilidade: np.ndarray, saco: np.ndarray) -> float:
    """P(saco) média dentro do saco menos a da faixa à volta dele (0 a 1)."""
    lado = max(3, round(D.M2_APRENDIDO_FAIXA_INCERTA * max(saco.shape)))
    s = saco.astype(np.uint8)
    perto = cv2.dilate(s, np.ones((lado, lado), np.uint8)) > 0
    anel = (cv2.dilate(s, np.ones((4 * lado, 4 * lado), np.uint8)) > 0) & ~perto
    fora = float(probabilidade[anel].mean()) if anel.any() else 0.0
    return float(np.clip(probabilidade[saco].mean() - fora, 0, 1))


def _completar_com_grabcut(img: np.ndarray, provavel: np.ndarray, faixa: int) -> np.ndarray:
    """O saco: a região provável afinada pelo GrabCut, numa versão reduzida (é o passo lento).
    Se o GrabCut não deixar nada, fica a região provável."""
    escala = min(1.0, D.M2_APRENDIDO_GRABCUT_LADO_PX / max(img.shape[:2]))
    pequena = cv2.resize(img, None, fx=escala, fy=escala, interpolation=cv2.INTER_AREA)
    reduzida = cv2.resize(provavel.astype(np.uint8), pequena.shape[1::-1], interpolation=cv2.INTER_NEAREST) > 0
    etiquetas = sementes(reduzida, max(2, round(faixa * escala)))
    cv2.grabCut(pequena, etiquetas, None, np.zeros((1, 65)), np.zeros((1, 65)), D.M2_APRENDIDO_GRABCUT_ITERACOES,
                cv2.GC_INIT_WITH_MASK)
    regiao = segmentacao.maior_regiao(np.isin(etiquetas, (cv2.GC_FGD, cv2.GC_PR_FGD)).astype(np.uint8) * 255)
    if regiao is None:
        return provavel
    return cv2.resize(regiao, img.shape[1::-1], interpolation=cv2.INTER_LINEAR) > 127


def _caracteristicas(img: np.ndarray) -> np.ndarray:
    """O índice (cor, textura) de cada píxel: CIELAB em NIVEIS_COR níveis e o desvio-padrão
    local da luminosidade em NIVEIS_TEXTURA níveis."""
    suave = cv2.GaussianBlur(img, (0, 0), D.M2_APRENDIDO_COR_SUAVIZACAO_PX)
    lab = cv2.cvtColor(suave, cv2.COLOR_BGR2LAB).astype(np.int64) * NIVEIS_COR // 256
    cinza = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    janela = (D.M2_APRENDIDO_TEXTURA_JANELA_PX,) * 2
    media = cv2.blur(cinza, janela)
    desvio = np.sqrt(np.maximum(cv2.blur(cinza * cinza, janela) - media * media, 0))
    textura = np.minimum((desvio / D.M2_APRENDIDO_TEXTURA_MAX * NIVEIS_TEXTURA).astype(np.int64), NIVEIS_TEXTURA - 1)
    cor = (lab[..., 0] * NIVEIS_COR + lab[..., 1]) * NIVEIS_COR + lab[..., 2]
    return cor * NIVEIS_TEXTURA + textura


def _suavizar(contagens: np.ndarray) -> np.ndarray:
    """As contagens espalhadas pelas cores vizinhas ([1 2 1] em cada eixo de cor)."""
    h = contagens.reshape(NIVEIS_COR, NIVEIS_COR, NIVEIS_COR, NIVEIS_TEXTURA)
    for eixo in range(3):
        h = (np.roll(h, 1, eixo) + 2 * h + np.roll(h, -1, eixo)) / 4
    return h.ravel()
