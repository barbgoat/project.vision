# Dataset

Uma pasta por **montagem** (cada uma tem a sua escala, `px_por_mm`) e, dentro,
uma pasta por **classe** (resumo em `dataset/README.md`):

```
dataset/
├─ telemovel/      fotos do telemóvel     → usar com configuracoes/telemovel.json
│   ├─ ok/         sacos sem defeitos
│   ├─ dimensao/ cor/ risco/ dobra/ rugas/ amasso/ mancha/   um tipo de defeito por pasta
│   └─ varios/     sacos com mais do que um defeito
└─ blackfly/       imagens da câmara da linha → usar com configuracoes/bancada_*.json ou linha.json
    └─ (as mesmas classes)
```

- O nome da pasta é a classe real do saco: `ok/` é OK, todas as outras são NOK.
- Nunca misturar montagens na mesma pasta: as medidas em mm ficariam erradas.
- **As imagens não vão para o git** (ver `.gitignore`); a estrutura de pastas e
  as anotações vão. Para guardar as imagens no repositório, usar o Git LFS.

## Anotações

Recomendado; obrigatório em `varios/`. Para avaliar se cada defeito foi
encontrado no sítio certo, cada imagem pode ter ao lado um `.json` com o mesmo
nome (`saco_007.jpg` → `saco_007.json`), feito no
[LabelMe](https://github.com/wkentaro/labelme). Rótulo = tipo do defeito;
linhas com *linestrip*, zonas e manchas com *polygon*, defeitos do saco todo
(dimensão, cor) com *rectangle*. O LabelMe instala-se fora do `.venv` do
projeto (p.ex. `pipx install labelme`).

## Como fotografar

- **Telemóvel:** em JPG (o OpenCV não lê HEIC), telemóvel num suporte a altura
  fixa, régua numa das fotos para calibrar o `px_por_mm` (pô-lo em
  `configuracoes/telemovel.json`), fundo uniforme, saco inteiro na foto, sem
  flash nem HDR, exposição e foco bloqueados. O M4 Cor não é fiável com
  telemóvel (balanço de brancos automático), por isso está desligado nesse perfil.
- **Régua à altura do contorno do saco**, não pousada no fundo: com uma lente
  grande-angular, o que está mais alto parece maior (ver [camara_e_lente.md](camara_e_lente.md)).
- Tirar uma foto do **fundo vazio** em cada sessão (mesma posição e luz) e
  indicá-la em `LOCALIZACAO_FUNDO_REFERENCIA` (ver [m2_localizacao.md](m2_localizacao.md)).
- Variar orientações, posições e intensidade dos defeitos; incluir muitos
  sacos OK, porque são eles que medem as falsas rejeições.
- Guardar à parte algumas imagens que nunca se usam para afinar.

## Avaliar

```powershell
python -m ferramentas.avaliar --dataset dataset\telemovel --config configuracoes\telemovel.json --guardar-erros
```

(esqueleto por agora: métricas por saco, por módulo e por defeito).
