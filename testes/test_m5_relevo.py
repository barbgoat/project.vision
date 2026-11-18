"""M5 Relevo: rugas em sacos sintéticos, a referência da impressão (sacos OK ensinados) e o detetor de retas."""

from __future__ import annotations

import math
import time

import cv2
import numpy as np
import pytest

from mestre import Mestre
from modulos.m2_localizacao import Localizador
from modulos.m5_relevo import escravo, linhas, referencia
from nucleo.contrato import Estado, TipoDefeito
from testes.apoio import config_teste, fundo

PPM = 5.0  # o px_por_mm de MONTAGEM
C_MM, L_MM = 150.0, 90.0  # o saco destes testes (abaixo dos 100 mm de LOCALIZACAO_LARGURA_MAX_MM)


def cena(rng: np.random.Generator, angulo: float = 12.0, desvio: tuple[float, float] = (0.0, 0.0),
         impressao: bool = False, vincos=(), cor: float = 200.0, comprimento: float = C_MM,
         largura: float = L_MM, queimar: float = 0.0, traco_escuro: bool = False, texto: bool = False) -> np.ndarray:
    """Um saco cinzento numa imagem da montagem, com "impressão" e vincos desenhados no referencial do
    saco. vincos: (x0, y0, x1, y1, contraste), em mm a partir do canto do saco (x ao longo do comprimento).

    A impressão: um bloco de texto (traços curtos e muito escuros: tinta) e um traço claro impresso,
    comprido, em diagonal: não é tinta e, sem a referência, passa por ruga. traco_escuro: o mesmo traço,
    mas escuro (comprido e fino como um vinco forte: também só sai com a referência). texto: só o bloco de
    texto. queimar: a fração do comprimento e da largura, ao
    centro, saturada (um reflexo forte; nas bordas, o M2 recusava o saco)."""
    w, h = int(comprimento * PPM), int(largura * PPM)
    lona = np.full((h, w), cor, np.float32)
    if impressao or texto:
        for y in range(60, 300, 12):
            for x in range(80, 330, 20):
                cv2.line(lona, (x, y), (x + 12, y), cor * 0.35, 2)
    if impressao:
        cv2.line(lona, (420, 80), (690, 420), min(cor * 1.2, 245.0), 2)
    if traco_escuro:
        cv2.line(lona, (420, 80), (690, 420), cor * 0.5, 2)
    for x0, y0, x1, y1, contraste in vincos:
        fio = np.zeros((h, w), np.float32)
        cv2.line(fio, (round(x0 * PPM), round(y0 * PPM)), (round(x1 * PPM), round(y1 * PPM)), 1.0, 2, cv2.LINE_AA)
        lona *= 1 - contraste * cv2.GaussianBlur(fio, (0, 0), 0.8)
    if queimar:
        dx, dy = round(w * (1 - queimar) / 2), round(h * (1 - queimar) / 2)
        lona[dy:h - dy, dx:w - dx] = 260.0  # com o ruído, continua >= 255
    lona += rng.normal(0, 1.5, lona.shape).astype(np.float32)
    altura, largura_img = 1000, 1250
    img = fundo(altura, largura_img, rng)
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angulo, 1.0)
    m[:, 2] += (largura_img / 2 - w / 2 + desvio[0], altura / 2 - h / 2 + desvio[1])
    saco = cv2.warpAffine(lona, m, (largura_img, altura), flags=cv2.INTER_LINEAR)
    alfa = cv2.warpAffine(np.ones((h, w), np.float32), m, (largura_img, altura), flags=cv2.INTER_LINEAR)[..., None]
    return np.clip(img * (1 - alfa) + saco[..., None] * alfa, 0, 255).astype(np.uint8)


def m5(img: np.ndarray, **relevo):
    """O relatório do M5 (só o M2 e o M5, como o Verificar rugas da BIapp)."""
    r = Mestre(config_teste(relevo=relevo), apenas=["relevo"]).inspecionar(img, guardar_intermedios=True)
    return r.relatorio("relevo")


def angulo_reta(graus: float) -> float:
    """Uma reta não tem sentido: entre -90 e 90 graus."""
    return (graus + 90) % 180 - 90


