import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from PIL import Image, ImageOps


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


@dataclass(frozen=True)
class OcrAttempt:
    text: str
    score: int
    metadata: dict


def score_ocr_text(text: str) -> int:
    compact = "".join(text.split())
    cjk_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    digit_count = sum(1 for char in text if char.isdigit())
    email_bonus = 160 if "@" in text else 0
    phone_bonus = 120 if digit_count >= 8 else 0
    company_bonus = 80 if "Ltd" in text or "公司" in text or "Co." in text else 0
    return len(compact) + cjk_count * 3 + digit_count + email_bonus + phone_bonus + company_bonus


def preprocess_image(source: Path, destination: Path, rotation: int) -> None:
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        image = image.convert("L")
        image = ImageOps.autocontrast(image)
        if max(image.size) < 2200:
            image = image.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
        if rotation:
            image = image.rotate(rotation, expand=True, fillcolor=255)
        image.save(destination)


def run_tesseract_command(image_path: Path, languages: str) -> tuple[str, str, int, int]:
    started = monotonic()
    command = ["tesseract", str(image_path), "stdout", "-l", languages, "--psm", "6"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    elapsed_ms = round((monotonic() - started) * 1000)
    return result.stdout.strip(), result.stderr.strip(), result.returncode, elapsed_ms


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
    attempts: list[OcrAttempt] = []
    errors: list[dict] = []

    try:
        with TemporaryDirectory(prefix="mymegi-ocr-") as temp_dir:
            temp_path = Path(temp_dir)
            for rotation in (0, 90, 180, 270):
                prepared_path = temp_path / f"card-{rotation}.png"
                preprocess_image(file_path, prepared_path, rotation)
                text, stderr, returncode, elapsed_ms = run_tesseract_command(prepared_path, languages)
                attempt_metadata = {
                    "rotation": rotation,
                    "elapsedMs": elapsed_ms,
                    "returnCode": returncode,
                    "textLength": len(text),
                    "score": score_ocr_text(text),
                }
                if stderr:
                    attempt_metadata["stderr"] = stderr
                if returncode == 0:
                    attempts.append(
                        OcrAttempt(
                            text=text,
                            score=attempt_metadata["score"],
                            metadata=attempt_metadata,
                        )
                    )
                else:
                    errors.append(attempt_metadata)
    except subprocess.TimeoutExpired as exc:
        raise OcrError(
            "OCR_TIMEOUT",
            "OCR processing exceeded the 60 second timeout.",
            {"timeoutSeconds": 60},
        ) from exc
    except OSError as exc:
        raise OcrError(
            "IMAGE_PREPROCESS_FAILED",
            "Failed to preprocess uploaded image for OCR.",
            {"error": str(exc)},
        ) from exc

    elapsed_ms = round((monotonic() - started) * 1000)
    if not attempts:
        raise OcrError(
            "OCR_FAILED",
            "Tesseract failed to process the uploaded file.",
            {"engine": "tesseract", "languages": languages, "elapsedMs": elapsed_ms, "errors": errors},
        )

    best = max(attempts, key=lambda attempt: attempt.score)
    return OcrResult(
        text=best.text,
        metadata={
            "engine": "tesseract",
            "languages": languages,
            "elapsedMs": elapsed_ms,
            "selectedRotation": best.metadata["rotation"],
            "selectedScore": best.score,
            "attempts": [attempt.metadata for attempt in attempts],
            "errors": errors,
            "command": ["tesseract", "<preprocessed-input>", "stdout", "-l", languages, "--psm", "6"],
            "textLength": len(best.text),
        },
    )
