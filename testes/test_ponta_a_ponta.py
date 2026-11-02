"""main.py e ferramentas, de ponta a ponta, com uma câmara simulada (caminhos com acentos)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

import main as programa
from ferramentas import testar_disparo
from nucleo.config import RAIZ
from nucleo.contrato import Captura
from nucleo.imagens import guardar_imagem
from testes.apoio import (
    CORES, FORMAS, CamaraSimulada, Linha, cena_forma, com_camara, correr, execucao, ler_csv, montagem,
)

PPM = 5.0


@pytest.mark.parametrize("comando", [
    ["main.py"], ["-m", "nucleo.config"], ["-m", "ferramentas.testar_modulo"], ["-m", "ferramentas.testar_disparo"],
    ["-m", "ferramentas.calcular_montagem"], ["-m", "ferramentas.afinar"], ["-m", "ferramentas.avaliar"],
    [RAIZ / "ferramentas" / "testar_modulo.py"],
], ids=lambda c: str(c[-1]).rsplit("\\", 1)[-1])
def test_ajuda(comando):
    p = correr(*comando, "--help")
    assert p.returncode == 0, p.stderr[-500:]


def test_main_sem_a_camara_explica_o_que_falta():
    p = correr("main.py", "--serie", "nao-existe")
    assert p.returncode == 2 and "Basler" in p.stderr


def test_ver_a_configuracao_de_um_perfil():
    p = correr("-m", "nucleo.config", "--config", RAIZ / "configuracoes" / "linha.json")
    assert p.returncode == 0 and "px_por_mm" in p.stdout


def test_calcular_montagem():
    p = correr("-m", "ferramentas.calcular_montagem")
    assert p.returncode == 0 and "px/mm" in p.stdout


# --- Trigger: um saco por sinal ---------------------------------------------------------

@pytest.fixture(scope="module")
def sacos(tmp_path_factory):
    """Os sacos de FORMAS (um certo e 7 com defeitos) e uma imagem vazia, como a câmara os daria com
    o trigger; também em ficheiros (para o testar_modulo) e o JSON da montagem."""
    pasta = tmp_path_factory.mktemp("ponta") / "Ensaios da câmara ç"
    rng = np.random.default_rng(11)
    capturas = []
    for nome, poligono in FORMAS.items():
        capturas.append(Captura(cena_forma(rng, poligono, angulo=-15, cor=CORES["azul"]), f"{nome}.png", 0.0))
    capturas.append(Captura(cena_forma(rng, [(0, 0), (0.1, 0), (0.1, 0.1)]), "vazio.png", 0.0))
    for c in capturas:
        guardar_imagem(pasta / "imagens" / c.origem, c.imagem)
    valores = montagem(geral={"px_por_mm": PPM}, disparo={"anti_ressalto_ms": 0.0, "fila_max": 20})  # todos à espera
    (pasta / "montagem.json").write_text(json.dumps(valores), encoding="utf-8")
    return pasta, capturas


def _correr_main(monkeypatch, sacos, saida: Path, *extra: str) -> int:
    pasta, capturas = sacos
    com_camara(monkeypatch, programa, lambda cfg: CamaraSimulada(capturas, trigger=True))
    return programa.main(["--disparo", "trigger", "--config", str(pasta / "montagem.json"), "--saida", str(saida),
                          *extra])


@pytest.fixture
def corrida_nok(monkeypatch, sacos, tmp_path):
    assert _correr_main(monkeypatch, sacos, tmp_path / "res_nok", "--depurar", "nok") == 0
    return execucao(tmp_path / "res_nok")


def test_main_um_resultado_por_saco(corrida_nok):
    linhas = {Path(linha["origem"]).stem: linha for linha in ler_csv(corrida_nok / "registo.csv")}
    assert linhas["certo"]["estado"] == "OK" and linhas["certo"]["dimensoes"] == "OK"
    assert all(linhas[n]["estado"] == "NOK" for n in FORMAS if n != "certo")
    assert linhas["vazio"]["estado"] == "ERRO" and "localizacao:" in linhas["vazio"]["mensagens"], \
        "o sinal disparou mas não se viu o saco: nunca é aprovado"
    assert all(linhas[n]["relevo"] == "POR_IMPLEMENTAR" for n in FORMAS), "os módulos por fazer aparecem"


def test_main_um_defeito_por_linha(corrida_nok):
    defeitos = ler_csv(corrida_nok / "defeitos.csv")
    assert len(defeitos) >= 8 and all(d["modulo"] == "dimensoes" and d["tipo"] == "dimensao" for d in defeitos)


def test_main_depuracao_so_dos_nao_ok(corrida_nok):
    depurados = sorted(d.name for d in (corrida_nok / "depuracao").iterdir())
    assert len(depurados) == len(FORMAS) and not any("certo" in d for d in depurados)
    linhas = ler_csv(corrida_nok / "registo.csv")
    assert all(bool(linha["depuracao"]) == (linha["estado"] != "OK") for linha in linhas)
    assert len(list((corrida_nok / "imagens").iterdir())) == len(FORMAS), "imagens anotadas dos não OK"


def test_repetir_um_caso_a_partir_da_depuracao(corrida_nok, sacos):
    original = next((corrida_nok / "depuracao").glob("*canto_e_aba*/00_original.png"))
    p = correr("-m", "ferramentas.testar_modulo", "dimensoes", original, "--config", sacos[0] / "montagem.json")
    assert p.returncode == 0 and "NOK(2)" in p.stdout


def test_main_sem_depuracao_nao_cria_nada(monkeypatch, sacos, tmp_path):
    assert _correr_main(monkeypatch, sacos, tmp_path) == 0
    assert not (execucao(tmp_path) / "depuracao").exists()


def test_testar_modulo_com_depuracao(sacos, tmp_path):
    pasta = sacos[0]
    p = correr("-m", "ferramentas.testar_modulo", "dimensoes", pasta / "imagens", "--config", pasta / "montagem.json",
               "--guardar", tmp_path)
    assert p.returncode == 0 and "painel:" in p.stdout
    assert len(list(tmp_path.glob("*/painel.jpg"))) == len(FORMAS) + 1
    assert any(tmp_path.glob("*canto_e_aba*/*dimensoes_forma.png"))


def test_main_trigger_com_a_camara_em_continuo_da_erro_claro(monkeypatch, sacos, tmp_path):
    pasta, capturas = sacos
    com_camara(monkeypatch, programa, lambda cfg: CamaraSimulada(capturas))  # imagens sem o sinal
    codigo = programa.main(["--disparo", "trigger", "--config", str(pasta / "montagem.json"),
                            "--saida", str(tmp_path)])
    assert codigo == 2


# --- Ao vivo: barreira virtual ----------------------------------------------------------

@pytest.fixture(scope="module")
def linha(tmp_path_factory):
    """A linha sintética (6 sacos a passar) e o JSON da montagem."""
    pasta = tmp_path_factory.mktemp("ao vivo ç")
    imagens, _ = Linha(np.random.default_rng(3)).gerar()
    valores = montagem(geral={"px_por_mm": Linha.PPM}, disparo={"fps": Linha.FPS})
    (pasta / "montagem.json").write_text(json.dumps(valores), encoding="utf-8")
    return imagens, pasta / "montagem.json"


def test_main_ao_vivo_uma_linha_por_saco(monkeypatch, linha, tmp_path):
    imagens, perfil = linha
    com_camara(monkeypatch, programa, lambda cfg: CamaraSimulada(imagens, intervalo_s=1 / Linha.FPS))
    assert programa.main(["--disparo", "ao_vivo", "--config", str(perfil), "--saida", str(tmp_path)]) == 0
    linhas = ler_csv(execucao(tmp_path) / "registo.csv")
    assert [linha["passagem"] for linha in linhas] == ["1", "2", "3", "4", "5", "6"]
    assert all(linha["localizacao"] == "OK" and linha["imagens_analisadas"] == "1" for linha in linhas)


def test_testar_disparo_encontra_as_passagens(monkeypatch, linha, tmp_path, capsys):
    imagens, perfil = linha
    com_camara(monkeypatch, testar_disparo, lambda cfg: CamaraSimulada(imagens))
    assert testar_disparo.main(["--disparo", "ao_vivo", "--config", str(perfil), "--guardar", str(tmp_path)]) == 0
    assert "6 passagens" in capsys.readouterr().out
    assert len(list(tmp_path.glob("passagem_*_1.jpg"))) == 6
