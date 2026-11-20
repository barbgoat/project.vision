"""M5 Relevo: rugas em sacos sintéticos, a referência da impressão (sacos OK ensinados) e o detetor de retas."""

from __future__ import annotations

import json
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
C_MM, L_MM = 150.0, 90.0  # o saco destes testes (abaixo de LOCALIZACAO_LARGURA_MAX_MM)


def cena(rng: np.random.Generator, angulo: float = 12.0, desvio: tuple[float, float] = (0.0, 0.0),
         impressao: bool = False, vincos=(), cor: float = 200.0, comprimento: float = C_MM,
         largura: float = L_MM, queimar: float = 0.0, traco_escuro: bool = False, texto: bool = False,
         dobras=(), circulos: bool = False) -> np.ndarray:
    """Um saco cinzento numa imagem da montagem, com "impressão" e vincos desenhados no referencial do
    saco. vincos: (x0, y0, x1, y1, contraste), em mm a partir do canto do saco (x ao longo do comprimento).

    A impressão: um bloco de texto (traços curtos e muito escuros: tinta) e um traço claro impresso,
    comprido, em diagonal: não é tinta e, sem a referência, passa por ruga. traco_escuro: o mesmo traço,
    mas escuro (comprido e fino como um vinco forte: também só sai com a referência). texto: só o bloco de
    texto. queimar: a fração do comprimento e da largura, ao
    centro, saturada (um reflexo forte; nas bordas, o M2 recusava o saco). dobras: como os vincos, mas
    largas e suaves (um vale com sigma de 1,5 mm: a dobra de um saco mate). circulos: outra impressão
    (outro produto): círculos escuros."""
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
    if circulos:
        for y in range(70, 420, 45):
            for x in range(90, 720, 50):
                cv2.circle(lona, (x, y), 13, cor * 0.4, 2)
    for x0, y0, x1, y1, contraste in vincos:
        fio = np.zeros((h, w), np.float32)
        cv2.line(fio, (round(x0 * PPM), round(y0 * PPM)), (round(x1 * PPM), round(y1 * PPM)), 1.0, 2, cv2.LINE_AA)
        lona *= 1 - contraste * cv2.GaussianBlur(fio, (0, 0), 0.8)
    for x0, y0, x1, y1, contraste in dobras:
        fio = np.zeros((h, w), np.float32)
        cv2.line(fio, (round(x0 * PPM), round(y0 * PPM)), (round(x1 * PPM), round(y1 * PPM)), 1.0, 1, cv2.LINE_AA)
        vale = cv2.GaussianBlur(fio, (0, 0), 1.5 * PPM)
        lona *= 1 - contraste * vale / max(float(vale.max()), 1e-6)
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
    """Outra impressão não alinha (nem com o mesmo tamanho): a impressão deste fica por descontar."""
    rng = np.random.default_rng(7)
    ensinar(tmp_path, cena(rng, comprimento=110, largura=70, circulos=True), "outro_produto")
    ensinar(tmp_path, cena(rng, circulos=True), "outro_do_mesmo_tamanho")
    r = m5(cena(rng, impressao=True), referencia_pasta=str(tmp_path))
    assert r.estado is Estado.NOK and "nenhum saco OK da referência se parece com este" in r.mensagem, r.mensagem
    assert r.medidas["referencia_sacos"] == 0


def test_referencia_usa_os_sacos_do_produto_e_le_de_novo_quando_muda(tmp_path):
    rng = np.random.default_rng(8)
    p = escravo.parametros(config_teste().relevo)
    ensinar(tmp_path, cena(rng, angulo=5, impressao=True), "a1")
    ensinar(tmp_path, cena(rng, angulo=-7, desvio=(20, 10), impressao=True), "a2")
    ensinar(tmp_path, cena(rng, comprimento=110, largura=70, circulos=True), "b1")
    ref = referencia.carregar(str(tmp_path), 3.0, PPM, p)
    assert [v.nome for v in ref.vistas] == ["a1.png", "a2.png", "b1.png"]
    assert referencia.carregar(str(tmp_path), 3.0, PPM, p) is ref, "lida uma vez enquanto não muda"
    r = m5(cena(rng, impressao=True), referencia_pasta=str(tmp_path))
    assert r.estado is Estado.OK and r.medidas["referencia_sacos"] == 2, "só os sacos OK do mesmo produto"
    assert r.medidas["referencia_cobertura_pct"] > 95

    ensinar(tmp_path, cena(rng, angulo=9, impressao=True), "a3")
    ref = referencia.carregar(str(tmp_path), 3.0, PPM, p)
    assert ref.n == 4
    assert [f.name for f in referencia.recortes(tmp_path)] == ["a1.png", "a2.png", "a3.png", "b1.png"]
    assert referencia.carregar("", 3.0, PPM, p) is None