def mesmo_angulo(a: float, b: float, tolerancia: float = 4.0) -> bool:
    d = abs(angulo_reta(a) - angulo_reta(b))
    return min(d, 180 - d) <= tolerancia


def ensinar(pasta, img: np.ndarray, nome: str) -> None:
    """Como o "É OK: ensinar" da BIapp: o recorte do M2 vai para a referência."""
    referencia.guardar_recorte(Localizador(config_teste()).localizar(img), pasta, nome)


# --- Sem impressão ---------------------------------------------------------------------------

def test_saco_liso_nao_tem_rugas():
    r = m5(cena(np.random.default_rng(1)))
    assert r.estado is Estado.OK, r.mensagem
    assert r.medidas["rugas"] == 0
    assert "sem referência" in r.mensagem, "avisa que a impressão não foi descontada"
    assert {"rugas", "candidatos", "impressao"} <= set(r.intermedios)


def test_vinco_diagonal_e_uma_ruga_com_posicao_e_angulo():
    # de (20, 10) a (110, 80) mm: 38 graus face ao comprimento, 114 mm, centro a (-10, 0) mm do centro do saco
    r = m5(cena(np.random.default_rng(2), vincos=[(20, 10, 110, 80, 0.15)]))
    assert r.estado is Estado.NOK and len(r.defeitos) == 1, r.resumo
    d = r.defeitos[0]
    assert d.tipo is TipoDefeito.RUGAS
    assert mesmo_angulo(d.angulo_graus, math.degrees(math.atan2(70, 90))), d.angulo_graus
    assert 60 <= d.comprimento_mm <= 120 and d.severidade >= 1
    assert d.contorno is not None and d.contorno.shape == (4, 2)
    # o recorte pode vir rodado 180 graus: o centro fica a (10, 0) ou a (-10, 0) mm
    assert abs(abs(d.x_mm) - 10) < 8 and abs(d.y_mm) < 8, (d.x_mm, d.y_mm)


def test_ruga_aparece_na_imagem_anotada_com_o_contorno():
    rng = np.random.default_rng(3)
    resultado = Mestre(config_teste(), apenas=["relevo"]).inspecionar(cena(rng, vincos=[(20, 10, 110, 80, 0.15)]))
    assert resultado.estado is Estado.NOK
    assert [d.modulo for d in resultado.defeitos] == ["relevo"]


def test_saco_queimado_nao_se_verifica():
    r = m5(cena(np.random.default_rng(4), queimar=0.85))
    assert r.estado is Estado.ERRO and "queimado" in r.mensagem, r.mensagem


# --- Impressão e referência --------------------------------------------------------------------

VINCO = (10, 85, 145, 25, 0.15)  # passa por baixo do bloco de texto e cruza o traço impresso


def test_impressao_sem_referencia_passa_por_ruga_e_com_referencia_nao(tmp_path):
    rng = np.random.default_rng(5)
    saco = cena(rng, angulo=12.0, impressao=True)
    sem = m5(saco)
    assert sem.estado is Estado.NOK, "o traço claro impresso parece uma ruga"
    assert "sem referência" in sem.mensagem

    pasta = tmp_path / "referência ç"  # com acentos, como as pastas do utilizador
    ensinar(pasta, cena(rng, angulo=-8.0, desvio=(30, -20), impressao=True), "ok_1")
    com = m5(saco, referencia_pasta=str(pasta))
    assert com.estado is Estado.OK, com.resumo
    assert com.medidas["referencia_sacos"] == 1 and com.medidas["referencia_alinhamento"] > 0.5
    assert com.mensagem == ""

    vincado = m5(cena(rng, angulo=12.0, impressao=True, vincos=[VINCO]), referencia_pasta=str(pasta))
    assert vincado.estado is Estado.NOK and len(vincado.defeitos) == 1, vincado.resumo
    assert mesmo_angulo(vincado.defeitos[0].angulo_graus, math.degrees(math.atan2(-60, 135)))


