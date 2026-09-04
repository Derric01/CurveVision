"""CurveVision Python SDK.

```python
from curvevision_sdk import CurveVision

with CurveVision("http://localhost:8000", token="cv_...") as cv:
    project = cv.project(project_id)
    for job in cv.jobs(task_id=task.id):
        print(job.index, job.state, cv.annotations(job.id)["shapes"])
```
"""

from curvevision_sdk.client import (
    CurveVision,
    CurveVisionError,
    Job,
    Organization,
    Project,
    Resource,
    Task,
)

__version__ = "0.1.0"

__all__ = [
    "CurveVision",
    "CurveVisionError",
    "Job",
    "Organization",
    "Project",
    "Resource",
    "Task",
    "__version__",
]