def test_recorte_sem_a_zona_util_e_ignorado(tmp_path):
    rng = np.random.default_rng(9)
    ensinar(tmp_path, cena(rng, impressao=True), "bom")
    ensinar(tmp_path, cena(rng, impressao=True), "sem_zona")
    (tmp_path / "sem_zona_util.png").unlink()
    ref = referencia.carregar(str(tmp_path), 3.0, PPM, escravo.parametros(config_teste().relevo))
    assert [v.nome for v in ref.vistas] == ["bom.png"]


def test_vistas_ficam_preparadas_para_arrancar_depressa(tmp_path, monkeypatch):
    """Preparar uma vista leva ~1,5 s numa imagem toda: fica em _vistas/<nome>.npz e, ao arrancar, lê-se de lá.
    Com outros parâmetros do M5 (outra vista) prepara-se de novo."""
    rng = np.random.default_rng(62)
    ensinar(tmp_path, cena(rng, impressao=True), "a1")
    p = escravo.parametros(config_teste().relevo)
    ref = referencia.carregar(str(tmp_path), 3.0, PPM, p)
    assert (tmp_path / referencia.PASTA_VISTAS / "a1.npz").is_file()
    referencia._referencia.cache_clear()  # como ao arrancar a BIapp
    referencia._vista.cache_clear()
    with monkeypatch.context() as m:
        m.setattr(linhas, "preparar", lambda *a, **k: pytest.fail("devia ler a vista guardada"))
        lida = referencia.carregar(str(tmp_path), 3.0, PPM, p).vistas[0]
    original = ref.vistas[0]
    assert lida.nome == "a1.png" and lida.forma == original.forma
    for campo in ("marcas", "visivel", "pontos", "descritores", "pequena", "visivel_pequena"):
        assert np.array_equal(getattr(lida, campo), getattr(original, campo)), campo
    outra = referencia.carregar(str(tmp_path), 3.0, PPM, {**p, "impressao_folga_mm": 2.5}).vistas[0]
    assert outra.mascara(outra.marcas).sum() > original.mascara(original.marcas).sum(), "mais folga, mais marcas"


TRACO_IMPRESSO_GRAUS = math.degrees(math.atan2(340, 270))  # o traço claro impresso de cena(impressao=True)


