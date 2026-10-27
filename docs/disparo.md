# Disparo (trigger): uma passagem = um saco

Com a câmara a captar a vários FPS, o mesmo saco aparece em várias imagens,
desde que entra até que sai do campo de visão. O M1 junta essas imagens numa
**passagem** e o Mestre dá **uma só decisão por saco**:

```
imagens a 24 FPS:  . . . [ s s S S S s s ] . . . [ s s S S S s s ] . . .
                         └── passagem 1 ──┘       └── passagem 2 ──┘
                         S = saco inteiro e perto do centro (candidatas)
```

1. **Disparo** (`modulos/m1_aquisicao/disparo.py`): decide quando começa e
   acaba cada passagem e guarda as melhores imagens (as `DISPARO_CANDIDATAS`
   com o saco mais centrado e inteiro).
2. **Mestre** (`inspecionar_passagem`): experimenta as candidatas pela ordem até
   o M2 encontrar o saco completo e analisa essa imagem. Se nenhuma tiver o
   saco inteiro, o saco fica em ERRO: um saco que passou sem ser inspecionado
   nunca é aprovado.
3. **Aquisição em paralelo** (`em_paralelo.py`): uma thread capta o saco
   seguinte enquanto o anterior é analisado. Se a análise se atrasar mais do
   que `DISPARO_FILA_MAX` sacos, a passagem é descartada e registada como ERRO
   ("não analisada"): nunca se perde um saco em silêncio.

## Modos (`DISPARO_MODO`, ou `--disparo` no `main.py`)

| Modo | Quando usar | Como separa os sacos |
|---|---|---|
| `foto` | fotos do telemóvel, uma por saco; ou a câmara com um **botão** (ou o sensor) na Line0 | cada imagem é uma passagem; os saltos do botão são ignorados |
| `sensor` | **na linha**: sensor de presença ligado à entrada Line0 da Blackfly S | as imagens com o sensor ativo |
| `virtual` | vídeos do telemóvel, webcam, ou câmara sem sensor | o saco a atravessar uma faixa estreita no centro da imagem (barreira ótica virtual) |

No modo `sensor` há duas estratégias (`DISPARO_SENSOR_ESTRATEGIA`):
- `janela`: a câmara capta em contínuo a `DISPARO_FPS` e grava em cada imagem o
  estado do sensor (chunk data `ExposureEndLineStatusAll`); a passagem são as
  imagens com o sensor ativo. Alinhar o sensor com o centro do campo de visão.
- `rajada`: cada ativação do sensor dispara `DISPARO_RAJADA_IMAGENS` imagens
  (`FrameBurstStart`), depois de `DISPARO_RAJADA_ATRASO_US`. Para o saco ficar
  centrado a meio da rajada:
  atraso ≈ (distância do sensor ao centro do campo + comprimento do saco / 2) / velocidade − (N − 1) / (2 · FPS).

A ligação do botão ou do sensor à câmara está em [camara_e_lente.md](camara_e_lente.md).

## Quantos FPS?

Imagens com o saco **inteiro** à vista ≈ FPS × (campo de visão − comprimento do
saco) / velocidade do tapete. Tem de dar pelo menos 1 (melhor 3 ou mais).
Exemplo com esta câmara: 24 FPS × (306 − 150 mm) / 500 mm/s ≈ 7 imagens. Se o campo de visão
for pouco maior do que o saco, é preciso mais FPS (ou um campo de visão maior).
A exposição tem de caber em 1/FPS.

## Testar já, sem câmara

Filmar sacos a passar com o telemóvel (em H.264, "Mais compatível" no iPhone),
**começando com o tapete vazio** (as primeiras imagens ensinam o fundo), e:

```powershell
python -m ferramentas.testar_disparo --video "D:\Vídeos\linha.mp4" --guardar resultados\disparo
python main.py --fonte video --video "D:\Vídeos\linha.mp4" --disparo virtual --config configuracoes\telemovel.json
```

O `testar_disparo` mostra cada passagem (duração, nº de imagens, candidatas e a
distância do saco ao centro em cada uma) e grava as candidatas. Confirmar: uma
passagem por saco e a 1.ª candidata com o saco inteiro e centrado. Acertar
`DISPARO_VIRTUAL_DIRECAO` (sentido do tapete na imagem) e `GERAL_PX_POR_MM`.

Testes com uma linha sintética (6 sacos de cores diferentes, 100 mm entre
sacos, a ponta do seguinte já à vista, 24 FPS): 6 passagens de 9 imagens; 1.ª
candidata com o saco a 0–5 % do centro; o M2 mede-o com 150,0–150,2 × 55,0–55,1 mm
(real 150 × 55). Sacos encostados não se separam (como com um sensor físico)
e o Mestre não os aprova. Estes casos estão em `testes/test_m1_disparo.py`.
