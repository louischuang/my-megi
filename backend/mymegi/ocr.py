import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import monotonic


class OcrError(Exception):
    def __init__(self, code: str, message: str, metadata: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.metadata = metadata or {}


@dataclass(frozen=True)
class OcrResult:
    text: str
    metadata: dict


def run_tesseract_ocr(file_path: Path, mime_type: str, languages: str = "eng+chi_tra") -> OcrResult:
    if mime_type == "application/pdf":
        raise OcrError(
            "UNSUPPORTED_FILE_TYPE",
            "PDF OCR is not supported in the current local Tesseract pipeline.",
            {"mimeType": mime_type},
        )

    if not file_path.exists():
        raise OcrError("FILE_NOT_FOUND", "Uploaded card file does not exist.")

    started = monotonic()
    command = ["tesseract", str(file_path), "stdout", "-l", languages]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    except subprocess.TimeoutExpired as exc:
        raise OcrError(
            "OCR_TIMEOUT",
            "OCR processing exceeded the 60 second timeout.",
            {"timeoutSeconds": 60},
        ) from exc

    elapsed_ms = round((monotonic() - started) * 1000)
    metadata = {
        "engine": "tesseract",
        "languages": languages,
        "elapsedMs": elapsed_ms,
        "command": command[:1] + ["<input>", "stdout", "-l", languages],
    }

    if result.returncode != 0:
        raise OcrError(
            "OCR_FAILED",
            result.stderr.strip() or "Tesseract failed to process the uploaded file.",
            metadata | {"returnCode": result.returncode},
        )

    text = result.stdout.strip()
    return OcrResult(text=text, metadata=metadata | {"textLength": len(text)})