def test_nao_e_ruga_tira_essa_linha_neste_saco_e_noutro_do_mesmo_produto(tmp_path):
    """O administrador diz que o traço impresso não é ruga: deixa de o ser neste saco (o vinco verdadeiro,
    que o cruza, fica) e num outro saco do mesmo produto noutra posição. Não é um saco OK."""
    rng = np.random.default_rng(31)
    img = cena(rng, angulo=12.0, impressao=True, vincos=[VINCO])
    r = Mestre(config_teste(), apenas=["relevo"]).inspecionar(img, guardar_intermedios=True)
    sem = r.relatorio("relevo")
    assert len(sem.defeitos) == 2, sem.resumo
    impresso = [d for d in sem.defeitos if mesmo_angulo(d.angulo_graus, TRACO_IMPRESSO_GRAUS)]
    assert len(impresso) == 1, sem.resumo
    caminho = referencia.guardar_nao_rugas(r.saco, tmp_path, impresso, PPM)
    assert caminho.parent == tmp_path / referencia.PASTA_NAO_RUGAS
    assert caminho.with_suffix(".png").is_file() and (caminho.parent / f"{caminho.stem}_util.png").is_file()
    assert referencia.recortes(tmp_path) == [], "não é um saco OK"

    com = m5(img, referencia_pasta=str(tmp_path))
    assert com.estado is Estado.NOK and len(com.defeitos) == 1, com.resumo
    assert mesmo_angulo(com.defeitos[0].angulo_graus, math.degrees(math.atan2(-60, 135))), "fica o vinco"
    assert com.medidas["referencia_nao_rugas"] == 1 and com.medidas["referencia_sacos"] == 0
    assert com.medidas["rugas_nao_ruga"] == 1, "o traço impresso foi achado, mas não contou"
    assert "não tem sacos OK" in com.mensagem
    assert "impressao" in com.intermedios

    outro = cena(rng, angulo=-8.0, desvio=(30, -20), impressao=True)
    assert m5(outro).estado is Estado.NOK
    assert m5(outro, referencia_pasta=str(tmp_path)).estado is Estado.OK, "a mesma linha impressa noutro saco"
    diferente = m5(cena(rng, circulos=True, vincos=[(75, 4, 75, 86, 0.15)]), referencia_pasta=str(tmp_path))
    assert diferente.estado is Estado.NOK and diferente.medidas["referencia_nao_rugas"] == 0, "outro produto: não serve"


def test_nao_e_ruga_na_mesma_imagem_junta_as_linhas(tmp_path):
    """Marcar mais linhas na mesma imagem acrescenta-as à mesma entrada (uma vista só); e a vista refaz-se."""
    rng = np.random.default_rng(32)
    img = cena(rng, impressao=True, vincos=[VINCO])
    r = Mestre(config_teste(), apenas=["relevo"]).inspecionar(img, guardar_intermedios=True)
    d1, d2 = r.relatorio("relevo").defeitos
    p = escravo.parametros(config_teste().relevo)
    referencia.guardar_nao_rugas(r.saco, tmp_path, [d1], PPM)
    uma = referencia.carregar(str(tmp_path), 3.0, PPM, p)
    caminho = referencia.guardar_nao_rugas(r.saco, tmp_path, [d2], PPM)
    assert referencia.nao_rugas(tmp_path) == [caminho]
    assert len(json.loads(caminho.read_text(encoding="utf-8"))["linhas"]) == 2
    duas = referencia.carregar(str(tmp_path), 3.0, PPM, p)
    assert duas.n == 0 and duas.nao_rugas == 1 and duas.vistas[0].nao_ruga
    mais = duas.vistas[0].mascara(duas.vistas[0].marcas).sum()
    assert mais > uma.vistas[0].mascara(uma.vistas[0].marcas).sum(), "a vista leva as duas linhas"
    assert m5(img, referencia_pasta=str(tmp_path)).estado is Estado.OK

    referencia._referencia.cache_clear()  # ao arrancar, a vista "não é ruga" lê-se do .npz
    referencia._vista.cache_clear()
    lida = referencia.carregar(str(tmp_path), 3.0, PPM, p).vistas[0]
    assert lida.nao_ruga and np.array_equal(lida.marcas, duas.vistas[0].marcas)


def test_segmento_px_volta_as_pontas_da_ruga():
    """As pontas que o "não é ruga" guarda são as da reta que o M5 achou (no recorte do saco)."""
    rng = np.random.default_rng(33)
    r = Mestre(config_teste(), apenas=["relevo"]).inspecionar(cena(rng, vincos=[(75, 4, 75, 86, 0.15)]),
                                                              guardar_intermedios=True)
    d = r.relatorio("relevo").defeitos[0]
    p1, p2 = referencia.segmento_px(d, r.saco, PPM)
    assert np.hypot(*(p2 - p1)) / PPM == pytest.approx(d.comprimento_mm, abs=0.1)
    assert r.saco.posicao_mm(*((p1 + p2) / 2), PPM) == pytest.approx((d.x_mm, d.y_mm), abs=0.05)
    faixa = linhas.faixas_nao_ruga(r.saco.mascara.shape, [(p1, p2)], PPM, linhas.PARAMETROS)
    assert faixa[tuple(np.round((p1 + p2) / 2).astype(int)[::-1])], "a faixa passa pelo meio da ruga"


