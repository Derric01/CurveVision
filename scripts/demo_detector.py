#!/usr/bin/env python3
"""A real object detector behind CurveVision's HTTP inference contract, for the demo video.

    python scripts/demo_detector.py --port 9100      # POST /infer

It runs **YOLO11n**, Ultralytics' smallest current detector (80 COCO classes), on the CPU
through onnxruntime, so it needs neither a GPU nor PyTorch: only `onnxruntime`, `numpy` and
`Pillow`. None of those is a CurveVision dependency, and the weights are not part of this
repository. They are downloaded on first use from Ultralytics' own release
(`MODEL_URL`) and cached under `~/.cache/curvevision-demo/`. They are AGPL-3.0, Ultralytics'
licence, which is why they are fetched rather than committed.

This is the shape any model server takes (docs/API.md, *Inference contract*): CurveVision
sends the frames as base64 images and gets boxes back, in image pixels, with a confidence
each. Nothing here is CurveVision-specific beyond that contract.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MODEL_URL = "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.onnx"
CACHE = Path.home() / ".cache" / "curvevision-demo" / "yolo11n.onnx"
SIZE = 640
#: Below this, a detection is not sent at all; the run's own threshold then applies.
FLOOR = 0.25
IOU = 0.45


def weights() -> Path:
    if not CACHE.is_file():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {MODEL_URL}")
        urllib.request.urlretrieve(MODEL_URL, CACHE)
    return CACHE


class Yolo:
    def __init__(self) -> None:
        import ast

        import onnxruntime

        self.session = onnxruntime.InferenceSession(
            str(weights()), providers=["CPUExecutionProvider"]
        )
        names = self.session.get_modelmeta().custom_metadata_map["names"]
        self.names: dict[int, str] = ast.literal_eval(names)

    def detect(self, image_bytes: bytes) -> list[dict[str, Any]]:
        import numpy as np
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        width, height = image.size
        # Letterbox: scale to fit 640, pad the rest grey, as the model was trained.
        scale = min(SIZE / width, SIZE / height)
        resized = image.resize((round(width * scale), round(height * scale)), Image.BILINEAR)
        canvas = Image.new("RGB", (SIZE, SIZE), (114, 114, 114))
        left, top = (SIZE - resized.width) // 2, (SIZE - resized.height) // 2
        canvas.paste(resized, (left, top))
        tensor = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0

        output = self.session.run(None, {"images": tensor})[0][0]  # (84, anchors)
        boxes, scores = output[:4].T, output[4:].T
        classes = scores.argmax(axis=1)
        confidence = scores.max(axis=1)
        keep = confidence >= FLOOR
        boxes, classes, confidence = boxes[keep], classes[keep], confidence[keep]

        # Centre and size in letterboxed pixels -> corners in the original image.
        x0 = (boxes[:, 0] - boxes[:, 2] / 2 - left) / scale
        y0 = (boxes[:, 1] - boxes[:, 3] / 2 - top) / scale
        x1 = (boxes[:, 0] + boxes[:, 2] / 2 - left) / scale
        y1 = (boxes[:, 1] + boxes[:, 3] / 2 - top) / scale
        corners = np.stack([x0, y0, x1, y1], axis=1).clip(0, [width, height, width, height])

        found = []
        for index in _nms(corners, confidence, classes):
            found.append(
                {
                    "label": self.names[int(classes[index])],
                    "points": [round(float(v), 1) for v in corners[index]],
                    "confidence": round(float(confidence[index]), 3),
                }
            )
        return found


def _nms(boxes: Any, scores: Any, classes: Any) -> list[int]:
    """Greedy non-maximum suppression, class by class."""
    import numpy as np

    kept: list[int] = []
    for cls in np.unique(classes):
        order = [int(i) for i in np.where(classes == cls)[0]]
        order.sort(key=lambda i: -scores[i])
        while order:
            best = order.pop(0)
            kept.append(best)
            order = [i for i in order if _iou(boxes[best], boxes[i]) < IOU]
    return kept


def _iou(a: Any, b: Any) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def serve(port: int) -> ThreadingHTTPServer:
    model = Yolo()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            shapes = []
            for frame in request.get("frames", []):
                for found in model.detect(base64.b64decode(frame["image"])):
                    shapes.append({"frame": frame["frame"], "type": "rectangle", **found})
            body = json.dumps({"shapes": shapes, "tags": [], "warnings": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: object) -> None:
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


#: The classes this detector can answer with, for registering it as a model.
def labels() -> list[str]:
    return list(Yolo().names.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--try", dest="image", type=Path, help="detect in one image and exit")
    args = parser.parse_args()
    if args.image:
        for found in Yolo().detect(args.image.read_bytes()):
            print(found)
        return 0
    server = serve(args.port)
    print(f"YOLO11n on http://127.0.0.1:{args.port}/infer")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
