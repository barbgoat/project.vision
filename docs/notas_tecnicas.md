# Notas técnicas

- **Iluminação**: o relevo realça-se com luz rasante (de ângulo baixo). Vale a
  pena testar várias posições da luz antes de afinar o software.
- **Movimento**: arrasto (px) ≈ velocidade × exposição × `px_por_mm`. Por
  exemplo, 500 mm/s × 120 µs × 8 px/mm ≈ 0,48 px.
- **Tempo**: o `registo.csv` tem o tempo de cada módulo e o M5 mede o de cada
  etapa (`tempo_<etapa>_ms`). Se não couber nos 500 ms: desfoques grandes numa
  imagem reduzida, menos escalas na Hessiana e, como os escravos de inspeção
  são independentes, corrê-los em paralelo.
- **Impressão**: os contornos da impressão também são cristas e podem dar
  falsos defeitos no M5 e no M6. Na v1, escolher um canal onde a tinta tenha
  pouco contraste, ou excluir essa zona.
- **Caminhos com acentos**: usar `nucleo.imagens.ler_imagem` e `guardar_imagem`
  em vez de `cv2.imread`/`cv2.imwrite`. O `cv2.putText` não desenha acentos
  (usar `nucleo.imagens.texto_ascii`).
- **Correção do ângulo no M2**: ao desenvolver o M3, o ângulo do saco passou a
  ser afinado pelos lados compridos (antes, um canto dobrado ou uma aba
  inclinavam o saco ~0,8° no recorte; agora o erro é de 0,01°).
