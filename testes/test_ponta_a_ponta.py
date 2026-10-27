"""main.py e ferramentas, corridos como na linha de comandos (caminhos com acentos)."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from nucleo.config import RAIZ
from nucleo.imagens import guardar_imagem
from testes.comandos import correr, execucao, ler_csv
from testes.sinteticos import CORES, FORMAS, Linha, cena_forma

PPM = 5.0


@pytest.mark.parametrize("comando", [
    ["main.py"], ["-m", "nucleo.config"], ["-m", "ferramentas.testar_modulo"], ["-m", "ferramentas.testar_disparo"],
    ["-m", "ferramentas.calcular_montagem"], ["-m", "ferramentas.afinar"], ["-m", "ferramentas.avaliar"],
    [RAIZ / "ferramentas" / "testar_modulo.py"],
], ids=lambda c: str(c[-1]).rsplit("\\", 1)[-1])
def test_ajuda(comando):
    p = correr(*comando, "--help")
    assert p.returncode == 0, p.stderr[-500:]


def test_main_sem_pasta_explica_o_que_falta():
    p = correr("main.py")
    assert p.returncode == 2 and "--pasta dataset" in p.stderr


def test_ver_a_configuracao_de_um_perfil():
    p = correr("-m", "nucleo.config", "--config", RAIZ / "configuracoes" / "telemovel.json")
    assert p.returncode == 0 and "px_por_mm" in p.stdout


def test_calcular_montagem():
    p = correr("-m", "ferramentas.calcular_montagem")
    assert p.returncode == 0 and "px/mm" in p.stdout


# --- Fotos: cada foto é um saco ---------------------------------------------------------

@pytest.fixture(scope="module")
def fotos(tmp_path_factory):
    """Os sacos de FORMAS (um certo e 7 com defeitos), uma foto vazia e o JSON da montagem."""
    pasta = tmp_path_factory.mktemp("ponta") / "Fotos do telemóvel ç"
    rng = np.random.default_rng(11)
    for nome, poligono in FORMAS.items():
        guardar_imagem(pasta / "fotos" / f"{nome}.png", cena_forma(rng, poligono, angulo=-15, cor=CORES["azul"]))
    guardar_imagem(pasta / "fotos" / "vazio.png", cena_forma(rng, [(0, 0), (0.1, 0), (0.1, 0.1)]))
    (pasta / "montagem.json").write_text(json.dumps({"geral": {"px_por_mm": PPM}}), encoding="utf-8")
    return pasta


@pytest.fixture(scope="module")
def corrida_nok(fotos):
    p = correr("main.py", "--pasta", fotos / "fotos", "--config", fotos / "montagem.json",
               "--saida", fotos / "res_nok", "--depurar", "nok")
    assert p.returncode == 0, p.stderr[-2000:]
    return p, execucao(fotos / "res_nok")


def test_main_um_resultado_por_saco(corrida_nok):
    _, pasta = corrida_nok
    linhas = {Path(linha["origem"]).stem: linha for linha in ler_csv(pasta / "registo.csv")}
    assert linhas["certo"]["estado"] == "OK" and linhas["certo"]["dimensoes"] == "OK"
    assert all(linhas[n]["estado"] == "NOK" for n in FORMAS if n != "certo")
    assert linhas["vazio"]["estado"] == "SEM_SACO" and "localizacao:" in linhas["vazio"]["mensagens"]
    assert all(linhas[n]["relevo"] == "POR_IMPLEMENTAR" for n in FORMAS), "os módulos por fazer aparecem"


def test_main_um_defeito_por_linha(corrida_nok):
    defeitos = ler_csv(corrida_nok[1] / "defeitos.csv")
    assert len(defeitos) >= 8 and all(d["modulo"] == "dimensoes" and d["tipo"] == "dimensao" for d in defeitos)


def test_main_depuracao_so_dos_nok(corrida_nok):
    pasta = corrida_nok[1]
    depurados = sorted(d.name for d in (pasta / "depuracao").iterdir())
    assert len(depurados) == len(FORMAS) and not any("certo" in d for d in depurados)
    linhas = ler_csv(pasta / "registo.csv")
    assert all(bool(linha["depuracao"]) == (linha["estado"] != "OK") for linha in linhas)
    assert len(list((pasta / "imagens").iterdir())) == len(FORMAS), "imagens anotadas dos não OK"


def test_repetir_um_caso_a_partir_da_depuracao(corrida_nok, fotos, tmp_path):
    original = next((corrida_nok[1] / "depuracao").glob("*canto_e_aba*/00_original.png"))
    (tmp_path / "caso").mkdir()
    (tmp_path / "caso" / "original.png").write_bytes(original.read_bytes())
    p = correr("main.py", "--pasta", tmp_path / "caso", "--config", fotos / "montagem.json", "--saida", tmp_path / "res")
    assert p.returncode == 0 and "NOK" in p.stdout and "2 dimensao" in p.stdout


def test_main_sem_depuracao_nao_cria_nada(fotos, tmp_path):
    p = correr("main.py", "--pasta", fotos / "fotos", "--config", fotos / "montagem.json", "--saida", tmp_path)
    assert p.returncode == 0 and not (execucao(tmp_path) / "depuracao").exists()


def test_testar_modulo_com_depuracao(fotos, tmp_path):
    p = correr("-m", "ferramentas.testar_modulo", "dimensoes", fotos / "fotos", "--config", fotos / "montagem.json",
               "--guardar", tmp_path)
    assert p.returncode == 0 and "painel:" in p.stdout
    assert len(list(tmp_path.glob("*/painel.jpg"))) == len(FORMAS) + 1
    assert any(tmp_path.glob("*canto_e_aba*/*dimensoes_forma.png"))


# --- Vídeo: barreira virtual ------------------------------------------------------------

@pytest.fixture(scope="module")
def video(tmp_path_factory):
    """A linha sintética (6 sacos a passar) num vídeo com acentos no nome, e o JSON da montagem."""
    pasta = tmp_path_factory.mktemp("video")
    imagens, _ = Linha(np.random.default_rng(3)).gerar()
    altura, largura = imagens[0].shape[:2]
    escritor = cv2.VideoWriter(str(pasta / "v.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), Linha.FPS, (largura, altura))
    for img in imagens:
        escritor.write(img)
    escritor.release()
    caminho = pasta / "vídeo linha ç.mp4"
    (pasta / "v.mp4").rename(caminho)
    for k in range(3):
        guardar_imagem(pasta / "sequencia" / f"seq_{k}.png", imagens[k])
    (pasta / "montagem.json").write_text(json.dumps({"geral": {"px_por_mm": Linha.PPM}, "disparo": {"fps": Linha.FPS}}),
                                         encoding="utf-8")
    return caminho, pasta / "montagem.json"


def test_main_com_video_uma_linha_por_saco(video, tmp_path):
    caminho, montagem = video
    p = correr("main.py", "--fonte", "video", "--video", caminho, "--disparo", "virtual", "--config", montagem,
               "--saida", tmp_path)
    assert p.returncode == 0, p.stderr[-2000:]
    linhas = ler_csv(execucao(tmp_path) / "registo.csv")
    assert [linha["passagem"] for linha in linhas] == ["1", "2", "3", "4", "5", "6"]
    assert all(linha["localizacao"] == "OK" and linha["imagens_analisadas"] == "1" for linha in linhas)


def test_testar_disparo_encontra_as_passagens(video, tmp_path):
    caminho, montagem = video
    p = correr("-m", "ferramentas.testar_disparo", "--video", caminho, "--config", montagem, "--guardar", tmp_path)
    assert p.returncode == 0 and "6 passagens" in p.stdout


def test_main_disparo_sensor_com_uma_pasta_da_erro_claro(video, tmp_path):
    caminho, montagem = video
    p = correr("main.py", "--pasta", caminho.parent / "sequencia", "--disparo", "sensor", "--config", montagem,
               "--saida", tmp_path)
    assert p.returncode == 2 and "virtual" in p.stderr
