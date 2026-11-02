# Câmara, lente e disparo

## Equipamento

| | | O que implica |
|---|---|---|
| **Câmara** Basler acA1300-30gm (agora), pelo `pypylon` | 1296 × 966 px, 3,75 µm, monocromática, GigE, 30 FPS | `Mono8`; a câmara é **partilhada** (ver abaixo) |
| **Câmara** FLIR Blackfly S BFS-PGE-50S5C-C (mais tarde) | 2448 × 2048 px, 3,45 µm, PoE GigE | código pronto e comentado em `fontes.py` |
| **Lente** Basler C23-0828-16M | 8 mm, F2.8–F16 (melhor a F4), distorção 0,07 % | a 300 mm com a Blackfly: campo de 308 × 258 mm, **~7,9 px/mm** |

**Grande-angular:** o que está mais alto parece maior (5 mm de altura → +2,5 mm
em 150 mm). Medir `GERAL_PX_POR_MM` com uma régua **à altura do saco**.

**Montagem:** câmara fixa, sempre à mesma distância; lente a ~300 mm, F4; lado maior
do sensor ao longo do tapete. GigE: placa de rede só para a câmara, com "Jumbo
Packet" 9014 e IP fixo na mesma sub-rede.

**Câmara partilhada com outra aplicação.** O programa procura-a pelo nº de série
(`camara.serial`, obrigatório no perfil; se não aparecer, liga-se por `camara.ip` e confirma o
nº). Muda só a exposição (`ExposureTimeAbs`), o ganho (`GainRaw`, se `aquisicao.ganho_raw` ≥ 0) e o
trigger; guarda os valores originais e repõe-nos ao fechar, o trigger primeiro. Nunca grava nem
carrega User Sets. Se a outra aplicação (ou o pylon Viewer) a tiver aberta, dá um erro claro.

```powershell
pip install pypylon
python -m ferramentas.testar_camara --config configuracoes\linha.json   # a câmara responde? (grava frame.png)
python -m ferramentas.calcular_montagem --distancia 350     # campo, px/mm, FPS, exposição, rede
```

Sem câmara à mão: `$env:PYLON_CAMEMU = "1"` dá uma câmara emulada do pylon (nº de série
`0815-0000`), para experimentar o programa.

## Disparo: dois modos

A câmara capta as imagens; o M1 junta as de cada saco numa **passagem** e o Mestre
dá **uma decisão por saco**. Se a análise se atrasar, a passagem fica em ERRO:
nenhum saco se perde em silêncio. O modo escolhe-se no perfil (`disparo.modo`), no
`main.py --disparo` ou na barra da BIapp.

| Modo | Quando | Como separa os sacos |
|---|---|---|
| `ao_vivo` | sem sensor (ensaios, linha ainda sem sensor) | a câmara capta em contínuo a `disparo.fps`; o saco a atravessar uma faixa no centro da imagem (barreira virtual) |
| `trigger` | botão ou sensor de presença | a câmara só capta quando recebe o sinal na `disparo.trigger_linha`: cada sinal, uma imagem, um saco |

**Ao vivo:** arrancar com o tapete vazio (as primeiras imagens ensinam o fundo). Imagens
com o saco inteiro ≈ FPS × (campo − saco) / velocidade (24 × (306 − 150) / 500 ≈ 7);
tem de dar ≥ 1. A exposição cabe em 1/FPS e, com o tapete a andar, ≤ ~125 µs (muita luz).
Com o saco parado (perfil `bancada_virtual`): pousá-lo abre a passagem e retirá-lo fecha-a.

**Trigger:** botão normalmente aberto (ou o sensor) entre uma fonte externa e a entrada
opto-isolada da câmara (na Basler ace, `Line1`; na Blackfly, `Line0`), retorno no GND
opto-isolado; **pinos e tensão a confirmar no manual do modelo**. A câmara dispara no flanco de
`disparo.trigger_ativacao` (`RisingEdge` ou `FallingEdge`). Os saltos do botão são ignorados:
sinais a menos de `DISPARO_ANTI_RESSALTO_MS` do anterior, medidos com o relógio da câmara.

```powershell
python -m ferramentas.testar_disparo --config configuracoes\linha.json --guardar resultados\disparo
```

Confirmar: uma passagem por saco e a 1.ª candidata com o saco inteiro e centrado.