def test_alinhar_pela_correlacao_tambem_rodado_180_graus():
    """Sem pontos de interesse, dois recortes do mesmo tamanho alinham-se pela correlação (e o ECC): a afim
    leva cada ponto da vista ao mesmo sítio do saco, também com o saco rodado 180 graus."""
    rng = np.random.default_rng(61)
    h, w = 400, 700
    manchas = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 20)
    cinza = (150 + 50 * manchas / np.abs(manchas).max()).astype(np.float32)
    util = np.ones((h, w), bool)
    p = linhas.PARAMETROS
    v = linhas.vista(linhas.preparar(cinza, util, PPM, p), p)
    for rodado, esperado in ((False, lambda x, y: (x, y)), (True, lambda x, y: (w - 1 - x, h - 1 - y))):
        alvo = np.ascontiguousarray(cinza[::-1, ::-1]) if rodado else cinza
        matriz = linhas._por_correlacao(v, linhas.preparar(alvo, util, PPM, p))
        for x, y in ((100.0, 50.0), (600.0, 350.0), (350.0, 200.0)):
            assert np.allclose(matriz @ [x, y, 1.0], esperado(x, y), atol=2.0), (rodado, x, y, matriz)


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


# --- Dobras largas e comparação com o resto do saco ----------------------------------------------

def test_dobra_larga_e_suave_e_uma_ruga():
    """Num saco mate as rugas são dobras largas (3–6 mm) e suaves (5–15 %): as linhas finas não as veem."""
    rng = np.random.default_rng(30)
    r = m5(cena(rng, dobras=[(15, 30, 135, 45, 0.08)]))
    assert r.estado is Estado.NOK and len(r.defeitos) == 1, r.resumo
    assert mesmo_angulo(r.defeitos[0].angulo_graus, math.degrees(math.atan2(15, 120))), r.defeitos[0].angulo_graus
    assert m5(cena(rng, dobras=[(15, 30, 135, 45, 0.02)])).estado is Estado.OK, "2 % não é dobra"


def test_dobras_nao_veem_o_texto_nem_o_traco_impresso_desfocados():
    r = m5(cena(np.random.default_rng(31), texto=True))
    assert r.estado is Estado.OK, r.resumo


def achado(p1, p2) -> linhas.Achado:
    p1, p2 = np.array(p1, float), np.array(p2, float)
    return linhas.Achado(float(np.hypot(*(p2 - p1))) / PPM, 0.0, 0.0, p1, p2)


def test_comparar_com_o_resto_do_saco():
    """Uma reta com prova num saco liso destaca-se do resto do saco; a mesma reta numa textura (traços
    soltos por todo o saco, como os reflexos da folha metalizada) não."""
    p = linhas.PARAMETROS
    h, w = 450, 750
    util, escondido = np.ones((h, w), bool), np.zeros((h, w), bool)
    a = achado((100, 200), (600, 200))
    liso = np.zeros((h, w), bool)
    cv2.line(liso.view(np.uint8), (100, 200), (600, 200), 1, 1)
    assert linhas.desvio_no_saco(a, liso, None, escondido, util, PPM, p) >= p["saco_desvio_min"]

    rng = np.random.default_rng(32)
    textura = np.zeros((h, w), np.uint8)
    for _ in range(6000):  # traços horizontais de 3–8 mm por todo o lado
        x, y, c = int(rng.integers(0, w)), int(rng.integers(0, h)), int(rng.integers(15, 40))
        cv2.line(textura, (x, y), (x + c, y), 1, 1)
    cv2.line(textura, (100, 200), (600, 200), 1, 1)
    assert linhas.desvio_no_saco(a, textura > 0, None, escondido, util, PPM, p) < p["saco_desvio_min"]


