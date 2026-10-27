"""M3 Dimensões: "o saco tem o tamanho e a forma certos?".

Usa as medidas que o M2 já fez (comprimento e largura do Saco) e a silhueta do
saco no recorte normalizado. Não volta a medir: as medidas do M2 e do M3
nunca podem discordar.

    1. Comprimento: |medido − nominal| ≤ tolerância?
    2. Largura: idem.
    3. Forma: a silhueta é comparada com o retângulo ideal do tamanho medido.
       Cada zona onde FALTA material (canto dobrado, corte torto, rasgão na
       borda) ou SOBRA (aba, dobra para fora), acima de uma área mínima, é um
       defeito próprio, com a sua posição.

           retângulo ideal          saco com o canto dobrado
           ┌────────────────┐        ╱───────────────┐
           │                │       ╱  ◣ falta        │
           │                │       │                 │
           └────────────────┘       └─────────────────┘

A diferença para o M2: o M2 só verifica se "é um saco" (limites largos); o M3
verifica se é o saco CERTO (tolerâncias apertadas).

Atenção à lente (Basler 8 mm a 300 mm do tapete, grande-angular): o que está
mais alto parece maior. Um saco com 5 mm de altura parece ~1,7 % maior (~2,5 mm
em 150 mm), perto da tolerância de ±3 mm. Para o compensar:
    - calibrar GERAL_PX_POR_MM com a régua à altura do contorno do saco, e não
      pousada no tapete;
    - medir na imagem com o saco centrado (o disparo já escolhe essa: longe do
      centro, a perspetiva aumenta o erro).
A calculadora mostra este erro para outras alturas e lentes
(python -m ferramentas.calcular_montagem --espessura 8). A distorção da lente
(0,07 %) é desprezável.

Está partido em dois ficheiros:

    escravo.py   o ponto de entrada que o Mestre chama; comprimento e largura
    forma.py     a silhueta vs o retângulo ideal: falta e sobra de material
"""

from modulos.m3_dimensoes.escravo import Dimensoes

__all__ = ["Dimensoes"]
