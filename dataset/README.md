# Dataset

Uma pasta por **montagem** (cada uma tem a sua escala, `px_por_mm`) e, dentro,
uma pasta por **classe**:

```
dataset/
├─ telemovel/      fotos do telemóvel     → usar com configuracoes/telemovel.json
│   ├─ ok/         sacos sem defeitos
│   ├─ dimensao/ cor/ risco/ dobra/ rugas/ amasso/ mancha/   um tipo de defeito por pasta
│   └─ varios/     sacos com mais do que um defeito (anotações obrigatórias)
└─ blackfly/       imagens da câmara da linha → usar com configuracoes/bancada_*.json ou linha.json
    └─ (as mesmas classes)
```

Nunca misturar montagens na mesma pasta: as medidas em mm ficariam erradas.

```powershell
python main.py --pasta dataset\telemovel --config configuracoes\telemovel.json --depurar nok
```

- O nome da pasta é a classe real do saco: `ok/` é OK, todas as outras são NOK.
- **Anotações** (recomendado; obrigatório em `varios/`): um `.json` do
  [LabelMe](https://github.com/wkentaro/labelme) com o mesmo nome da imagem;
  rótulo = tipo do defeito; linhas com *linestrip*, zonas com *polygon*.
- **As imagens não vão para o git** (podem ser vários GB; ver `.gitignore`); a
  estrutura de pastas e as anotações vão. Para guardar as imagens no
  repositório, usar o Git LFS.
- Como fotografar: ver `docs/dataset.md`.