def test_texto_do_proprio_saco_nao_e_ruga_mesmo_sem_referencia():
    """Traços escuros curtos (texto): o M5 desconta a tinta do próprio saco."""
    assert m5(cena(np.random.default_rng(12), texto=True)).estado is Estado.OK


def test_vinco_forte_escuro_como_tinta_e_ruga():
    """Um vinco forte na folha metalizada é escuro e fino como a tinta; o texto nunca é tão comprido."""
    r = m5(cena(np.random.default_rng(14), texto=True, vincos=[(20, 10, 130, 80, 0.5)]))
    assert r.estado is Estado.NOK and len(r.defeitos) == 1, r.resumo
    assert mesmo_angulo(r.defeitos[0].angulo_graus, math.degrees(math.atan2(70, 110))), r.resumo


def test_traco_escuro_impresso_comprido_so_sai_com_referencia(tmp_path):
    """Uma linha impressa escura, comprida e fina parece um vinco forte: sem referência passa por ruga;
    com os sacos OK ensinados, repete-se neles e é impressão."""
    rng = np.random.default_rng(15)
    saco = cena(rng, traco_escuro=True)
    assert m5(saco).estado is Estado.NOK
    pasta = tmp_path / "ref"
    ensinar(pasta, cena(rng, angulo=-8.0, desvio=(30, -20), traco_escuro=True), "ok_1")
    com = m5(saco, referencia_pasta=str(pasta))
    assert com.estado is Estado.OK, com.resumo


def test_vinco_de_lado_a_lado_sem_referencia():
    """Perpendicular ao comprimento do saco (90 graus no recorte), sem nenhum saco ensinado."""
    r = m5(cena(np.random.default_rng(13), vincos=[(75, 4, 75, 86, 0.15)]))
    assert r.estado is Estado.NOK and len(r.defeitos) == 1 and mesmo_angulo(r.defeitos[0].angulo_graus, 90), r.resumo


def test_com_referencia_tambem_se_veem_rugas_de_lado_a_lado(tmp_path):
    rng = np.random.default_rng(6)
    ensinar(tmp_path, cena(rng, angulo=-5.0, impressao=True), "ok_1")
    r = m5(cena(rng, angulo=10.0, impressao=True, vincos=[(120, 4, 120, 86, 0.15)]), referencia_pasta=str(tmp_path))
    assert r.estado is Estado.NOK and mesmo_angulo(r.defeitos[0].angulo_graus, 90), r.resumo


def test_referencia_de_outro_produto_nao_serve(tmp_path):
    rng = np.random.default_rng(7)
    ensinar(tmp_path, cena(rng, comprimento=110, largura=70, impressao=True), "outro_produto")
    r = m5(cena(rng, impressao=True), referencia_pasta=str(tmp_path))
    assert r.estado is Estado.NOK and "nenhum saco OK da referência tem o tamanho deste" in r.mensagem


def test_referencia_agrupa_por_produto_e_le_de_novo_quando_muda(tmp_path):
    rng = np.random.default_rng(8)
    p = escravo.parametros(config_teste().relevo)
    ensinar(tmp_path, cena(rng, angulo=5, impressao=True), "a1")
    ensinar(tmp_path, cena(rng, angulo=-7, desvio=(20, 10), impressao=True), "a2")
    ensinar(tmp_path, cena(rng, comprimento=110, largura=70, impressao=True), "b1")
    grupos = referencia.carregar(str(tmp_path), 3.0, PPM, p)
    assert sorted(g.nomes for g in grupos) == [("a1.png", "a2.png"), ("b1.png",)]
    assert referencia.carregar(str(tmp_path), 3.0, PPM, p) is grupos, "lida uma vez enquanto não muda"

    ensinar(tmp_path, cena(rng, angulo=9, impressao=True), "a3")
    grupos = referencia.carregar(str(tmp_path), 3.0, PPM, p)
    assert sorted(len(g.nomes) for g in grupos) == [1, 3]
    assert [f.name for f in referencia.recortes(tmp_path)] == ["a1.png", "a2.png", "a3.png", "b1.png"]
    assert referencia.carregar("", 3.0, PPM, p) == ()


