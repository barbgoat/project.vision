"""Bancada das rugas: manifesto por saco físico, cenas de uma sessão, referência sem o próprio grupo."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from ferramentas import bancada_rugas as b
from nucleo.imagens import guardar_imagem
from testes.apoio import CORES, cena_saco, config_teste

MANIFESTO = "saco;grupo;sessao;passagens;rotulo;nota\n"


def _manifesto(tmp_path: Path, linhas: str) -> Path:
    f = tmp_path / "m.csv"
    f.write_text(MANIFESTO + linhas, encoding="utf-8")
    return f


# --- Manifesto ---------------------------------------------------------------------------------

def test_passagens_ida_e_volta():
    assert b.ler_passagens("1-5") == (1, 2, 3, 4, 5)
    assert b.ler_passagens("8") == (8,)
    assert b.ler_passagens("1,3,7-8") == (1, 3, 7, 8)
    assert b.texto_passagens((1, 2, 3, 7, 9, 10)) == "1-3,7,9-10"
    with pytest.raises(ValueError):
        b.ler_passagens("5-2")


def test_manifesto_grupo_por_omissao_e_o_saco(tmp_path):
    cenas = b.ler_manifesto(_manifesto(tmp_path, "r01;;S;1-3;rugas;\nok01;;S;4;OK;\n"))
    assert [(c.saco, c.grupo, c.passagem, c.rotulo) for c in cenas] == [("r01", "r01", 1, "rugas"), ("ok01", "ok01", 4, "ok")]


@pytest.mark.parametrize("linhas, erro", [
    ("r01;;S;1;amarrotado;\n", "rótulo"),
    ("r01;;S;1;rugas;\nr01;;S;2;ok;\n", "rótulos/grupos diferentes"),
    ("r01;;S;1-3;rugas;\nr02;;S;3;rugas;\n", "passagem 3"),
])
def test_manifesto_recusa_incoerencias(tmp_path, linhas, erro):
    with pytest.raises(ValueError, match=erro):
        b.ler_manifesto(_manifesto(tmp_path, linhas))


def test_agrupar_junta_so_passagens_seguidas_com_a_mesma_foto():
    rng = np.random.default_rng(0)
    a, c = rng.uniform(0, 255, (20, 30)).astype(np.float32), rng.uniform(0, 255, (20, 30)).astype(np.float32)
    seq = [(1, a), (2, a + 1), (3, c), (4, a), (5, a)]
    assert b.agrupar_passagens(seq) == [[1, 2], [3], [4, 5]]


# --- Sessões sintéticas ------------------------------------------------------------------------

def _sessao(resultados: Path, nome: str, fotos: list[np.ndarray]) -> None:
    for p, img in enumerate(fotos, start=1):
        guardar_imagem(resultados / nome / "originais" / "ok" / f"{p:06d}_cam_{p}_OK_original.png", img)


def _saco(seed: int, cor: str, angulo: float, ruga: bool) -> np.ndarray:
    img, (cx, cy) = cena_saco(np.random.default_rng(seed), cor=CORES[cor], angulo=angulo)
    if ruga:  # uma linha clara no meio do saco
        cv2.line(img, (int(cx - 60), int(cy - 30)), (int(cx + 60), int(cy + 30)), (180, 180, 180), 3)
    return img


class Simples:
    """Um detetor de brincar: NOK se houver um ponto claro na área útil; regista as referências."""

    nome, parametros = "simples", {"limiar": 150}

    def __init__(self):
        self.referencias: list[int] = []

    def preparar(self, r: b.Recorte):
        return float(r.cinza[r.util].max())

    def referencia(self, outros):
        self.referencias.append(len(outros))
        return len(outros)

    def detetar(self, preparado, referencia):
        return b.Deteccao(preparado > 150, preparado, imagens={"cinza": np.zeros((5, 5), np.float32)})


@pytest.fixture(scope="module")
def resultados(tmp_path_factory):
    raiz = tmp_path_factory.mktemp("resultados")
    # rugas: 2 sacos × 2 fotos; a 1.ª foto do 1.º saco repete-se (o saco não se mexeu)
    # (cores diferentes: as cenas sintéticas têm de diferir como as reais, > MESMA_FOTO)
    r1a = _saco(1, "vermelho", 20, True)
    _sessao(raiz, "SR", [r1a, r1a.copy(), _saco(2, "preto", 35, True), _saco(3, "verde", 28, True),
                         _saco(4, "preto", 120, True)])
    _sessao(raiz, "SO", [_saco(5, "azul", 15, False), _saco(6, "verde", 30, False)])
    return raiz


def test_propor_cenas_reparte_por_sacos(resultados):
    cenas, avisos = b.propor_cenas(resultados, "SR", "rugas", 2, "r")
    assert [(c.saco, c.passagens) for c in cenas] == [("r01", (1, 2)), ("r01", (3,)), ("r02", (4,)), ("r02", (5,))]
    assert avisos == []
    cenas, avisos = b.propor_cenas(resultados, "SR", "rugas", 3, "r", grupo="SR")
    assert {c.grupo for c in cenas} == {"SR"} and "não se repartem" in avisos[0]


def test_avaliar_por_saco_sem_o_proprio_grupo(resultados, tmp_path):
    cenas = b.propor_cenas(resultados, "SR", "rugas", 2, "r")[0] + b.propor_cenas(resultados, "SO", "ok", 1, "ok")[0]
    det = Simples()
    av = b.avaliar(cenas, det, config_teste(), resultados, tmp_path)
    assert all(a.estado in ("OK", "NOK") for a in av), [a.texto for a in av]
    assert [a.estado for a in av] == ["NOK"] * 4 + ["OK"] * 2
    # 4 grupos (r01, r02, ok01, ok02); a referência de cada um tem as cenas dos outros
    assert sorted(det.referencias) == [4, 4, 5, 5]
    sacos = {s["saco"]: s["veredito"] for s in b.por_saco(av)}
    assert sacos == {"r01": "apanhado", "r02": "apanhado", "ok01": "aceite", "ok02": "aceite"}
    texto = "\n".join(b.resumo(av))
    assert "rugas apanhadas: 2 de 2" in texto and "OK rejeitados:   0 de 2" in texto
    assert len(list((tmp_path / "imagens").rglob("*.jpg"))) == 6


def test_saco_ok_rejeitado_se_alguma_foto_o_for():
    c1, c2 = b.Cena("ok01", "ok01", "S", (1,), "ok"), b.Cena("ok01", "ok01", "S", (2,), "ok")
    r1, r2 = b.Cena("r01", "r01", "S", (3,), "rugas"), b.Cena("r01", "r01", "S", (4,), "rugas")
    av = [b.Avaliada(c1, "OK"), b.Avaliada(c2, "NOK"), b.Avaliada(r1, "NOK"), b.Avaliada(r2, "OK")]
    assert {s["saco"]: s["veredito"] for s in b.por_saco(av)} == {"ok01": "REJEITADO", "r01": "em parte"}


def test_exportar_folder(resultados, tmp_path):
    cenas = b.propor_cenas(resultados, "SR", "rugas", 2, "r")[0] + b.propor_cenas(resultados, "SO", "ok", 1, "ok")[0]
    n = b.exportar(cenas, config_teste(), resultados, tmp_path, treino={"ok01"})
    assert n == {"test/ruga": 4, "train/good": 1, "test/good": 1}
    assert len(list((tmp_path / "masks").rglob("*.png"))) == 6
