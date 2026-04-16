from __future__ import annotations

import subprocess

from ..config import StudentSettings
from ..models import StudentResponse


class LocalStudentRunner:
    def __init__(self, settings: StudentSettings) -> None:
        self.settings = settings

    def suggest(self, prompt: str) -> StudentResponse:
        command = [
            *self.settings.generate_command,
            "--model",
            self.settings.model_id,
            "--prompt",
            prompt,
            "--max-tokens",
            str(self.settings.max_tokens),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            shell=False,
            check=False,
            timeout=self.settings.timeout_seconds,
        )
        if completed.returncode != 0:
            return StudentResponse(text=None, raw={"stderr": completed.stderr, "returncode": completed.returncode})
        return StudentResponse(text=completed.stdout.strip(), raw={"stdout": completed.stdout})


class StaticStudent:
    def __init__(self, text: str | None = None) -> None:
        self.text = text

    def suggest(self, prompt: str) -> StudentResponse:
        return StudentResponse(text=self.text, raw={"prompt": prompt})
