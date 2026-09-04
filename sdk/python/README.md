# CurveVision Python SDK & CLI

Typed Python client and command-line tool for [CurveVision](https://github.com/Derric01/CurveVision).

```bash
pip install curvevision-sdk
```

## SDK

```python
from curvevision_sdk import CurveVision

with CurveVision("http://localhost:8000", token="cv_...") as cv:
    org = cv.organizations()[0]

    project = cv.create_project(
        org.id,
        slug="street-scenes",
        name="Street Scenes",
        labels=[{"name": "car", "color": "#ef4444"}, {"name": "pedestrian"}],
    )

    task = cv.create_task(project.id, name="Batch 1", segment_size=200)
    cv.upload(task.id, ["frames/000.jpg", "frames/001.jpg"])

    job = cv.jobs(task_id=task.id)[0]
    cv.create_shapes(
        job.id,
        [
            {
                "label_id": str(project.label_id("car")),
                "frame": 0,
                "shape_type": "rectangle",
                "points": [10, 20, 110, 220],
            }
        ],
    )

    cv.export(project.id, format="yolo", destination="dataset.zip")
```

Every method returns typed objects with the raw response available on `.raw`, so an SDK
built against an older server keeps working as the API grows.

Errors raise `CurveVisionError`, which carries the server's RFC 9457 problem document —
including field-level validation errors on `.errors`.

## CLI

```bash
curvevision login https://curvevision.example.com --username alice
curvevision projects
curvevision task create <project-id> --name "Batch 1" --upload ./images/*.jpg
curvevision task progress <task-id>
curvevision job list --mine
curvevision export <project-id> --format yolo --output dataset.zip
curvevision import <task-id> annotations.zip --format coco --replace
```

`login` mints a long-lived API token and stores it in
`~/.config/curvevision/config.json` with `0600` permissions. `CURVEVISION_URL` and
`CURVEVISION_TOKEN` override the stored configuration, which is what CI should use.

## License

MIT — see [LICENSE](../../LICENSE).
