r"""Anotar o saco em fotos com qualquer fundo: máscaras e polígonos para treinar uma rede de segmentação.

    python -m ferramentas.anotar dataset\telemovel
    python -m ferramentas.anotar "D:\Fotos novas" --saida dataset\segmentacao

Para cada foto (também nas subpastas) aparece logo uma proposta do contorno do saco, a
verde. Se estiver certa: Enter. Se não: arrastar um retângulo à volta do saco (o GrabCut
separa-o do fundo lá dentro), ou marcar o contorno ponto a ponto, com zoom; se ainda for
preciso, corrigir com o pincel.

    0          retângulo: arrastar à volta do saco
    1 / 2      pincel "é saco" / "é fundo" (cada traço volta a correr o GrabCut)
    3          pontos: um clique por ponto do contorno; fecha com duplo clique, clique no 1.º
               ponto ou Enter; Backspace apaga o último, Esc desiste. O polígono é a máscara,
               à resolução total da foto
    e          editar: o contorno atual passa a pontos (modo 3), que se arrastam com o rato
    g          o GrabCut encosta o contorno à aresta do saco (a poucos px dos pontos)
    s          proposta do SAM 3 pelo texto de --prompt ("plastic bag")
    4          SAM 2: clique = ponto do saco, Ctrl+clique = ponto do fundo, arrastar = caixa
               (cada clique refaz a máscara; c limpa os cliques)
    roda       zoom à volta do rato     botão direito: mover a vista     z: sem zoom
    r          volta à proposta automática
    Enter      guardar      v  guardar sem saco (só fundo)      n  saltar      q/Esc  sair

O programa aprende com cada foto guardada: um modelo das cores e da textura do saco e
do fundo (modelo.npz, em --saida) passa a dar a proposta seguinte, e o GrabCut afina-a.
Quanto mais fotos (e fundos) anotados, melhores as propostas. É o mesmo modelo que o M2
usa com localizacao.segmentador = "aprendido" (modulos/m2_localizacao/segmentadores/aprendido.py).

    python -m ferramentas.anotar --treinar     refaz o modelo com todas as anotações
                                               (depois de apagar ou corrigir máscaras à mão)

O SAM 3 e o SAM 2 são opcionais (PyTorch, GPU e os pesos do Hugging Face; ver o README).
Sem eles, as teclas s e 4 só avisam.

Em --saida (omissão: dataset\segmentacao), com o mesmo nome para cada foto:
    imagens/             a foto original
    mascaras/            PNG, 255 = saco (para U-Net e afins)
    etiquetas/           TXT do YOLO-seg: "0 x1 y1 x2 y2 ..." (polígono, coordenadas de 0 a 1)
    anotacoes_coco.json  COCO com polígonos (para o RF-DETR-Seg)
    metadados.csv        nome; saco (físico); tipo_saco; fundo: para dividir sem misturar o
                         mesmo saco entre treino e teste (ferramentas/dividir_dataset.py)
--tipo, --fundo e --saco dão os metadados das fotos da sessão (--saco por omissão: o nome
da pasta da foto, por isso convém uma pasta por saco físico).
As fotos já anotadas são saltadas: pode-se parar e continuar mais tarde.

    python -m ferramentas.anotar --coco        refaz anotacoes_coco.json com todas as máscaras

É o primeiro passo: com algumas centenas de fotos anotadas, as mesmas máscaras treinam
uma rede de segmentação (U-Net, YOLO-seg), que aprende formas e não só cores.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

if __package__ in (None, ""):  # corrido como ficheiro: python ferramentas\anotar.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modulos.m2_localizacao import fundo  # noqa: E402
from modulos.m2_localizacao.segmentadores import sam3  # noqa: E402
from modulos.m2_localizacao.segmentadores.aprendido import Modelo, reduzir, sementes  # noqa: E402
from nucleo import defines as D  # noqa: E402
from nucleo.config import RAIZ  # noqa: E402
from nucleo.imagens import guardar_imagem, ler_imagem  # noqa: E402

EXTENSOES = (".jpg", ".jpeg", ".png", ".bmp")
LADO_TRABALHO = D.M2_APRENDIDO_LADO_PX  # o GrabCut, o ecrã e o modelo trabalham com este lado maior (px)
FAIXA_BORDA = 0.03  # fração da imagem junto à borda que se assume ser fundo
ITERACOES = 3  # do GrabCut
PINCEL = 6  # raio do pincel (px no ecrã)
ZOOM_PASSO = 1.25  # cada volta da roda do rato
ZOOM_MAX = 20.0
FECHAR_PX = 8  # um clique a menos disto (no ecrã) do 1.º ponto fecha o polígono
FAIXA_AFINAR = 4  # tecla g: o GrabCut pode mover o contorno até isto (px de trabalho) para cada lado
JANELA = "anotar o saco"
EDITAR_PX = 1.5  # tecla e: o contorno vira pontos a esta tolerância (px da foto)
SAM2_MODELO = "facebook/sam2.1-hiera-large"  # pesos do SAM 2 no Hugging Face
METADADOS = ("saco", "tipo_saco", "fundo")


# --- Segmentação (sem interface) -------------------------------------------------------------

def proposta(img: np.ndarray, modelo: Modelo | None = None) -> np.ndarray:
    """Etiquetas do GrabCut (cv2.GC_*) para a imagem, já ajustadas: a primeira proposta.

    Ponto de partida: com um modelo treinado, os píxeis que ele acha mais "saco" do que
    "fundo"; sem modelo (ou se não achar nada), o que difere do fundo, que é uma superfície
    suave ajustada à cor da borda (a do M2, que acompanha um gradiente de luz): ΔE em
    CIELAB acima de metade do limiar de Otsu. A maior região é "provavelmente saco"; o
    GrabCut afina a partir daí com modelos de cor do saco e do fundo desta foto.
    """
    faixa = max(2, round(FAIXA_BORDA * min(img.shape[:2])))
    saco = np.zeros(img.shape[:2], bool)
    if modelo is not None and modelo.treinado:
        provavel = cv2.GaussianBlur(modelo.probabilidade(img), (0, 0), D.M2_APRENDIDO_SUAVIZACAO_PX) > \
            D.M2_APRENDIDO_PROBABILIDADE_MIN
        _fundo_na_borda(provavel, faixa)
        saco = mascara(np.where(provavel, cv2.GC_PR_FGD, cv2.GC_BGD).astype(np.uint8))
    if not saco.any():
        saco = _diferente_da_borda(img, faixa)
    if saco.any():
        etiquetas = sementes(saco, faixa)  # as mesmas do segmentador aprendido do M2
    else:  # nada se destaca: o centro da imagem
        a, l = img.shape[:2]
        etiquetas = np.full(img.shape[:2], cv2.GC_BGD, np.uint8)
        etiquetas[a // 10:-a // 10, l // 10:-l // 10] = cv2.GC_PR_FGD
        _fundo_na_borda(etiquetas, faixa)
    return grabcut(img, etiquetas)


def _diferente_da_borda(img: np.ndarray, faixa: int) -> np.ndarray:
    """A maior região que difere da cor da borda (ΔE acima de metade do Otsu)."""
    cor = fundo.lab(img, 2.0)
    diferenca = fundo.diferenca(cor, fundo.modelar_fundo(cor, fundo.faixa_da_borda(img.shape[:2], faixa)), 1.0)
    escala = 255 / max(float(diferenca.max()), 1e-6)
    otsu, _ = cv2.threshold((diferenca * escala).astype(np.uint8), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Metade do Otsu: um saco com partes de cores diferentes (orla azul, centro prateado) entra todo
    return mascara(np.where(diferenca * escala > otsu / 2, cv2.GC_PR_FGD, cv2.GC_BGD).astype(np.uint8))


def com_retangulo(img: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    """Etiquetas do GrabCut com o saco dentro do retângulo dado (e só fundo fora dele)."""
    x0, x1 = sorted((max(0, x0), min(img.shape[1] - 1, x1)))
    y0, y1 = sorted((max(0, y0), min(img.shape[0] - 1, y1)))
    etiquetas = np.full(img.shape[:2], cv2.GC_BGD, np.uint8)
    etiquetas[y0:y1 + 1, x0:x1 + 1] = cv2.GC_PR_FGD
    return grabcut(img, etiquetas)


def grabcut(img: np.ndarray, etiquetas: np.ndarray) -> np.ndarray:
    """O GrabCut a partir das etiquetas dadas (as certas, GC_BGD/GC_FGD, não mudam)."""
    etiquetas = etiquetas.copy()
    if not np.isin(etiquetas, (cv2.GC_FGD, cv2.GC_PR_FGD)).any() or \
            not np.isin(etiquetas, (cv2.GC_BGD, cv2.GC_PR_BGD)).any():
        return etiquetas  # o GrabCut precisa das duas classes
    cv2.grabCut(img, etiquetas, None, np.zeros((1, 65)), np.zeros((1, 65)), ITERACOES, cv2.GC_INIT_WITH_MASK)
    return etiquetas


def mascara(etiquetas: np.ndarray) -> np.ndarray:
    """O saco (bool): a maior região marcada como saco, com os buracos tapados."""
    saco = _maior_regiao(np.isin(etiquetas, (cv2.GC_FGD, cv2.GC_PR_FGD)))
    contornos, _ = cv2.findContours(saco.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cheio = np.zeros(saco.shape, np.uint8)
    cv2.drawContours(cheio, contornos, -1, 1, cv2.FILLED)
    return cheio > 0


def poligono_yolo(mascara_cheia: np.ndarray) -> str:
    """A linha do YOLO-seg para a máscara: "0 x1 y1 x2 y2 ..." (de 0 a 1); "" sem saco."""
    contornos, _ = cv2.findContours(mascara_cheia.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contornos:
        return ""
    contorno = max(contornos, key=cv2.contourArea)
    pontos = cv2.approxPolyDP(contorno, 0.001 * cv2.arcLength(contorno, True), True).reshape(-1, 2)
    altura, largura = mascara_cheia.shape
    normalizados = (pontos + 0.5) / (largura, altura)
    return "0 " + " ".join(f"{x:.5f} {y:.5f}" for x, y in normalizados)


def guardar(origem: Path, nome: str, saida: Path, mascara_cheia: np.ndarray | None,
            metadados: dict[str, str] | None = None) -> None:
    """A foto, a máscara (vazia = sem saco), a etiqueta do YOLO-seg, os metadados e a entrada
    no COCO, todos com o mesmo nome."""
    img = ler_imagem(origem)
    if mascara_cheia is None:
        mascara_cheia = np.zeros(img.shape[:2], bool)
    destino = saida / "imagens" / f"{nome}{origem.suffix.lower()}"
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(origem, destino)
    guardar_imagem(saida / "mascaras" / f"{nome}.png", mascara_cheia.astype(np.uint8) * 255)
    (saida / "etiquetas").mkdir(parents=True, exist_ok=True)
    linha = poligono_yolo(mascara_cheia)
    (saida / "etiquetas" / f"{nome}.txt").write_text(linha + "\n" if linha else "", encoding="utf-8")
    todos = ler_metadados(saida)
    todos[nome] = {c: (metadados or {}).get(c, "") for c in METADADOS}
    escrever_metadados(saida, todos)
    coco = ler_coco(saida)
    _coco_imagem(coco, nome, destino.name, mascara_cheia, todos[nome])
    escrever_coco(saida, coco)


# --- Metadados e COCO -------------------------------------------------------------------------

def ler_metadados(saida: Path) -> dict[str, dict[str, str]]:
    """nome → {saco, tipo_saco, fundo}, de metadados.csv ({} se não existir)."""
    caminho = saida / "metadados.csv"
    if not caminho.exists():
        return {}
    with caminho.open(encoding="utf-8", newline="") as ficheiro:
        return {linha["nome"]: {c: linha.get(c, "") for c in METADADOS}
                for linha in csv.DictReader(ficheiro, delimiter=";")}


def escrever_metadados(saida: Path, todos: dict[str, dict[str, str]]) -> None:
    saida.mkdir(parents=True, exist_ok=True)
    with (saida / "metadados.csv").open("w", encoding="utf-8", newline="") as ficheiro:
        escritor = csv.writer(ficheiro, delimiter=";")
        escritor.writerow(["nome", *METADADOS])
        for nome in sorted(todos):
            escritor.writerow([nome, *(todos[nome].get(c, "") for c in METADADOS)])


def ler_coco(saida: Path) -> dict[str, Any]:
    """anotacoes_coco.json; se ainda não existir, feito a partir das máscaras já guardadas."""
    caminho = saida / "anotacoes_coco.json"
    if caminho.exists():
        return json.loads(caminho.read_text(encoding="utf-8"))
    return exportar_coco(saida, escrever=False)


def escrever_coco(saida: Path, coco: dict[str, Any]) -> None:
    (saida / "anotacoes_coco.json").write_text(json.dumps(coco, ensure_ascii=False), encoding="utf-8")


def exportar_coco(saida: Path, escrever: bool = True) -> dict[str, Any]:
    """O COCO de todas as anotações em `saida` (imagens/, mascaras/, metadados.csv)."""
    coco = _coco_vazio()
    metadados = ler_metadados(saida)
    for f in sorted((saida / "mascaras").glob("*.png")):
        imagem = next((saida / "imagens").glob(f"{f.stem}.*"), None)
        saco = ler_imagem(f)
        if imagem is None or saco is None:
            continue
        _coco_imagem(coco, f.stem, imagem.name, saco[..., 0] > 127, metadados.get(f.stem, {}))
    if escrever:
        escrever_coco(saida, coco)
    return coco


def _coco_vazio() -> dict[str, Any]:
    return {"info": {"description": "sacos anotados (ferramentas/anotar.py)"}, "images": [], "annotations": [],
            "categories": [{"id": 1, "name": "saco", "supercategory": "saco"}]}


def _coco_imagem(coco: dict[str, Any], nome: str, ficheiro: str, saco: np.ndarray, metadados: dict[str, str]) -> None:
    """Põe (ou substitui) a imagem `nome` e o polígono do saco no COCO."""
    antiga = next((i for i in coco["images"] if i["nome"] == nome), None)
    if antiga is not None:
        coco["images"].remove(antiga)
        coco["annotations"] = [a for a in coco["annotations"] if a["image_id"] != antiga["id"]]
    id_imagem = max((i["id"] for i in coco["images"]), default=0) + 1
    altura, largura = saco.shape
    coco["images"].append({"id": id_imagem, "file_name": f"imagens/{ficheiro}", "width": largura, "height": altura,
                           "nome": nome, **{c: metadados.get(c, "") for c in METADADOS}})
    contornos, _ = cv2.findContours(saco.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contornos:
        return  # foto sem saco: só a imagem
    contorno = max(contornos, key=cv2.contourArea)
    pontos = cv2.approxPolyDP(contorno, 1.0, True).reshape(-1, 2).astype(float)
    x, y, w, h = cv2.boundingRect(contorno)
    coco["annotations"].append({
        "id": max((a["id"] for a in coco["annotations"]), default=0) + 1, "image_id": id_imagem, "category_id": 1,
        "segmentation": [pontos.ravel().round(1).tolist()], "area": float(np.count_nonzero(saco)),
        "bbox": [float(x), float(y), float(w), float(h)], "iscrowd": 0})


# --- SAM (opcional) -----------------------------------------------------------------------------

_SAM2: Any = None


def sam2_prever(img: np.ndarray, pontos: list[tuple[float, float, int]],
                caixa: tuple[float, float, float, float] | None) -> np.ndarray:
    """A máscara do SAM 2 (bool, à resolução da foto) para os cliques (x, y, 1 saco / 0 fundo)
    e a caixa (x0, y0, x1, y1), em px da foto. RuntimeError se o SAM 2 não estiver instalado."""
    global _SAM2
    try:
        import torch
        from sam2.sam2_image_predictor import SAM2ImagePredictor
    except ImportError as erro:
        raise RuntimeError(f"SAM 2 não instalado (ver o README): {erro}") from erro
    if _SAM2 is None:
        _SAM2 = [SAM2ImagePredictor.from_pretrained(SAM2_MODELO), None]
    preditor = _SAM2[0]
    with torch.inference_mode():
        if _SAM2[1] is not img:  # a mesma foto só se prepara uma vez
            preditor.set_image(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
            _SAM2[1] = img
        coordenadas = np.array([(x, y) for x, y, _ in pontos], np.float32) if pontos else None
        rotulos = np.array([r for _, _, r in pontos], np.int32) if pontos else None
        mascaras, _, _ = preditor.predict(point_coords=coordenadas, point_labels=rotulos,
                                          box=None if caixa is None else np.array(caixa, np.float32),
                                          multimask_output=False)
    return np.asarray(mascaras)[0] > 0


def ampliar(mascara_pequena: np.ndarray, forma: tuple[int, ...]) -> np.ndarray:
    """A máscara da versão de trabalho na resolução da foto (aresta suave, não em degraus)."""
    grande = cv2.resize(mascara_pequena.astype(np.float32), (forma[1], forma[0]), interpolation=cv2.INTER_LINEAR)
    return cv2.GaussianBlur(grande, (0, 0), max(forma[:2]) / LADO_TRABALHO / 2) > 0.5


def _maior_regiao(binaria: np.ndarray) -> np.ndarray:
    n, rotulos, estatisticas, _ = cv2.connectedComponentsWithStats(binaria.astype(np.uint8), connectivity=8)
    if n <= 1:
        return np.zeros(binaria.shape, bool)
    return rotulos == 1 + int(np.argmax(estatisticas[1:, cv2.CC_STAT_AREA]))


def _fundo_na_borda(etiquetas: np.ndarray, faixa: int) -> None:
    """A faixa junto à borda passa a fundo (GC_BGD, ou False numa máscara booleana)."""
    valor = False if etiquetas.dtype == bool else cv2.GC_BGD
    etiquetas[:faixa], etiquetas[-faixa:], etiquetas[:, :faixa], etiquetas[:, -faixa:] = (valor,) * 4


# --- O modelo que aprende com as anotações -----------------------------------------------------

def treinar(saida: Path) -> Modelo:
    """Um modelo novo com todas as anotações em `saida` (imagens/ e mascaras/); fica guardado."""
    modelo = Modelo()
    for f in sorted((saida / "mascaras").glob("*.png")):
        imagem = next((saida / "imagens").glob(f"{f.stem}.*"), None)
        img, saco = (ler_imagem(imagem) if imagem else None), ler_imagem(f)
        if img is None or saco is None:
            continue
        pequena = trabalho(img)
        modelo.aprender(pequena, cv2.resize(saco[..., 0], pequena.shape[1::-1], interpolation=cv2.INTER_NEAREST) > 127)
    modelo.guardar(saida)
    return modelo


def trabalho(img: np.ndarray) -> np.ndarray:
    """A versão de trabalho da foto (lado maior LADO_TRABALHO): a mesma em que o modelo aprende."""
    return reduzir(img)


# --- Interface (janela do OpenCV) --------------------------------------------------------------

class Anotacao:
    """Uma foto em anotação: as etiquetas do GrabCut, os pontos do polígono, o zoom e o rato.

    Três referenciais: a foto original (os pontos do polígono, para a máscara sair à
    resolução total), a versão de trabalho (GrabCut, pincel) e o ecrã (com zoom).
    """

    def __init__(self, original: np.ndarray, modelo: Modelo | None = None) -> None:
        self.original = original
        self.img = trabalho(original)
        self.escala = self.img.shape[1] / original.shape[1]  # px de trabalho por px da original
        self.etiquetas = proposta(self.img, modelo)
        self.tracos = np.zeros(self.img.shape[:2], np.uint8)  # o que se pintou: 1 saco, 2 fundo (só para ver)
        self.modo = 0  # 0 retângulo, 1 pincel "saco", 2 pincel "fundo", 3 pontos, 4 SAM 2
        self.pontos: list[tuple[float, float]] = []  # polígono a ser marcado (px da original)
        self.poligono: np.ndarray | None = None  # polígono fechado = a máscara exata (px da original)
        self.exata: np.ndarray | None = None  # máscara do SAM à resolução da foto (até ser editada)
        self.vertice: int | None = None  # vértice do polígono a ser arrastado
        self.cliques: list[tuple[float, float, int]] = []  # SAM 2: (x, y, 1 saco / 0 fundo), px da original
        self.caixa: tuple[float, float, float, float] | None = None  # SAM 2 (px da original)
        self.aviso = ""  # mostrado no ecrã (p.ex. "SAM 3 não instalado")
        self.zoom = 1.0
        self.origem = np.zeros(2)  # canto de cima à esquerda da vista (px da original)
        self.inicio: tuple[int, int] | None = None  # botão esquerdo carregado (ecrã)
        self.atual: tuple[int, int] | None = None  # onde está o rato (ecrã)
        self.arrasto: tuple[int, int] | None = None  # botão direito carregado (ecrã)

    # --- Referenciais ---
    def _para_original(self, x: float, y: float) -> np.ndarray:
        return self.origem + np.array([x, y], float) / (self.escala * self.zoom)

    def _para_trabalho(self, x: float, y: float) -> tuple[int, int]:
        px, py = self._para_original(x, y) * self.escala
        return round(px), round(py)

    def _para_ecra(self, pontos: np.ndarray) -> np.ndarray:
        """Pontos em px da original → ecrã."""
        return (np.asarray(pontos, float) - self.origem) * self.escala * self.zoom

    # --- Rato ---
    def rato(self, evento: int, x: int, y: int, flags: int, *_: object) -> None:
        if evento == cv2.EVENT_MOUSEWHEEL:
            self.ampliar(x, y, ZOOM_PASSO if _roda(flags) > 0 else 1 / ZOOM_PASSO)
            return
        if evento == cv2.EVENT_RBUTTONDOWN:
            self.arrasto = (x, y)
            return
        if evento == cv2.EVENT_RBUTTONUP:
            self.arrasto = None
            return
        if evento == cv2.EVENT_MOUSEMOVE and self.arrasto is not None:  # mover a vista
            self.origem -= np.subtract((x, y), self.arrasto) / (self.escala * self.zoom)
            self.arrasto = (x, y)
            self._limitar()
            return
        if evento == cv2.EVENT_MOUSEMOVE:
            self.atual = (x, y)
        if self.modo == 3:
            self._marcar_ponto(evento, x, y)
        elif self.modo == 4:
            self._clique_sam2(evento, x, y, flags)
        elif evento == cv2.EVENT_LBUTTONDOWN:
            self.inicio = self.atual = (x, y)
            self._pintar(self.inicio, self.inicio)
        elif evento == cv2.EVENT_MOUSEMOVE and self.inicio is not None:
            self._pintar(self.atual, (x, y))
        elif evento == cv2.EVENT_LBUTTONUP and self.inicio is not None:
            if self.modo:
                self.etiquetas = grabcut(self.img, self.etiquetas)
                self.poligono = self.exata = None
            elif abs(x - self.inicio[0]) > 10 and abs(y - self.inicio[1]) > 10:
                self.etiquetas = com_retangulo(self.img, *self._para_trabalho(*self.inicio), *self._para_trabalho(x, y))
                self.tracos[:] = 0
                self.poligono = self.exata = None
            self.inicio = None

    def _pintar(self, de: tuple[int, int] | None, ate: tuple[int, int]) -> None:
        """Um traço do pincel (ecrã), na versão de trabalho; o pincel fica do mesmo tamanho no ecrã."""
        if not self.modo or de is None:
            return
        raio = max(1, round(PINCEL / self.zoom))
        a, b = self._para_trabalho(*de), self._para_trabalho(*ate)
        etiqueta = cv2.GC_FGD if self.modo == 1 else cv2.GC_BGD
        cv2.line(self.etiquetas, a, b, etiqueta, 2 * raio)
        cv2.circle(self.etiquetas, b, raio, etiqueta, cv2.FILLED)
        cv2.line(self.tracos, a, b, self.modo, 2 * raio)
        cv2.circle(self.tracos, b, raio, self.modo, cv2.FILLED)

    def _marcar_ponto(self, evento: int, x: int, y: int) -> None:
        """Clique: mais um ponto; duplo clique, ou clique no 1.º ponto: fecha o polígono.
        Com um polígono fechado, arrastar um vértice move-o."""
        if self.vertice is not None:
            if evento == cv2.EVENT_MOUSEMOVE:
                self.poligono[self.vertice] = self._para_original(x, y)
            elif evento == cv2.EVENT_LBUTTONUP:
                self.vertice = None
                self._rasterizar()
            return
        if evento == cv2.EVENT_LBUTTONDOWN and not self.pontos and self.poligono is not None:
            distancias = np.hypot(*(self._para_ecra(self.poligono) - (x, y)).T)
            if distancias.min() < FECHAR_PX:
                self.vertice = int(np.argmin(distancias))
                return
        if evento == cv2.EVENT_LBUTTONDBLCLK and len(self.pontos) >= 3:
            self.fechar()
        elif evento == cv2.EVENT_LBUTTONDOWN:
            perto_do_primeiro = len(self.pontos) >= 3 and \
                np.hypot(*(self._para_ecra(self.pontos[0]) - (x, y))) < FECHAR_PX
            if perto_do_primeiro:
                self.fechar()
            else:
                self.pontos.append(tuple(self._para_original(x, y)))

    # --- Ações ---
    def fechar(self) -> None:
        """Fecha o polígono marcado: passa a ser a máscara (exata) do saco."""
        if len(self.pontos) < 3:
            return
        self.poligono, self.pontos = np.array(self.pontos, float), []
        self._rasterizar()

    def _rasterizar(self) -> None:
        """As etiquetas a partir do polígono (dentro: saco; fora: fundo)."""
        self.etiquetas = np.full(self.img.shape[:2], cv2.GC_BGD, np.uint8)
        cv2.fillPoly(self.etiquetas, [_subpixel(self.poligono * self.escala)], cv2.GC_FGD, cv2.LINE_8, 4)
        self.tracos[:] = 0
        self.exata = None

    def editar(self) -> None:
        """O contorno atual passa a pontos (polígono) que se podem arrastar no modo 3."""
        cheia = self.mascara_final()
        contornos, _ = cv2.findContours(cheia.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not contornos:
            return
        contorno = max(contornos, key=cv2.contourArea)
        self.poligono = cv2.approxPolyDP(contorno, EDITAR_PX, True).reshape(-1, 2).astype(float)
        self.pontos, self.modo = [], 3
        self._rasterizar()

    def usar_mascara(self, cheia: np.ndarray) -> None:
        """Uma máscara à resolução da foto (do SAM) passa a ser a anotação, tal como está."""
        pequena = cv2.resize(cheia.astype(np.uint8), self.img.shape[1::-1], interpolation=cv2.INTER_AREA) > 0
        self.etiquetas = np.where(pequena, cv2.GC_FGD, cv2.GC_BGD).astype(np.uint8)
        self.tracos[:] = 0
        self.poligono, self.exata = None, cheia

    def sam3_texto(self, prompt: str, score_min: float) -> None:
        """Proposta do SAM 3 pelo texto: a maior máscara confiante que não toca na borda."""
        try:
            mascaras, scores = sam3.mascaras_por_texto(self.original, prompt)
        except RuntimeError as erro:
            self.aviso = str(erro)[:110]
            return
        escolhida = sam3.escolher(mascaras, scores, score_min, rejeitar_borda=True)
        if escolhida is None:
            self.aviso = f"SAM 3: nada com confianca >= {score_min:g} para '{prompt}'"
        else:
            self.usar_mascara(escolhida[0])
            self.aviso = f"SAM 3: confianca {escolhida[1]:.2f}"

    def _clique_sam2(self, evento: int, x: int, y: int, flags: int) -> None:
        """SAM 2: clique = ponto do saco, Ctrl+clique = do fundo, arrastar = caixa."""
        if evento == cv2.EVENT_LBUTTONDOWN:
            self.inicio = (x, y)
        elif evento == cv2.EVENT_LBUTTONUP and self.inicio is not None:
            if abs(x - self.inicio[0]) > 10 and abs(y - self.inicio[1]) > 10:
                (x0, y0), (x1, y1) = self._para_original(*self.inicio), self._para_original(x, y)
                self.caixa = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            else:
                ox, oy = self._para_original(x, y)
                self.cliques.append((ox, oy, 0 if flags & cv2.EVENT_FLAG_CTRLKEY else 1))
            self.inicio = None
            try:
                self.usar_mascara(sam2_prever(self.original, self.cliques, self.caixa))
                self.aviso = f"SAM 2: {len(self.cliques)} cliques{' + caixa' if self.caixa else ''}"
            except RuntimeError as erro:
                self.aviso = str(erro)[:110]

    def afinar(self) -> None:
        """O GrabCut encosta o contorno atual à aresta do saco, a ±FAIXA_AFINAR px dele."""
        saco = mascara(self.etiquetas).astype(np.uint8)
        if not saco.any():
            return
        nucleo = np.ones((2 * FAIXA_AFINAR + 1,) * 2, np.uint8)
        etiquetas = np.full(saco.shape, cv2.GC_BGD, np.uint8)
        etiquetas[cv2.dilate(saco, nucleo) > 0] = cv2.GC_PR_BGD
        etiquetas[saco > 0] = cv2.GC_PR_FGD
        etiquetas[cv2.erode(saco, nucleo) > 0] = cv2.GC_FGD
        self.etiquetas = grabcut(self.img, etiquetas)
        self.poligono = self.exata = None

    def ampliar(self, x: float, y: float, fator: float) -> None:
        """Zoom à volta do ponto (x, y) do ecrã, que fica no mesmo sítio."""
        ponto = self._para_original(x, y)
        self.zoom = float(np.clip(self.zoom * fator, 1.0, ZOOM_MAX))
        self.origem = ponto - np.array([x, y], float) / (self.escala * self.zoom)
        self._limitar()

    def _limitar(self) -> None:
        visivel = np.array(self.img.shape[1::-1]) / (self.escala * self.zoom)
        self.origem = np.clip(self.origem, 0, np.maximum(np.array(self.original.shape[1::-1]) - visivel, 0))

    def mascara_final(self) -> np.ndarray:
        """O saco à resolução da foto: o polígono marcado ou a máscara do SAM, se houver, senão a
        máscara de trabalho ampliada."""
        if self.exata is not None:
            return self.exata
        if self.poligono is None:
            return ampliar(mascara(self.etiquetas), self.original.shape)
        cheia = np.zeros(self.original.shape[:2], np.uint8)
        cv2.fillPoly(cheia, [_subpixel(self.poligono)], 1, cv2.LINE_8, 4)
        return cheia > 0

    # --- Ecrã ---
    def desenhar(self, titulo: str) -> np.ndarray:
        h, w = self.img.shape[:2]
        z, (ox, oy) = self.zoom, self.origem * self.escala  # a vista em px de trabalho
        de_trabalho = np.array([[z, 0, -ox * z], [0, z, -oy * z]])
        fator = self.escala * z
        if fator > 1:  # com zoom, a foto original (mais detalhe)
            de_original = np.array([[fator, 0, -self.origem[0] * fator], [0, fator, -self.origem[1] * fator]])
            vista = cv2.warpAffine(self.original, de_original, (w, h), flags=cv2.INTER_LINEAR)
        else:
            vista = cv2.warpAffine(self.img, de_trabalho, (w, h), flags=cv2.INTER_LINEAR)

        if not self.pontos:  # a marcar pontos, o contorno anterior só atrapalha
            saco = cv2.warpAffine(mascara(self.etiquetas).astype(np.uint8), de_trabalho, (w, h),
                                  flags=cv2.INTER_NEAREST) > 0
            vista[saco] = (0.7 * vista[saco] + 0.3 * np.array((0, 200, 0))).astype(np.uint8)
            if self.poligono is not None:
                contornos = [_subpixel(self._para_ecra(self.poligono))]
            else:
                contornos, _ = cv2.findContours(mascara(self.etiquetas).astype(np.uint8), cv2.RETR_EXTERNAL,
                                                cv2.CHAIN_APPROX_NONE)
                contornos = [_subpixel(c.reshape(-1, 2) * z - (ox * z, oy * z)) for c in contornos]
            cv2.polylines(vista, contornos, True, (0, 255, 0), 1, cv2.LINE_AA, 4)
        tracos = cv2.warpAffine(self.tracos, de_trabalho, (w, h), flags=cv2.INTER_NEAREST)
        vista[tracos == 1] = (255, 255, 255)  # pincel "saco": branco
        vista[tracos == 2] = (0, 0, 255)  # pincel "fundo": vermelho

        if self.modo in (0, 4) and self.inicio is not None and self.atual is not None:
            cv2.rectangle(vista, self.inicio, self.atual, (0, 255, 255), 2)
        if self.poligono is not None and self.modo == 3 and not self.pontos:  # vértices que se arrastam
            for p in np.round(self._para_ecra(self.poligono)).astype(np.int32):
                cv2.circle(vista, tuple(p), 3, (0, 255, 255), 1, cv2.LINE_AA)
        if self.modo == 4:
            for cx, cy, rotulo in self.cliques:
                p = tuple(np.round(self._para_ecra((cx, cy))).astype(np.int32))
                cv2.circle(vista, p, 5, (0, 255, 0) if rotulo else (0, 0, 255), -1, cv2.LINE_AA)
            if self.caixa is not None:
                a, b = (tuple(np.round(self._para_ecra(c)).astype(np.int32)) for c in (self.caixa[:2], self.caixa[2:]))
                cv2.rectangle(vista, a, b, (255, 200, 0), 1)
        if self.pontos:  # o polígono a ser marcado
            no_ecra = np.round(self._para_ecra(self.pontos)).astype(np.int32)
            cv2.polylines(vista, [no_ecra], False, (0, 255, 255), 1, cv2.LINE_AA)
            if self.atual is not None:
                cv2.line(vista, tuple(no_ecra[-1]), self.atual, (0, 200, 255), 1, cv2.LINE_AA)
            for i, p in enumerate(no_ecra):
                cv2.circle(vista, tuple(p), FECHAR_PX if i == 0 else 3, (0, 255, 255), 1 if i == 0 else -1)

        modo = ("retangulo", "pincel SACO", "pincel FUNDO", "pontos", "SAM 2")[self.modo]
        dicas = {3: "clique: ponto | duplo clique, 1o ponto ou Enter: fecha | arrastar um vertice | Backspace",
                 4: "clique: saco | Ctrl+clique: fundo | arrastar: caixa | c limpa os cliques"}
        linhas = [f"{titulo} | {modo} | zoom {z:.1f}x" + (f" | {self.aviso}" if self.aviso else ""),
                  dicas.get(self.modo, "Enter guarda | v sem saco | n salta | r repoe | q sai"),
                  "0 retangulo 1/2 pincel 3 pontos 4 SAM2 s SAM3 | e editar g afina | roda zoom, b.dir. mover, z"]
        cv2.rectangle(vista, (0, 0), (w, 18 * len(linhas) + 6), (0, 0, 0), cv2.FILLED)
        for i, linha in enumerate(linhas):
            cv2.putText(vista, linha, (6, 17 + 18 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1,
                        cv2.LINE_AA)
        return vista


def _roda(flags: int) -> int:
    """O sentido da roda do rato (> 0: para a frente): a palavra alta, com sinal, dos flags."""
    valor = flags & 0xFFFFFFFF
    return (valor - (1 << 32) if valor >= 1 << 31 else valor) >> 16


def _subpixel(pontos: np.ndarray) -> np.ndarray:
    """Pontos para as funções de desenho com shift=4 (1/16 de px)."""
    return np.round(np.asarray(pontos, float).reshape(-1, 2) * 16).astype(np.int32)


def fotos(pasta: Path, saida: Path) -> list[tuple[Path, str]]:
    """(foto, nome na saída) das fotos de `pasta` e subpastas, sem as de `saida` nem as já anotadas."""
    lista = []
    for f in sorted(pasta.rglob("*")):
        if f.suffix.lower() not in EXTENSOES or saida.resolve() in f.resolve().parents:
            continue
        nome = "__".join(f.relative_to(pasta).with_suffix("").parts)
        if not (saida / "mascaras" / f"{nome}.png").exists():
            lista.append((f, nome))
    return lista


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Anotar o saco em fotos com qualquer fundo (GrabCut).")
    parser.add_argument("pasta", type=Path, nargs="?", help="pasta com as fotos (e subpastas)")
    parser.add_argument("--saida", type=Path, default=RAIZ / "dataset" / "segmentacao",
                        help="onde ficam imagens/, mascaras/, etiquetas/ e modelo.npz")
    parser.add_argument("--treinar", action="store_true", help="refazer o modelo com todas as anotações e sair")
    parser.add_argument("--coco", action="store_true", help="refazer anotacoes_coco.json com todas as máscaras e sair")
    parser.add_argument("--tipo", default="", help="tipo de saco das fotos desta sessão (p.ex. PreVital)")
    parser.add_argument("--fundo", default="", help="fundo das fotos desta sessão (p.ex. preto, inox, tapete)")
    parser.add_argument("--saco", default="", help="saco físico (por omissão: o nome da pasta de cada foto)")
    parser.add_argument("--prompt", default="plastic bag", help="texto para a proposta do SAM 3 (tecla s)")
    parser.add_argument("--score-sam3", type=float, default=0.5, help="confiança mínima do SAM 3 (tecla s)")
    args = parser.parse_args(argv)
    if args.treinar:
        modelo = treinar(args.saida)
        print(f"Modelo refeito com {modelo.fotos} fotos anotadas em {args.saida}")
        return 0
    if args.coco:
        coco = exportar_coco(args.saida)
        print(f"anotacoes_coco.json com {len(coco['images'])} imagens e {len(coco['annotations'])} sacos")
        return 0
    if args.pasta is None:
        parser.error("indica a pasta com as fotos (ou --treinar)")
    modelo = Modelo.carregar(args.saida)
    print(f"Modelo: {modelo.fotos} fotos aprendidas" if modelo.treinado else "Modelo: ainda sem fotos aprendidas")
    lista = fotos(args.pasta, args.saida)
    print(f"{len(lista)} fotos por anotar em {args.pasta}; resultados em {args.saida}")
    cv2.namedWindow(JANELA, cv2.WINDOW_AUTOSIZE)
    guardadas = 0
    for i, (f, nome) in enumerate(lista, start=1):
        original = ler_imagem(f)
        if original is None:
            continue
        anotacao = Anotacao(original, modelo)
        cv2.setMouseCallback(JANELA, anotacao.rato)
        while True:
            cv2.imshow(JANELA, anotacao.desenhar(f"{i}/{len(lista)} {nome}"))
            tecla = cv2.waitKey(30) & 0xFF
            if tecla in (13, 10) and anotacao.pontos:  # Enter com um polígono aberto: fecha-o
                anotacao.fechar()
                continue
            if tecla in (13, 10, ord("v")):  # Enter: com saco; v: sem saco
                saco = mascara(anotacao.etiquetas) if tecla != ord("v") else np.zeros(anotacao.img.shape[:2], bool)
                metadados = {"saco": args.saco or f.parent.name, "tipo_saco": args.tipo, "fundo": args.fundo}
                guardar(f, nome, args.saida, anotacao.mascara_final() if saco.any() else None, metadados)
                modelo.aprender(anotacao.img, saco)  # a próxima proposta já conta com esta foto
                modelo.guardar(args.saida)
                guardadas += 1
                break
            if tecla == ord("n"):
                break
            if tecla == 27 and anotacao.pontos:  # Esc com um polígono aberto: desiste dele
                anotacao.pontos = []
                continue
            if tecla in (ord("q"), 27):
                cv2.destroyAllWindows()
                print(f"{guardadas} fotos anotadas.")
                return 0
            if tecla in (ord("0"), ord("1"), ord("2"), ord("3"), ord("4")):
                anotacao.modo = tecla - ord("0")
            elif tecla == ord("e"):
                anotacao.editar()
            elif tecla == ord("s"):
                anotacao.aviso = "SAM 3 a pensar..."
                cv2.imshow(JANELA, anotacao.desenhar(f"{i}/{len(lista)} {nome}"))
                cv2.waitKey(1)
                anotacao.sam3_texto(args.prompt, args.score_sam3)
            elif tecla == ord("c"):
                anotacao.cliques, anotacao.caixa = [], None
            elif tecla == 8 and anotacao.pontos:  # Backspace: apaga o último ponto
                anotacao.pontos.pop()
            elif tecla == ord("g"):
                anotacao.afinar()
            elif tecla == ord("z"):
                anotacao.zoom, anotacao.origem = 1.0, np.zeros(2)
            elif tecla == ord("r"):
                anotacao.etiquetas = proposta(anotacao.img, modelo)
                anotacao.tracos[:] = 0
                anotacao.poligono, anotacao.pontos, anotacao.exata = None, [], None
    cv2.destroyAllWindows()
    print(f"{guardadas} fotos anotadas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
