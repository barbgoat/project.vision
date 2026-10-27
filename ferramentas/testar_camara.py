"""Teste rápido da câmara, sem o resto do programa: liga-se, tira uma imagem e grava-a.

    python ferramentas\\testar_camara.py

Mostra a versão do Spinnaker e quantas câmaras encontra, e grava frame.png na
pasta atual. A imagem sai em bruto (Bayer, por isso parece cinzenta com um
padrão fino): a conversão para cores fica a cargo da FonteSpinnaker
(modulos/m1_aquisicao/fontes.py), que vai substituir este teste.
"""

import PySpin
import cv2

system = PySpin.System.GetInstance()
v = system.GetLibraryVersion()
print(f"Spinnaker {v.major}.{v.minor}.{v.type}.{v.build}")

cams = system.GetCameras()
print("Câmaras encontradas:", cams.GetSize())

if cams.GetSize() > 0:
    cam = cams[0]
    cam.Init()
    cam.BeginAcquisition()
    img = cam.GetNextImage(2000)  # timeout em ms
    if not img.IsIncomplete():
        frame = img.GetNDArray()
        cv2.imwrite("frame.png", frame)
        print("Imagem gravada:", frame.shape)
    img.Release()
    cam.EndAcquisition()
    cam.DeInit()
    del cam

cams.Clear()
system.ReleaseInstance()