def test_recorte_sem_a_zona_util_e_ignorado(tmp_path):
    rng = np.random.default_rng(9)
    ensinar(tmp_path, cena(rng, impressao=True), "bom")
    ensinar(tmp_path, cena(rng, impressao=True), "sem_zona")
    (tmp_path / "sem_zona_util.png").unlink()
    grupos = referencia.carregar(str(tmp_path), 3.0, PPM, escravo.parametros(config_teste().relevo))
    assert [g.nomes for g in grupos] == [("bom.png",)]


# --- Detetor de retas ----------------------------------------------------------------------------

@pytest.mark.parametrize("ppm", [5.0, 10.0])  # 10 px/mm: a procura faz-se numa grelha reduzida
def test_retas_acha_a_reta_tracejada_entre_pontos_soltos(ppm):
    rng = np.random.default_rng(10)
    h, w = int(100 * ppm), int(150 * ppm)
    pontos = rng.random((h, w)) < 0.025 / ppm ** 2  # 0,025 pontos soltos por mm² (com muitos mais, a reta continua pelo ruído)
    a, b = np.array([20 * ppm, 80 * ppm]), np.array([120 * ppm, 20 * ppm])  # 117 mm a -31 graus
    for t in np.linspace(0, 1, 3000):
        if (t * 40) % 1 < 0.7:  # tracejado: 70 % do traço
            x, y = np.round(a + t * (b - a)).astype(int)
            pontos[y, x] = True
    achadas = linhas.retas(pontos, np.zeros_like(pontos), ppm, linhas.PARAMETROS)
    assert len(achadas) == 1, [(round(c), round(pr), ang) for c, pr, ang, _, _ in achadas]
    comprimento, prova, _, p1, p2 = achadas[0]
    assert 100 <= comprimento <= 125 and prova >= linhas.PARAMETROS["prova_min_mm"]
    extremos = sorted([tuple(p1[:2]), tuple(p2[:2])])
    for medido, certo in zip(extremos, sorted([tuple(a), tuple(b)])):
        assert np.hypot(*(np.array(medido) - certo)) < 6 * ppm, (medido, certo)


def test_retas_atravessa_o_que_esta_escondido_sem_contar_como_prova():
    ppm = 5.0
    pontos = np.zeros((500, 750), bool)
    escondido = np.zeros_like(pontos)
    escondido[:, 300:450] = True  # 30 mm tapados (impressão, queimado) a meio da reta
    pontos[250, 100:300] = pontos[250, 450:650] = True  # 40 + 40 mm à vista, 110 mm ao todo
    p = {**linhas.PARAMETROS, "alinhado_graus": 0.0}
    [(comprimento, prova, _, _, _)] = linhas.retas(pontos, escondido, ppm, p)
    assert comprimento > 100 and 70 <= prova <= 90
    assert linhas.retas(pontos, np.zeros_like(pontos), ppm, p) == [] or \
        all(c < 100 for c, *_ in linhas.retas(pontos, np.zeros_like(pontos), ppm, p)), "sem o tapado, a falha corta-a"


def test_tempo_do_m5_num_saco():
    img = cena(np.random.default_rng(11), impressao=True, vincos=[VINCO])
    Mestre(config_teste(), apenas=["relevo"]).inspecionar(img)  # aquece (OpenCV, caches)
    inicio = time.perf_counter()
    m5(img)
    assert time.perf_counter() - inicio < 5.0, "o Verificar rugas tem de responder em segundos"


def test_selagem_junto_a_borda_nao_e_ruga_mas_o_mesmo_vinco_longe_dela_e():
    """Uma linha paralela ao comprimento, encostada à borda da zona inspecionada (a selagem, a aresta de um
    painel impresso), não é ruga; a mesma linha a meio do saco é."""
    rng = np.random.default_rng(16)
    assert m5(cena(rng, vincos=[(10, 7.5, 140, 7.5, 0.3)])).estado is Estado.OK
    r = m5(cena(rng, vincos=[(10, 30, 140, 30, 0.3)]))
    assert r.estado is Estado.NOK and mesmo_angulo(r.defeitos[0].angulo_graus, 0), r.resumo