def test_paralelas_escondidas_nao_contam():
    """Com o saco quase todo escondido (queimado, tinta) não há com que comparar: a ruga fica."""
    p = linhas.PARAMETROS
    h, w = 450, 750
    pontos = np.zeros((h, w), bool)
    cv2.line(pontos.view(np.uint8), (100, 200), (600, 200), 1, 1)
    escondido = np.ones((h, w), bool)
    escondido[195:206] = False
    assert linhas.desvio_no_saco(achado((100, 200), (600, 200)), pontos, None, escondido, np.ones((h, w), bool),
                                 PPM, p) == math.inf


def test_escala_numpy_nao_rebenta():
    """Na inspeção a escala pode chegar como np.float64 (calibração): os mapas do remap têm de ser float32."""
    rng = np.random.default_rng(44)
    cinza = rng.uniform(50, 200, (300, 400)).astype(np.float32)
    prep = linhas.preparar(cinza, np.ones(cinza.shape, bool), np.float64(PPM), linhas.PARAMETROS)
    assert prep.linhas.shape == cinza.shape


def test_zona_a_volta_e_textura_desligadas_por_omissao():
    """Medido a 19/11: perdiam rugas reais sem tirar as falsas do Dolmio. O código fica, desligado."""
    p = linhas.PARAMETROS
    assert p["saco_local_mm"] == 0 and p["textura_coerencia_max"] == 0
    rng = np.random.default_rng(63)
    pontos = rng.random((200, 300)) < 0.3
    orientacao = rng.uniform(0, 180, (200, 300)).astype(np.float32)
    assert not linhas.textura(pontos, orientacao, np.ones((200, 300), bool), PPM, p).any()
    r = m5(cena(rng, vincos=[(20, 10, 110, 80, 0.15)]))
    assert r.estado is Estado.NOK and "zona à volta" not in r.defeitos[0].descricao
    assert "textura_pct" not in r.medidas


def test_textura_e_direcoes_misturadas_e_um_feixe_de_dobras_nao():
    """Textura (ligada): muitos pontos de linha em direções misturadas (fotografia, letras). Um feixe de dobras
    paralelas, mesmo denso, tem uma direção só: não é textura."""
    p = {**linhas.PARAMETROS, "textura_coerencia_max": 0.5}
    h, w = 300, 600
    pontos, orientacao = np.zeros((h, w), np.uint8), np.zeros((h, w), np.float32)
    rng = np.random.default_rng(47)
    for _ in range(1500):  # à esquerda: traços curtos em todas as direções
        x, y, a = int(rng.integers(0, 280)), int(rng.integers(0, h)), float(rng.uniform(0, 180))
        d = np.array([np.cos(np.radians(a)), np.sin(np.radians(a))]) * 8
        cv2.line(pontos, (x, y), (int(x + d[0]), int(y + d[1])), 1, 1)
        cv2.line(orientacao, (x, y), (int(x + d[0]), int(y + d[1])), a, 1)
    for y in range(10, h, 6):  # à direita: dobras paralelas a 1,2 mm umas das outras
        cv2.line(pontos, (320, y), (w - 1, y), 1, 1)
        cv2.line(orientacao, (320, y), (w - 1, y), 0.0, 1)
    t = linhas.textura(pontos > 0, orientacao, np.ones((h, w), bool), PPM, p)
    assert t[:, 40:240].mean() > 0.7 and t[:, 360:].mean() < 0.05


def test_comparar_com_a_zona_a_volta():
    """(Ligada) uma reta numa zona impressa de um saco meio liso destaca-se do saco todo, mas não da zona à volta."""
    p = {**linhas.PARAMETROS, "saco_local_mm": 15.0}
    h, w = 500, 750
    pontos = np.zeros((h, w), np.uint8)
    rng = np.random.default_rng(48)
    for _ in range(1500):  # impressão miúda só na faixa de cima (y < 150)
        x, y, c = int(rng.integers(0, w)), int(rng.integers(0, 150)), int(rng.integers(15, 40))
        cv2.line(pontos, (x, y), (x + c, y), 1, 1)
    cv2.line(pontos, (100, 75), (600, 75), 1, 1)
    a = achado((100, 75), (600, 75))
    sem, util = np.zeros((h, w), bool), np.ones((h, w), bool)
    assert linhas.desvio_no_saco(a, pontos > 0, None, sem, util, PPM, p) >= p["saco_desvio_min"]
    local = linhas.desvio_no_saco(a, pontos > 0, None, sem, util, PPM, p, p["saco_local_mm"])
    assert local < p["saco_local_desvio_min"], local


