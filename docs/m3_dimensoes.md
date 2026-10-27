# M3 Dimensões: como funciona e como verificar

O M3 usa as medidas que o M2 já fez (não volta a medir) e responde a "o saco
tem o tamanho e a forma certos?":

1. **Comprimento** e 2. **largura** (`modulos/m3_dimensoes/escravo.py`): NOK se
   |medido − nominal| > tolerância (`DIMENSOES_*_NOMINAL_MM`,
   `DIMENSOES_TOLERANCIA_*_MM`), p.ex. "comprimento 158.1 mm, fora de 150 +/- 3 mm (+8.1 mm)".
3. **Forma** (`modulos/m3_dimensoes/forma.py`): a silhueta do saco é comparada
   com o **retângulo ideal** do tamanho medido. Cada zona onde **falta**
   material (canto dobrado, corte torto, rasgão na borda) ou **sobra** (aba,
   dobra para fora) com mais de `DIMENSOES_FORMA_AREA_MIN_MM2` é um defeito
   próprio, com a posição em mm e a causa provável. Desvios até
   `DIMENSOES_FORMA_FOLGA_MM` junto às arestas são ignorados (cantos
   arredondados, soldaduras). Falta num canto e sobra no canto oposto da mesma
   ponta são reconhecidas como **corte torto**.

```
   retângulo ideal (amarelo)      canto dobrado (vermelho)      aba (magenta)
   ┌────────────────┐              ┌──────────────◣┐            ┌────────────────┐
   │                │              │               │            │                │
   └────────────────┘              └───────────────┘            └──────▀▀▀───────┘
```

Medidas no relatório: comprimento, largura, desvio de cada um, área,
retangularidade, área em falta e área a mais.

## Resultados em sacos sintéticos

Medidos pelo M2 (5 px/mm, luz rasante, ruído); casos em `testes/test_m3_dimensoes.py`:

| Caso | Resultado |
|---|---|
| 8 sacos certos (8 cores, rotações de −60° a +89°) | todos **OK**, sem falsos alarmes (150,0–150,1 × 55,0–55,2 mm) |
| comprido (158 mm) / estreito (50 mm) | NOK só pelo comprimento / só pela largura |
| canto dobrado | NOK: "falta material num canto (canto dobrado?): 71 mm² em x=70, y=−22 mm" (real: ±70, ±22,5) |
| corte torto (12 mm) | NOK: comprimento curto + falta e sobra na mesma ponta, "(corte torto?)" |
| aba / rasgão num lado | NOK: "material a mais num lado" em x=0, y=30 mm / "falta material num lado" |
| canto dobrado + aba no mesmo saco | NOK com **2 defeitos separados** |
| `main.py` numa pasta com os 8 casos | saco certo **OK**, os outros 7 **NOK**; M3 em ~5 ms |

## Verificar com as tuas fotos

Etapa `dimensoes_forma` no painel de depuração (ver [depuracao.md](depuracao.md)):

```powershell
python -m ferramentas.testar_modulo dimensoes dataset\telemovel\dimensao --config configuracoes\telemovel.json --guardar resultados\verificacao_m3
```

**Atenção à lente** (grande-angular): um saco com 5 mm de altura parece ~2,5 mm
mais comprido. Calibrar o `GERAL_PX_POR_MM` com a régua à altura do contorno do
saco (ver [camara_e_lente.md](camara_e_lente.md)).
