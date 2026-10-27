"""M4 Cor: "o saco tem a cor certa?".

Mede a cor média do saco e compara-a com uma cor de referência, em CIELAB,
onde a distância entre duas cores (ΔE) corresponde à diferença que o olho vê.
Deteta sacos trocados (de outra cor) e sacos descolorados.

Atenção: só é fiável com o balanço de brancos e a exposição fixos (câmara da
linha). Com fotos de telemóvel, que ajustam a cor sozinhas, vai dar falsos
alarmes; nesse caso desligar o módulo (COR_ATIVO = False em nucleo/defines.py).
"""

from __future__ import annotations

from nucleo.contrato import Escravo, Relatorio, Saco


class Cor(Escravo):
    """O escravo M4. Usa a secção cor da configuração."""

    nome = "cor"
    pergunta = "O saco tem a cor certa?"

    def analisar(self, saco: Saco, guardar_intermedios: bool = False) -> Relatorio:
        """Compara a cor média do saco com a referência.

        Recebe:
            saco: o Saco do M2 (só de leitura).
            guardar_intermedios: se True, pôr em intermedios um mapa da
                diferença de cor de cada píxel face à referência.

        Devolve:
            Relatorio com um Defeito COR se a diferença for grande (contorno =
            None: diz respeito ao saco todo) e as medidas L, a, b e delta_e.

        O que vai fazer (parâmetros em self.cfg.cor):
            1. Converter o recorte para CIELAB a partir de float32 em [0, 1]
               (cv2.cvtColor(img.astype(np.float32) / 255, cv2.COLOR_BGR2Lab)):
               dá L* em 0–100 e a*, b* com sinal. Em uint8, o OpenCV escala os
               valores (L*·255/100, a*+128), o que estraga as contas de ΔE.
            2. Cor do saco = mediana de L*, a*, b* em saco.mascara_util (a
               mediana ignora reflexos e pequenos defeitos).
            3. ΔE entre essa cor e referencia_lab: ΔE76 (distância euclidiana)
               para começar; ΔE2000 se for preciso seguir melhor a perceção.
            4. ΔE > delta_e_max → Defeito COR, intensidade = ΔE, descrição
               "cor fora da referência (dE 8.3 > 6.0)".
            5. Relatorio.concluir(self.nome, defeitos, medidas).
        Calibração (ferramenta futura): referencia_lab = média das cores de
        vários sacos OK, medidas com a câmara final.
        """
        raise NotImplementedError
