import os
import cv2
from math import ceil
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
from matplotlib.figure import Figure
import base64
import argparse
import json
from io import BytesIO
from ipynb.fs.full.Observer import cm, cameras, pov, Camera, CalibrationBox

import threading
import atexit
from flask import Flask, render_template, Response, request, make_response
from traceback import format_exc
from ultralytics import YOLO

CONSOLE_OUTPUT = "No Output Yet"


humanInferenceModel = YOLO("yolov8n.pt")


POOL_TIME = 0.1 #Seconds
PORT = int(os.getenv("OBSERVER_PORT", "7000"))

data_lock = threading.Lock()

captureTimer = threading.Timer(0,lambda x: None,())    
def createCaptureApp():
    app = Flask(__name__)

    def interrupt():
        global captureTimer
        captureTimer.cancel()

    def cycleMachine():
        with data_lock:
            cm.cycle()
        # Set the next timeout to happen
        captureTimer = threading.Timer(POOL_TIME, cycleMachine, ())
        captureTimer.start()   

    def initialize():
        global captureTimer
        captureTimer = threading.Timer(POOL_TIME, cycleMachine, ())
        captureTimer.start()

    initialize()
    # When you kill Flask (SIGTERM), cancels the timer
    atexit.register(interrupt)
    return app


observerApp = createCaptureApp()


def imageToBase64(img):
    return base64.b64encode(cv2.imencode('.jpg', img)[1]).decode()

