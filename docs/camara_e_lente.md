# Câmara e lente

## Câmara: FLIR Blackfly S BFS-PGE-50S5C-C (PoE GigE, cor, Sony IMX264)

| Característica | Valor | O que implica |
|---|---|---|
| Resolução | 2448 × 2048 px (5 MP), píxel de 3,45 µm, sensor 2/3" (8,45 × 7,07 mm) | com a lente abaixo: **~7,9 px/mm** (0,13 mm por píxel) |
| FPS | até 24 a resolução total | `DISPARO_FPS = 24`; ~7–8 imagens com o saco nominal inteiro a 500 mm/s |
| Obturador | global | sem deformação dos sacos em movimento |
| Exposição | 11 µs a 30 s | tapete a andar, a 500 mm/s: ≤ 125 µs para o arrasto ficar ≤ 0,5 px, o que **pede muita luz** |
| Interface | GigE Vision 1.2 (~120 MB/s), PoE | 24 FPS só em `BayerRG8` (8 bits, cor reconstruída no PC); com `BGR8` ~8 FPS, com 12 bits ~16 |
| Profundidade | 12 bits | usamos 8 bits (`BayerRG8`): chega para a inspeção e permite os 24 FPS |
| Buffer interno | 240 MB | ~48 imagens guardadas na câmara se a rede atrasar |

## Lente: Basler C23-0828-16M

[Documentação do fabricante](https://docs.baslerweb.com/c23-0828-16m).

| Característica | Valor | O que implica |
|---|---|---|
| Distância focal | 8 mm (ângulo horizontal 56,4°) | a **300 mm** do tapete: campo de **308 × 258 mm** |
| Distância ótima de trabalho | 0,3 m (foco de 0,1 m ao infinito) | `LENTE_DISTANCIA_MM = 300` |
| Abertura | F2.8–F16, mais nítida a **F4** | a F4, profundidade de campo de ~78 mm: chega para sacos pousados |
| Distorção | 0,07 % | desprezável: não é preciso corrigir a distorção para medir |
| Círculo de imagem | 11,8 mm | cobre a diagonal do sensor (11,0 mm): cantos sem escurecer |

**Atenção, grande-angular:** o que está mais alto parece maior. Um saco com
5 mm de altura parece ~2,5 mm mais comprido (em 150 mm), perto da tolerância de
±3 mm do M3. Calibrar o `GERAL_PX_POR_MM` com a régua **à altura do contorno do
saco** (não pousada no tapete) e medir com o saco centrado (o disparo já escolhe
essa imagem). Para medidas mais exatas no futuro: uma lente de 16 mm
(C23-1628-16M) a ~600 mm reduz o efeito para metade.

As características estão nos defines `CAMARA_*` e `LENTE_*`. A configuração
recusa pedidos impossíveis: FPS acima de 24, exposição fora de 11 µs–30 s ou
maior do que o intervalo entre imagens, lente que não cobre o sensor.

## Montagem

- lente a ~300 mm do tapete, focada à altura dos sacos, abertura F4;
- o lado de 2448 px **ao longo do movimento do tapete** (mais imagens com o saco inteiro);
- uma **placa de rede só para a câmara**, com "Jumbo Packet" 9014 nas
  propriedades da placa no Windows (`CAMARA_PACOTE_BYTES = 9000`); a 24 FPS a
  ligação GigE fica no limite;
- câmara e placa na mesma sub-rede (no SpinView, "Auto Force IP" se for preciso);
- alimentação por PoE (switch ou injetor PoE entre a câmara e o PC).

Primeiro contacto com a câmara (versão do Spinnaker, câmaras encontradas e uma
imagem em bruto gravada em `frame.png`): `python ferramentas\testar_camara.py`.

## Fases de ensaio

Descritas também em `modulos/m1_aquisicao/__init__.py`:

1. sem tapete, saco parado;
2. em cima do tapete, parado;
3. tapete a andar.

Nas fases 1 e 2 a velocidade do tapete não importa e a exposição escolhe-se só
pelo brilho (perfis `configuracoes/bancada_botao.json` e `bancada_virtual.json`).

**Trigger com um botão** ligado à entrada opto-isolada Line0 (disparo `foto`, o
valor por omissão): pousar o saco, carregar no botão → uma imagem → um
resultado. Os saltos do botão são ignorados (`DISPARO_ANTI_RESSALTO_MS` e
`TriggerOverlap = Off`). Os mesmos fios servem depois para o sensor de
presença. Ligação: botão normalmente aberto em série entre uma fonte de tensão
externa e a Line0, retorno no OPTO_GND (ligar o OPTO_GND primeiro); **pinos e
tensão máxima a confirmar no guia de instalação da BFS-PGE**.

Sem botão: disparo `virtual` (perfil `configuracoes/bancada_virtual.json`).

## Calcular a montagem

```powershell
python -m ferramentas.calcular_montagem                       # câmara + lente dos defines
python -m ferramentas.calcular_montagem --distancia 350       # a mesma lente, mais longe
python -m ferramentas.calcular_montagem --espessura 8         # saco mais alto: que erro nas medidas?
python -m ferramentas.calcular_montagem --focal 16 --distancia 600
```

Mostra o campo de visão e o px/mm (e se bate com `GERAL_PX_POR_MM`), a
profundidade de campo, o erro causado pela altura do saco, se o saco cabe em
qualquer rotação, quantas imagens terá o saco inteiro à vista, o arrasto da
exposição, o débito na rede e a memória. Depois de montar, medir o px/mm com
uma régua e pô-lo em `GERAL_PX_POR_MM` (ou no perfil da montagem, em `configuracoes/`).
