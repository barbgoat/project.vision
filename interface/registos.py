"""Interface, registos: o que fica escrito das decisões de quem usa a BIapp.

    revisoes.csv            a decisão sobre cada saco não OK (descartado ou NOK)
    correcoes.csv, dataset/ a classe real de um saco, para o avaliar e a afinação
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from modulos.m7_saida import nome_curto
from nucleo.config import RAIZ
from nucleo.contrato import Resultado
from nucleo.imagens import guardar_imagem


COLUNAS_CORRECOES = ["data_hora", "origem", "estado_sistema", "tipos_sistema", "classe_real", "imagem_dataset"]


# --- Revisão dos sacos não OK -------------------------------------------------------------

COLUNAS_REVISOES = ["data_hora", "saco", "origem", "estado_sistema", "decisao", "mensagem"]


def registar_revisao(resultado: Resultado, numero: int, decisao: str, pasta: Path) -> None:
    """Acrescenta a decisão sobre um saco ("NOK" ou "descartado") a <pasta>/revisoes.csv."""
    caminho = pasta / "revisoes.csv"
    novo = not caminho.exists()
    pasta.mkdir(parents=True, exist_ok=True)
    with open(caminho, "a", newline="", encoding="utf-8-sig") as ficheiro:
        escritor = csv.writer(ficheiro, delimiter=";")
        if novo:
            escritor.writerow(COLUNAS_REVISOES)
        escritor.writerow([datetime.now().isoformat(sep=" ", timespec="seconds"), numero,
                           resultado.captura.origem if resultado.captura else "", resultado.estado, decisao,
                           resultado.mensagem])


# --- Dataset: a classe real de um saco ----------------------------------------------------

def pasta_dataset(perfil: Path) -> Path:
    """A montagem do dataset para as correções: telemovel com o perfil do telemóvel; senão, blackfly."""
    return RAIZ / "dataset" / ("telemovel" if perfil.stem == "telemovel" else "blackfly")


def guardar_correcao(resultado: Resultado, classe: str, dataset: Path, registo: Path) -> str:
    """Corrige o veredito de um saco: a classe real que o operador ou o engenheiro viu.

    A imagem original (sem anotações, PNG) vai para <dataset>/<classe>/, como
    o avaliar e a afinação esperam, e a correção fica numa linha do registo
    (correcoes.csv, ";" e utf-8-sig como os do M7). Uma imagem que já está
    no dataset não é copiada outra vez: a pasta onde está é que diz a classe.

    Recebe:
        resultado: o saco a corrigir (com a captura analisada).
        classe: "ok", um TipoDefeito ou "varios" (as pastas do dataset).
        dataset: a pasta da montagem, p.ex. dataset/telemovel.
        registo: o correcoes.csv (acrescenta uma linha; cria-o se não existir).
    Devolve:
        O que foi feito, em texto, para a barra de estado.
    """
    captura = resultado.captura
    if captura is None:
        raise ValueError("este saco não tem imagem (passagem descartada)")
    origem = Path(captura.origem)
    if origem.is_file() and origem.resolve().is_relative_to(dataset.parent.resolve()):
        destino = None
        feito = f"{origem.name} já está no dataset ({origem.parent.name}/): ficou só registada"
    else:
        nome = f"{datetime.now():%Y%m%d_%H%M%S_%f}"[:-3] + f"_{nome_curto(captura.origem)}.png"
        destino = dataset / classe / nome
        guardar_imagem(destino, captura.imagem)
        feito = f"imagem em {dataset.name}/{classe}/{nome}"

    novo = not registo.exists()
    registo.parent.mkdir(parents=True, exist_ok=True)
    with open(registo, "a", newline="", encoding="utf-8-sig") as ficheiro:
        escritor = csv.writer(ficheiro, delimiter=";")
        if novo:
            escritor.writerow(COLUNAS_CORRECOES)
        tipos = ",".join(f"{tipo}:{n}" for tipo, n in sorted(resultado.contagem_tipos.items()))
        escritor.writerow([
            datetime.fromtimestamp(captura.instante).isoformat(sep=" ", timespec="milliseconds"),
            captura.origem, resultado.estado, tipos, classe, destino or "",
        ])
    return feito