# --- Saco maior do que o campo: a imagem toda, ensinada noutras posições ---------------------------

def impresso_grande(rng: np.random.Generator) -> np.ndarray:
    """Um saco muito impresso, maior do que o campo (320 × 230 mm): letras, molduras e traços claros
    compridos (como o rebordo de uma taça: sem referência, passam por ruga)."""
    w, h = int(320 * PPM), int(230 * PPM)
    lona = np.full((h, w), 185, np.uint8)  # o putText só desenha em 8 bits
    letras = list("ABCDEFGHJKLMNPRSTUVWXYZ0123456789")
    for _ in range(110):
        x, y = int(rng.integers(0, w - 150)), int(rng.integers(25, h - 5))
        cv2.putText(lona, "".join(rng.choice(letras, 5)), (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                    float(rng.uniform(0.5, 1.2)), int(rng.integers(30, 110)), 2)
    for _ in range(35):
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        cv2.rectangle(lona, (x, y), (x + int(rng.integers(30, 120)), y + int(rng.integers(20, 90))),
                      int(rng.integers(60, 160)), 3)
    for x0, y0, x1, y1 in ((120, 160, 900, 620), (1450, 150, 950, 1050), (250, 1000, 1400, 820)):
        cv2.line(lona, (x0, y0), (x1, y1), 235, 2)
    return lona.astype(np.float32)


def foto_do_campo(lona: np.ndarray, centro_mm: tuple[float, float], angulo: float, rng: np.random.Generator,
                  vincos=()) -> np.ndarray:
    """O que a câmara vê (180 × 136 mm) com o centro do campo em centro_mm do saco e o saco rodado."""
    lona = lona.copy()
    for x0, y0, x1, y1, contraste in vincos:  # em mm do saco
        fio = np.zeros(lona.shape, np.float32)
        cv2.line(fio, (round(x0 * PPM), round(y0 * PPM)), (round(x1 * PPM), round(y1 * PPM)), 1.0, 2, cv2.LINE_AA)
        lona *= 1 - contraste * cv2.GaussianBlur(fio, (0, 0), 0.8)
    lona += rng.normal(0, 1.5, lona.shape).astype(np.float32)
    largura, altura = 900, 680
    m = cv2.getRotationMatrix2D((centro_mm[0] * PPM, centro_mm[1] * PPM), angulo, 1.0)
    m[:, 2] += (largura / 2 - centro_mm[0] * PPM, altura / 2 - centro_mm[1] * PPM)
    img = cv2.warpAffine(lona, m, (largura, altura), flags=cv2.INTER_LINEAR, borderValue=185)
    return np.repeat(np.clip(img, 0, 255).astype(np.uint8)[..., None], 3, axis=2)


def saco_inteiro(img: np.ndarray, margem_mm: float = 5.0):
    """A imagem toda como recorte (como o Verificar rugas quando o saco não cabe no campo)."""
    from nucleo.contrato import Saco
    altura, largura = img.shape[:2]
    util = np.zeros((altura, largura), np.uint8)
    m = round(margem_mm * PPM)
    util[m:-m, m:-m] = 255
    contorno = np.array([[[0, 0]], [[largura - 1, 0]], [[largura - 1, altura - 1]], [[0, altura - 1]]], np.int32)
    return Saco(contorno, ((largura - 1) / 2, (altura - 1) / 2), 0.0, largura / PPM, altura / PPM,
                np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]), img, np.full((altura, largura), 255, np.uint8), util)


def m5_inteira(img: np.ndarray, **relevo):
    from nucleo.contrato import Captura
    return escravo.Relevo().inspecionar(saco_inteiro(img), Captura(img, "teste", 0.0), config_teste(relevo=relevo),
                                        guardar_intermedios=True)


