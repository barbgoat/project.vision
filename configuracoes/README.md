# Perfis de configuração

Cada ficheiro tem **só os valores que mudam** em relação a `nucleo/defines.py`
(que descreve a câmara da linha). Escolhe-se com `--config`:

```powershell
python main.py --pasta dataset\telemovel --config configuracoes\telemovel.json
python -m nucleo.config --config configuracoes\telemovel.json     # ver todos os valores em uso
```

| Perfil | Para quê | O que muda |
|---|---|---|
| `telemovel.json` | fotos do telemóvel (`dataset/telemovel/`) | `px_por_mm` do telemóvel, cor desligada (o telemóvel ajusta a cor sozinho) |
| `bancada_botao.json` | câmara, fases 1–2 (saco parado), **botão** na Line0 | disparo `foto`, exposição longa, guarda todas as imagens, depuração dos NOK |
| `bancada_virtual.json` | câmara, fases 1–2, **sem botão** | disparo `virtual` (pousar o saco abre a passagem, retirá-lo fecha-a), exposição longa, depuração dos NOK |
| `linha.json` | câmara, fase 3 (tapete a andar), ainda sem sensor | disparo `virtual`; o resto são os defines |

**Valores a medir antes de usar:**
- `telemovel.json` → `px_por_mm`: o 12.4 é um exemplo. Medir com uma régua numa
  foto, à altura do contorno do saco (ver `docs/camara_e_lente.md`).
- `bancada_*.json` → `exposicao_us`: com o saco parado escolhe-se só pelo brilho
  da imagem (até 41 666 µs a 24 FPS).

**Criar um perfil novo:** um JSON com as secções e parâmetros a mudar (os nomes
são os dos defines em minúsculas, sem a secção: `GERAL_PX_POR_MM` →
`{"geral": {"px_por_mm": ...}}`). Gralhas e valores incoerentes dão erro ao
arrancar. O `config.json` na raiz, se existir, é usado automaticamente e não vai
para o git (é para ajustes desta máquina).
