"""Run at boot; measurements are requested by the USB host, never queued."""

import json
import sys
import time
from machine import I2C, Pin

from protocol import PROTOCOL
from sensors import Sensors


sensors = Sensors(I2C(0, sda=Pin(0), scl=Pin(1), freq=100_000))
while True:
    line = sys.stdin.readline()
    if not line:
        time.sleep(0.1)
        continue
    request_id = None
    try:
        request = json.loads(line)
        if (not isinstance(request, dict) or set(request) != {"command", "request_id"}
                or request["command"] != "measure" or not isinstance(request["request_id"], str)
                or not request["request_id"]):
            raise ValueError("invalid request")
        request_id = request["request_id"]
    except ValueError:
        print(json.dumps({"protocol": PROTOCOL, "request_id": request_id, "error": "invalid_request"}))
        continue
    print(json.dumps({"protocol": PROTOCOL, "request_id": request_id, "samples": sensors.measure()}))