def test_saco_maior_do_que_o_campo_ensinado_noutras_posicoes(tmp_path):
    """Cada foto vê outra parte do saco, deslocada e rodada (até 180 graus): os sacos OK ensinados alinham-se
    pelos pontos de interesse e a impressão sai da parte que cada um tem em comum com este."""
    rng = np.random.default_rng(64)
    lona = impresso_grande(rng)
    alvo = foto_do_campo(lona, (160, 115), 0.0, rng)
    sem = m5_inteira(alvo)
    assert sem.estado is Estado.NOK, "os traços claros impressos passam por ruga"

    for nome, centro, angulo in (("ok_1", (130, 100), 25.0), ("ok_2", (190, 125), -160.0)):
        referencia.guardar_recorte(saco_inteiro(foto_do_campo(lona, centro, angulo, rng)), tmp_path, nome)
    com = m5_inteira(alvo, referencia_pasta=str(tmp_path))
    assert com.estado is Estado.OK, com.resumo
    assert com.medidas["referencia_sacos"] == 2 and com.medidas["referencia_alinhamento"] > 0.8
    assert com.medidas["referencia_cobertura_pct"] > 85, "as duas juntas veem quase todo o campo"

    vinco = (95, 75, 215, 160, 0.15)  # 147 mm a 35 graus, no meio da impressão
    vincado = m5_inteira(foto_do_campo(lona, (160, 115), 0.0, rng, vincos=[vinco]), referencia_pasta=str(tmp_path))
    assert vincado.estado is Estado.NOK and len(vincado.defeitos) == 1, vincado.resumo
    assert mesmo_angulo(vincado.defeitos[0].angulo_graus, math.degrees(math.atan2(85, 120))), vincado.defeitos[0]


def test_saco_maior_do_que_o_campo_so_com_parte_ensinada_avisa(tmp_path):
    """Com um saco OK que só via um canto deste, a impressão desconta-se aí e a mensagem diz quanto falta."""
    rng = np.random.default_rng(65)
    lona = impresso_grande(rng)
    referencia.guardar_recorte(saco_inteiro(foto_do_campo(lona, (75, 70), 10.0, rng)), tmp_path, "canto")
    r = m5_inteira(foto_do_campo(lona, (160, 115), 0.0, rng), referencia_pasta=str(tmp_path))
    assert r.medidas["referencia_sacos"] == 1 and r.medidas["referencia_cobertura_pct"] < 60
    assert "só cobrem" in r.mensagem and "ensinar um saco OK nesta posição" in r.mensagem, r.mensagem


# --- Famílias de rugas paralelas -------------------------------------------------------------------

def test_desvio_nao_conta_as_paralelas_que_sao_rugas():
    """Uma família de rugas largas (faixas de ~2 mm, como na folha metalizada) a 5 mm: mais de metade das
    paralelas do saco passa por uma delas, e cada ruga, comparada com o resto do saco, tinha as outras na
    mediana e no MAD. Sem as paralelas a ±4 mm das rugas já detetadas destaca-se. (Com rugas finas a mediana
    já não as via: só ~1 paralela em 5 lhes toca.)"""
    p = linhas.PARAMETROS
    h, w = 450, 750
    util, escondido = np.ones((h, w), bool), np.zeros((h, w), bool)
    pontos = np.zeros((h, w), np.uint8)
    rugas = [achado((100, y), (600, y)) for y in range(25, 430, 25)]  # 17 rugas a 5 mm
    for a in rugas:
        cv2.line(pontos, tuple(int(v) for v in a.p1), tuple(int(v) for v in a.p2), 1, 9)
    pontos = pontos > 0
    a = rugas[6]
    sem = linhas.desvio_no_saco(a, pontos, None, escondido, util, PPM, p)
    com = linhas.desvio_no_saco(a, pontos, None, escondido, util, PPM, p, outras=rugas)
    assert sem < p["saco_desvio_min"] <= com, (sem, com)


