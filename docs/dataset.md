# Dataset

```
dataset/
├─ telemovel/    fotos do telemóvel   → --config configuracoes\telemovel.json
└─ blackfly/     imagens da câmara    → --config configuracoes\bancada_*.json ou linha.json

Em cada uma:  ok/   dimensao/ cor/ risco/ dobra/ rugas/ amasso/ mancha/ (um defeito)   varios/
```

- A pasta é a classe real: `ok/` é OK, as outras são NOK.
- Nunca misturar montagens: cada uma tem o seu `px_por_mm`.
- As imagens não vão para o git (só as pastas e as anotações); para as guardar, Git LFS.
- **Anotações** (obrigatórias em `varios/`): um `.json` do [LabelMe](https://github.com/wkentaro/labelme)
  com o nome da imagem; rótulo = tipo do defeito.

## Como fotografar (telemóvel)

- JPG (não HEIC), telemóvel num suporte a altura fixa, sem flash nem HDR,
  exposição e foco bloqueados, fundo uniforme, saco inteiro na foto.
- Uma foto com uma **régua à altura do saco** → `px_por_mm` em `configuracoes\telemovel.json`.
- Uma foto do **fundo vazio** por sessão → `LOCALIZACAO_FUNDO_REFERENCIA`.
- Variar posição, rotação e intensidade dos defeitos; muitos sacos OK; guardar
  algumas imagens à parte, nunca usadas para afinar.
