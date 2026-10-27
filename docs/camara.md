# Câmara, lente e disparo

## Equipamento

| | | O que implica |
|---|---|---|
| **Câmara** BFS-PGE-50S5C-C | 2448 × 2048 px, 3,45 µm, global shutter, PoE GigE | 24 FPS em `BayerRG8`; exposição 11 µs–30 s |
| **Lente** Basler C23-0828-16M | 8 mm, F2.8–F16 (melhor a F4), distorção 0,07 % | a 300 mm: campo de 308 × 258 mm, **~7,9 px/mm** |

**Grande-angular:** o que está mais alto parece maior (5 mm de altura → +2,5 mm
em 150 mm). Calibrar `GERAL_PX_POR_MM` com uma régua **à altura do saco**.

**Montagem:** lente a ~300 mm, F4; lado de 2448 px ao longo do tapete; placa de
rede só para a câmara com "Jumbo Packet" 9014; PoE (switch ou injetor).

```powershell
python ferramentas\testar_camara.py                         # a câmara responde?
python -m ferramentas.calcular_montagem --distancia 350     # campo, px/mm, FPS, exposição, rede
```

## Fases de ensaio

1. Sem tapete, saco parado → 2. no tapete, parado → 3. tapete a andar.

Nas fases 1–2 usa-se um **botão** na entrada Line0 (perfil `bancada_botao`):
pousar o saco, carregar → uma imagem → um resultado; os saltos do botão são
ignorados. Ligação: botão normalmente aberto entre uma fonte externa e a Line0,
retorno no OPTO_GND; **pinos e tensão a confirmar no guia da BFS-PGE**. Sem
botão: perfil `bancada_virtual`.

## Disparo (trigger)

A câmara capta várias imagens do mesmo saco; o M1 junta-as numa **passagem**
e o Mestre dá **uma decisão por saco**, analisando a imagem com o saco mais
centrado. Se a análise se atrasar, a passagem fica em ERRO: nenhum saco se perde em silêncio.

| Modo | Quando | Como separa os sacos |
|---|---|---|
| `foto` | fotos; câmara com botão | cada imagem é um saco |
| `sensor` | linha com sensor na Line0 | imagens com o sensor ativo (`janela`) ou rajada por ativação (`rajada`) |
| `virtual` | vídeo, webcam, câmara sem sensor | o saco a atravessar uma faixa no centro da imagem |

**FPS:** imagens com o saco inteiro ≈ FPS × (campo − saco) / velocidade
(24 × (306 − 150) / 500 ≈ 7). Tem de dar ≥ 1. A exposição cabe em 1/FPS e,
com o tapete a andar, ≤ ~125 µs (muita luz).

**Sem câmara:** filmar o tapete com o telemóvel, começando vazio, e

```powershell
python -m ferramentas.testar_disparo --video linha.mp4 --guardar resultados\disparo
```

Confirmar: uma passagem por saco e a 1.ª candidata com o saco inteiro e centrado.