def test_rugas_paralelas_a_6_mm_ficam_as_duas_e_sao_uma_familia():
    """Duas rugas paralelas a mais de 5 mm não são a mesma (nem uma tira o destaque à outra)."""
    r = m5(cena(np.random.default_rng(40), vincos=[(60, 4, 60, 86, 0.15), (66, 4, 66, 86, 0.15)]))
    assert r.estado is Estado.NOK and len(r.defeitos) == 2, r.resumo
    assert r.medidas["familias"] == 1 and r.medidas["familia_rugas_max"] == 2 and r.medidas["rugas_familia"] == 0
    assert "família de 2 rugas a" in r.mensagem and "(espaçamento 6 mm)" in r.mensagem, r.mensagem
    assert all("família 1" in d.descricao for d in r.defeitos)


def test_procura_da_familia_acha_paralelas_mais_curtas():
    """À volta de uma ruga forte, paralelas curtas (30 mm, abaixo de ruga_min_mm) acham-se com os limites da
    família; numa diagonal também."""
    r = m5(cena(np.random.default_rng(40), vincos=[(60, 4, 60, 86, 0.15), (75, 30, 75, 62, 0.12),
                                                   (48, 20, 48, 50, 0.12)]))
    assert len(r.defeitos) == 3 and r.medidas["rugas_familia"] == 2, r.resumo
    assert "família de 3 rugas" in r.mensagem and "espaçamento 13–15 mm" in r.mensagem, r.mensagem
    assert sum("achada pela família" in d.descricao for d in r.defeitos) == 2
    assert {"rugas", "candidatos"} <= set(r.intermedios)

    diagonal = m5(cena(np.random.default_rng(40), vincos=[(20, 10, 110, 80, 0.15), (40, 10, 70, 33, 0.12)]))
    assert len(diagonal.defeitos) == 2 and diagonal.medidas["rugas_familia"] == 1, diagonal.resumo
    curta = m5(cena(np.random.default_rng(40), vincos=[(75, 30, 75, 62, 0.12)]))
    assert curta.estado is Estado.OK, "sozinha, uma linha de 32 mm não é ruga"
    sem_familia = m5(cena(np.random.default_rng(40), vincos=[(60, 4, 60, 86, 0.15), (75, 30, 75, 62, 0.12)]),
                     ruga_min_mm_familia=40.0)
    assert len(sem_familia.defeitos) == 1, "com os limites da família como os normais, não se junta"


def test_paralelas_regulares_sao_textura_e_nao_se_juntam():
    """Muitas linhas paralelas com espaçamento regular (reflexos, estrias da folha metalizada): textura."""
    vincos = [(60, 4, 60, 86, 0.15)] + [(70 + 7 * i, 30, 70 + 7 * i, 60, 0.12) for i in range(8)]
    r = m5(cena(np.random.default_rng(40), vincos=vincos))
    assert len(r.defeitos) == 1 and r.medidas["rugas_familia"] == 0, r.resumo
    assert "família" not in r.mensagem


def test_familia_ou_textura_pelo_espacamento():
    p = linhas.PARAMETROS
    semente = achado((100, 0), (100, 400))

    def em(*xs):
        return [semente] + [achado((x, 0), (x, 400)) for x in xs]

    assert not linhas.e_textura(semente, em(150, 215), PPM, p), "3 rugas a 10 e 13 mm"
    assert not linhas.e_textura(semente, em(140, 210, 230), PPM, p), "irregular: 8, 14 e 4 mm... (juntas a <5 mm)"
    assert linhas.e_textura(semente, em(135, 170, 205), PPM, p), "4 a 7 mm certos: periódico"
    assert linhas.e_textura(semente, em(*range(140, 500, 50)), PPM, p), "mais de familia_linhas_max"
    f = linhas.Familia(1, [], -12.4, [8.2, 15.1])
    assert f.texto() == "família de 0 rugas a -12° (espaçamento 8–15 mm)"


def test_limites_da_familia_validados():
    from nucleo.config import ConfigRelevo
    assert ConfigRelevo().erros() == []
    assert ConfigRelevo(prova_min_mm_familia=30.0).erros(), "a prova não pode ser maior do que o comprimento"
    assert ConfigRelevo(desvio_min_familia=0.0).erros()
