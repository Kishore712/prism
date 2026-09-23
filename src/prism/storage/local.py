from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from ..exceptions import CaptureReadError, CaptureWriteError, NotFoundError
from ..models.capture import CapturedSession
from ..protocols.capture_store import StoredCapture


class LocalCaptureStore:
    """Persists validated captures atomically beneath one configured root."""

    _capture_id_pattern = re.compile(r"^cap_[0-9a-f]{26}$")

    def __init__(self, data_dir: Path) -> None:
        self._root = data_dir.expanduser().resolve() / "captures"

    def save(self, capture: CapturedSession) -> StoredCapture:
        if not self._capture_id_pattern.fullmatch(capture.capture_id):
            raise CaptureWriteError("The generated capture ID is not safe for local persistence")
        capture_dir = self._root / capture.capture_id
        target = capture_dir / "capture.json"
        temporary: Path | None = None
        try:
            self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
            capture_dir.mkdir(mode=0o700)
            os.chmod(self._root, 0o700)
            os.chmod(capture_dir, 0o700)
            descriptor, temp_name = tempfile.mkstemp(
                prefix=".capture-",
                suffix=".tmp",
                dir=capture_dir,
            )
            temporary = Path(temp_name)
            os.chmod(temporary, 0o600)
            payload = capture.model_dump(mode="json")
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            os.chmod(target, 0o600)
            return StoredCapture(artifact_path=target)
        except (OSError, TypeError, ValueError) as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            try:
                if capture_dir.exists() and not any(capture_dir.iterdir()):
                    capture_dir.rmdir()
            except OSError:
                pass
            raise CaptureWriteError(
                "The canonical capture could not be written atomically"
            ) from exc

    def load(self, capture_id: str) -> CapturedSession:
        if not self._capture_id_pattern.fullmatch(capture_id):
            raise NotFoundError("The requested capture does not exist")
        target = self._root / capture_id / "capture.json"
        try:
            payload = target.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise NotFoundError("The requested capture does not exist") from exc
        except OSError as exc:
            raise CaptureReadError("The canonical capture could not be read") from exc
        try:
            return CapturedSession.model_validate_json(payload)
        except PydanticValidationError as exc:
            raise CaptureReadError("The stored capture failed schema validation") from exc
