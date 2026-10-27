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