def renderConsole():
    while True:
        cam = list(cameras.values())[0]
        shape = (400, 400)
        mid = [int(d / 2) for d in shape]
        zeros = np.zeros(shape, dtype="uint8")
        consoleImage = cv2.putText(zeros, f'CapMac--{cm.state}',
            (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.7, 255, 2, cv2.LINE_AA)
        consoleImage = cv2.putText(zeros, f'ID: {cm.interactionDetected} || DC: {cm.debounceCounter}',
            (50, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.7, 255, 2, cv2.LINE_AA)     
        ret, consoleImage = cv2.imencode('.jpg', consoleImage)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpg\r\n\r\n' + consoleImage.tobytes() + b'\r\n')


@observerApp.route('/console', methods=['GET'])
def getConsoleImage():
    return Response(renderConsole(), mimetype='multipart/x-mixed-replace; boundary=frame')


@observerApp.route('/bootstrap.min.css', methods=['GET'])
def getBSCSS():
    with open("templates/bootstrap.min.css", "r") as f:
        bscss = f.read()
    return Response(bscss, mimetype="text/css")


@observerApp.route('/bootstrap.min.js', methods=['GET'])
def getBSJS():
    with open("templates/bootstrap.min.js", "r") as f:
        bsjs = f.read()
    return Response(bsjs, mimetype="application/javascript")


def genCameraFullViewWithActiveZone(camNum):
    while True:
        cam = cameras[int(camNum)]
        camImage = cam.drawActiveZone(cm.cc.drawObjectsOnCam(cam))
        fig = Figure()
        axis = fig.add_subplot(1, 1, 1)
        axis.imshow(camImage)
        camFrame = BytesIO()
        FigureCanvas(fig).print_png(camFrame)
        camFrame.seek(0)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpg\r\n\r\n' + camFrame.read() + b'\r\n')
    
    
@observerApp.route('/config/camera/<camNum>')
def cameraActiveZoneWithObjects(camNum):
    return Response(genCameraFullViewWithActiveZone(int(camNum)), mimetype='multipart/x-mixed-replace; boundary=frame')
    return response


def buildConfigurator():
    cameraConfigRows = []
    clickSubs = []
    for cam in cameras.values():
        activeZone = json.dumps(cam.activeZone.tolist())
        currentCalibration = f"Calibrated to: {cam.MCalibratedTo}" if cam.M is not None else "Not Calibrated"
        cameraConfigRows.append(f"""
            <div class="row">
                <div class="col-lg-8  offset-lg-2">
                    <h3 class="mt-5">Camera {cam.camNum}</h3>
                    <div>
                        <img src="/config/camera/{cam.camNum}" title="{cam.camNum} Capture" width="100%" height="500" id="cam{cam.camNum}" onclick="cam{cam.camNum}ClickListener(event)"></iframe>
                    </div>
                    <form method="post">
                      <label for="min_width">Minimum Width of Tracked Objects</label><br>
                      <input type="number" id="min_width" name="min_width" value="{cam.minimumWidth}"><br>
                      <label for="az">Active Zone</label><br>
                      <input type="text" name="az" id="cam{cam.camNum}_ActiveZone" value="{activeZone}" size="50"><br>
                      <input type="hidden" id="camNum" name="camNum" value="{cam.camNum}">
                      <input type="submit" value="Update Cam {cam.camNum}">
                      <input type="hidden" id="configType" name="configType" value="activeZone">
                    </form>
                    <hr>
                    {currentCalibration}
                    <form method="post"
                        <label>Calibration Coordinates:&nbsp;&nbsp;(</label>
                        <label for="calibrate_x">X:&nbsp;&nbsp;</label>
                        <input type="number" id="calibrate_x" name="calibrate_x" placeholder="0.0" min="0" max="10000">
                        <label for="calibrate_y">, Y:&nbsp;&nbsp;</label>
                        <input type="number" id="calibrate_y" name="calibrate_y" placeholder="0.0" min="0" max="10000">&nbsp;&nbsp;)</p>
                        <input type="hidden" id="camNum" name="camNum" value="{cam.camNum}">
                        <input type="hidden" id="configType" name="configType" value="calibrate">
                        <input type="submit" value="Calibrate Cam {cam.camNum}">
                    </form>
                </div>
            </div>""")
        clickSubs.append(f"""
            function cam{cam.camNum}ClickListener(event) {{
                const imgElem = document.getElementById("cam{cam.camNum}")
                bounds=imgElem.getBoundingClientRect();
                const left=bounds.left;
                const top=bounds.top;
                const x = event.x - left;
                const y = event.y - top;
                const cw=imgElem.clientWidth
                const ch=imgElem.clientHeight
                const iw=imgElem.naturalWidth
                const ih=imgElem.naturalHeight
                const px=x/cw*iw
                const py=y/ch*ih
                console.log(px, px)
                const x_offset = 80
                const x_scale = 1800 / 480
                const image_x = (px - x_offset) * x_scale
                const y_offset = 100
                const y_scale = 1080 / 280
                const image_y = (py - y_offset) * y_scale
                const formField = document.getElementById("cam{cam.camNum}_ActiveZone")
                var formValue = JSON.parse(formField.value)
                formValue.push([~~image_x, ~~image_y])
                formField.value = JSON.stringify(formValue)
            }}""")
    with open("templates/Configuration.html") as f:
        template = f.read()
    return template.replace("{cameraConfigRows}", "\n".join(cameraConfigRows)).replace("{clickSubscriptions}", "\n".join(clickSubs))

    
@observerApp.route('/config', methods=['GET'])
def config():
    cm.cc.capture()
    return buildConfigurator()
    
    
@observerApp.route('/config', methods=['POST'])
def updateConfig():
    global CONSOLE_OUTPUT
    CONSOLE_OUTPUT = ""
    print(f"Received update config request")
    
    if (config_type := request.form.get("config_type")) == 'save_state':
        cm.cc.saveState()
        CONSOLE_OUTPUT = "State Saved!"
        print("State Saved!")
    elif config_type == 'capture_cameras':
        cm.cc.capture()
        CONSOLE_OUTPUT = "Cameras Captured"
    elif config_type == 'load_state':
        cm.cc.recoverState()
        CONSOLE_OUTPUT = "State Recovered!"
        print("State Recovered!")
    else:
        camNum = int(request.form.get("camNum"))
        if (configType := request.form.get("configType")) == "activeZone":
            az = np.float32(json.loads(request.form.get(f"az")))
            min_width = float(request.form.get("min_width"))

            cam = cameras[camNum]
            cam.setActiveZone(az)
            cam.minimumWidth = min_width
        elif configType == "calibrate":
            xPosition = float(request.form.get('calibrate_x'))
            yPosition = float(request.form.get('calibrate_y'))
            try:
                qrCodes = cameras[camNum].calibrate((xPosition, yPosition))
                CONSOLE_OUTPUT = f"Camera {camNum} Calibrated to ({xPosition}, {yPosition}). Saw {qrCodes} calibration boxes"
            except Exception as e:
                print(f"Failed Calibration: {e}")
                CONSOLE_OUTPUT = f"Failed Calibration: {e}"

    print(f"Rebuilding configurator")
    return buildConfigurator()


def changeBetween(cam, im0, im1):
    if im0 is None or im1 is None:
        return []
    img_height = im0.shape[0]
    diff = cv2.absdiff(cv2.cvtColor(im0, cv2.COLOR_BGR2GRAY),
                       cv2.cvtColor(im1, cv2.COLOR_BGR2GRAY))
    thresh = cv2.threshold(diff,64,255,cv2.THRESH_BINARY)[1]
    kernel = np.ones((5,5), np.uint8) 
    dilate = cv2.dilate(thresh, kernel, iterations=2)
    contours, _ = cv2.findContours(dilate.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    
    activeZoneZeroMask = np.zeros((im0.shape[:2]), dtype="uint8")
    activeZoneMask = cv2.fillPoly(activeZoneZeroMask, [np.array(cam.activeZone, np.int32)], 255)
    
    for contour in contours:
        bRect = cv2.boundingRect(contour)
        x, y, w, h = bRect
        area = w * h
        presumedBasePoint = cam.boxToBasePoint((x, y, w, h))
        if area > 10000 and cam.pointInActiveZone(presumedBasePoint):
            boxes.append(cv2.boundingRect(contour))
    return contours, boxes


def genCameraActiveZoneWithObjectsAndDeltas(camNum):
    while True:
        cam = cameras[camNum]
        boxes, contours, clips = cam.changeSet()
        camImage = cam.maskFrameToActiveZone(cam.mostRecentFrame.copy())
        camImage = cam.drawBoxesOnImage(camImage, boxes, (255, 100, 0))
        camImage = cv2.drawContours(camImage, contours, -1, (100, 0, 100), 5)
        
        if cm.interactionDetected:
            camImage = cv2.line(camImage, (0, 0), (1800, 1080), (0, 0, 255), 20)
            camImage = cv2.line(camImage, (0, 1080), (1800, 0), (0, 0, 255), 20)
        else:
            camImage = cv2.line(camImage, (0, 3), (1800, 3), (0, 255, 0), 50)
        
        ret, camImage = cv2.imencode('.jpg', camImage)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpg\r\n\r\n' + camImage.tobytes() + b'\r\n')


@observerApp.route('/control/camera/<camNum>')
def cameraActiveZoneWithObjectsAndDeltas(camNum):
    return Response(genCameraActiveZoneWithObjectsAndDeltas(int(camNum)), mimetype='multipart/x-mixed-replace; boundary=frame')


@observerApp.route('/control')
def buildController():
    with open("templates/Controller.html", "r") as f:
        template = f.read()

    cameraCaptures = '<div class="container" width="100%">\n' + "".join([f"""
        <div class="row" width="100%">
            <div class="col">
                <h3 class="mt-5">Camera {cam.camNum}</h3>
                <img src="/control/camera/{cam.camNum}" title="{cam.camNum} Capture" width="100%">
            </div>
        </div>
    """ for cam in cameras.values()]) + "</div>"
    template = template.replace("{cameraCaptures}", cameraCaptures)
    return template


@observerApp.route('/changes')
def buildChanges():
    keys = sorted(list(cm.changes.keys()), reverse=True)
    changeRows = []
    for changeKey in keys:
        changeBoxes = {camNum: changeSet[0] for camNum, changeSet in cm.changes[changeKey].items()}
        changeImages = {camNum: cm.cc.hStackImages(changeSet[2]) for camNum, changeSet in cm.changes[changeKey].items()}
        cameraClips = "".join([f"""
            <div class="col">
                <img alt="{changeKey}-{camNum} Changes" src="data:image/png;base64,{imageToBase64(image)}" />
            </div>
        """ for camNum, image in changeImages.items()])
        changeRow = f"""
            <div class="row">
                <div class="col-1">
                    <p>Cycle {changeKey}</p>
                </div>
                <div class="col-2">
                    <p>Change Boxes: {changeBoxes}</p>
                </div>
                <div class="col-6">
                    <div class="container">
                        <div class="row">
                            {cameraClips}
                        </div>
                    </div>
                </div>
            </div>"""
        changeRows.append(changeRow)
    
    topRow = """
        <div class="row" width="100%">
            <div class="container justify-content-center">
                <div class="row">
                    <div class="col-1">
                        <p>Change Cycle<p>
                    </div>
                    <div class="col-2">
                        <p>Object Coordinates<p>
                    </div>
                    <div class="col-6">
                        <div class="container">
                            <div class="row">
                               {cameras}
                            </div>
                        </div>
                    </div>
                </div>
                {changeRows}
            </div>
        </div>""".format(
        cameras="".join([f"""
                <div class="col">
                    <p>Camera {camNum}<p>
                </div>""" for camNum in cameras.keys()]),
        changeRows="".join(changeRows)
    )
    with open("templates/ChangeTable.html", "r") as f:
        template = f.read()
    return template.replace("{changeTableBody}", topRow)
    


if __name__ == "__main__":
    cm.cycle()
    print(f"Launching Observer Server on {PORT}")
    observerApp.run(host="0.0.0.0", port=PORT)