"""Interface, avisos: dizer ao operador o que se passa e o que fazer, sem pesar na inspeção.

    explicar      porque é que a inspeção parou, em palavras simples e com o que fazer
    motivo        porque é que um saco não ficou OK, numa linha (para a vista Operador)
    AlarmeNOK     demasiados NOK nos últimos sacos
    Semaforo      um ponto colorido com o estado (câmara, disco, base de dados) na barra de estado
    Avisos        as faixas de aviso por cima da imagem, cada uma com a sua chave

Tudo isto corre na janela (thread da interface) com o que o ciclo já entrega: nada aqui é chamado
pela inspeção, e o que se verifica com o tempo (o disco) é de poucos em poucos segundos.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget

from nucleo.contrato import Estado, Resultado

VERDE, AMARELO, VERMELHO, CINZENTO = "#2e7d32", "#f9a825", "#c62828", "#90a4ae"
DISCO_AVISO_GB = 2.0  # abaixo disto, aviso amarelo no disco
DISCO_MINIMO_GB = 0.5  # abaixo disto não se começa a inspecionar (gravar os resultados ia falhar)
VERIFICAR_S = 5.0  # de quanto em quanto tempo os semáforos se atualizam (o disco: ~0,1 ms)
ALARME_NOK_SACOS = 20  # o alarme olha para os últimos tantos sacos decididos
ALARME_NOK_MAX = 5  # e dispara com tantos NOK entre eles
MOTIVOS_MAX = 3  # defeitos mostrados no motivo; os outros contam-se

# (o que aparece no erro, em minúsculas) → (o que se diz ao operador, é da câmara?)
_CAUSAS: tuple[tuple[tuple[str, ...], str, bool], ...] = (
    (("no space left", "requested and 0 written", "errno 28", "disco cheio"),
     "Disco cheio: não há espaço para gravar os resultados. Libertar espaço (sessões antigas em "
     "resultados, Reciclagem) e voltar a Iniciar.", False),
    (("controlled by another application", "em uso por outra aplicação"),
     "A câmara está aberta noutro programa (pylon Viewer, outra BIapp ou um script de teste). "
     "Fechá-lo e voltar a Iniciar.", True),
    (("não encontrada", "not found", "no device"),
     "Câmara não encontrada: ver o cabo de rede e a alimentação da câmara.", True),
    (("deixou de enviar imagens",),
     "A câmara deixou de enviar imagens: ver o cabo de rede e a alimentação, e voltar a Iniciar.", True),
    (("falta o pypylon",), "Falta o pypylon no computador (pip install pypylon).", True),
    (("fundo_referencia",),
     "Falta o fundo vazio do perfil: Afinar câmara → Fundo vazio (sem saco à vista) → Guardar no perfil.", False),
)


def explicar(erro: str) -> tuple[str, bool]:
    """(o que dizer ao operador, se a culpa é da câmara) para o erro que parou a inspeção."""
    baixo = erro.lower()
    for chaves, texto, camara in _CAUSAS:
        if any(c in baixo for c in chaves):
            return texto, camara
    curto = erro.splitlines()[0] if erro else "erro desconhecido"
    return f"A inspeção parou: {curto[:200]}. Os detalhes estão no Log (vista Engenharia).", False


def motivo(r: Resultado) -> str:
    """Porque é que o saco não ficou OK, numa linha; vazio num OK."""
    if r.estado is Estado.OK:
        return ""
    if r.estado is Estado.NOK:
        textos = [d.descricao or str(d.tipo) for d in r.defeitos if not d.causa]
        if not textos:
            return r.mensagem
        mais = len(textos) - MOTIVOS_MAX
        return "; ".join(textos[:MOTIVOS_MAX]) + (f"; e mais {mais}" if mais > 0 else "")
    return (r.mensagem.splitlines()[0] if r.mensagem else str(r.estado))[:200]


class AlarmeNOK:
    """Os últimos ALARME_NOK_SACOS sacos decididos (depois da revisão): alarme com ALARME_NOK_MAX NOK ou mais."""

    def __init__(self, sacos: int = ALARME_NOK_SACOS, maximo: int = ALARME_NOK_MAX) -> None:
        self.maximo = maximo
        self._ultimos: deque[bool] = deque(maxlen=sacos)

    def limpar(self) -> None:
        self._ultimos.clear()

    def contar(self, nok: bool) -> None:
        self._ultimos.append(nok)

    @property
    def nok(self) -> int:
        return sum(self._ultimos)

    @property
    def ativo(self) -> bool:
        return self.nok >= self.maximo

    def texto(self) -> str:
        return (f"{self.nok} NOK nos últimos {len(self._ultimos)} sacos: verificar a máquina, o material e a "
                "afinação antes de continuar.")


def taxa_nok(ok: int, nok: int) -> str:
    """A percentagem de NOK nos sacos contados (OK + NOK), p.ex. "12,5 % NOK"; vazio sem sacos."""
    total = ok + nok
    return f"{100 * nok / total:.1f} % NOK".replace(".", ",") if total else ""


class Semaforo(QLabel):
    """● Nome: estado, com a cor do estado; a dica diz mais."""

    def __init__(self, nome: str) -> None:
        super().__init__()
        self.nome = nome
        self.cor = CINZENTO
        self.mostrar(CINZENTO, "—")

    def mostrar(self, cor: str, estado: str, dica: str = "") -> None:
        self.cor = cor
        self.setText(f"<span style='color:{cor}; font-size:14pt'>●</span> {self.nome}: {estado}")
        self.setToolTip(dica or f"{self.nome}: {estado}")


class Avisos(QWidget):
    """Faixas de aviso por cima da imagem, uma por chave (p.ex. "parou", "disco", "nok"), cada uma com
    a sua cor, um botão opcional e um × para a fechar. Sem avisos não ocupa espaço."""

    def __init__(self) -> None:
        super().__init__()
        self._faixas: dict[str, tuple[QFrame, QLabel]] = {}
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self.hide()

    def mostrar(self, chave: str, texto: str, cor: str = VERMELHO, botao: str = "",
                acao: Callable[[], None] | None = None) -> None:
        if chave in self._faixas:
            self._faixas[chave][1].setText(texto)
            self._faixas[chave][0].setStyleSheet(_estilo(cor))
            return
        faixa = QFrame()
        faixa.setStyleSheet(_estilo(cor))
        linha = QHBoxLayout(faixa)
        linha.setContentsMargins(12, 6, 6, 6)
        rotulo = QLabel(texto)
        rotulo.setWordWrap(True)
        linha.addWidget(rotulo, 1)
        if botao and acao is not None:
            b = QPushButton(botao)
            b.clicked.connect(lambda *_: acao())
            linha.addWidget(b)
        fechar = QToolButton()
        fechar.setText("✕")
        fechar.setToolTip("Fechar este aviso")
        fechar.clicked.connect(lambda *_, c=chave: self.limpar(c))
        linha.addWidget(fechar)
        self._layout.addWidget(faixa)
        self._faixas[chave] = (faixa, rotulo)
        self.show()

    def limpar(self, chave: str) -> None:
        if (faixa := self._faixas.pop(chave, None)) is not None:
            faixa[0].deleteLater()
        self.setVisible(bool(self._faixas))

    def texto(self, chave: str) -> str:
        """O texto do aviso com esta chave ("" se não houver)."""
        return self._faixas[chave][1].text() if chave in self._faixas else ""


def _estilo(cor: str) -> str:
    return (f"QFrame {{ background: {cor}; border-radius: 6px; }} "
            "QLabel { color: white; font-size: 12pt; font-weight: bold; background: transparent; }")
