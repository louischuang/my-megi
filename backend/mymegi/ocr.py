from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
import subprocess
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
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    short_line_count = sum(1 for line in lines if len(line) <= 2)
    symbol_count = sum(1 for char in text if not char.isalnum() and not char.isspace() and char not in "@.-:/：")
    cjk_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    digit_count = sum(1 for char in text if char.isdigit())
    valid_email_bonus = 700 if re.search(r"[\w.+-]+\s*\(?@\s*[\w.-]+\.[A-Za-z]{2,}", text) else 0
    phone_bonus = 260 if re.search(r"\b0\d{1,3}[-\s]?\d{6,8}\b", text) else 0
    mobile_bonus = 260 if re.search(r"09\d{2}[-\s]?\d{3}[-\s]?\d{3}", text) else 0
    contact_label_bonus = sum(
        90
        for label in ("電話", "電", "TEL", "手機", "機", "E-mail", "mail", "統編")
        if label in text
    )
    org_bonus = 180 if any(token in text for token in ("University", "大學", "公司", "Ltd", "Co.")) else 0
    address_bonus = 180 if any(token in text for token in ("路", "街", "號", "Taiwan", "市")) else 0
    useful_length = min(len(compact), 260)
    capped_cjk = min(cjk_count, 90) * 3
    capped_digits = min(digit_count, 40) * 2
    gibberish_penalty = 420 if len(compact) > 700 and valid_email_bonus == 0 and phone_bonus == 0 else 0
    fragmented_line_penalty = short_line_count * 28 if len(lines) > 16 else 0
    symbol_penalty = min(symbol_count, 80) * 4
    return (
        useful_length
        + capped_cjk
        + capped_digits
        + valid_email_bonus
        + phone_bonus
        + mobile_bonus
        + contact_label_bonus
        + org_bonus
        + address_bonus
        - gibberish_penalty
        - fragmented_line_penalty
        - symbol_penalty
    )


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


def render_oriented_preview(source: Path, rotation: int = 0) -> bytes:
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        if rotation:
            image = image.rotate(rotation, expand=True)
        if image.mode not in {"RGB", "L"}:
            image = image.convert("RGB")
        output = BytesIO()
        image.save(output, format="JPEG", quality=92, optimize=True)
        return output.getvalue()


def run_tesseract_command(image_path: Path, languages: str, psm: int) -> tuple[str, str, int, int]:
    started = monotonic()
    command = ["tesseract", str(image_path), "stdout", "-l", languages, "--psm", str(psm)]
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
            for psm in (4, 5, 6, 11, 12):
                for rotation in (0, 90, 180, 270):
                    prepared_path = temp_path / f"card-{psm}-{rotation}.png"
                    preprocess_image(file_path, prepared_path, rotation)
                    text, stderr, returncode, elapsed_ms = run_tesseract_command(
                        prepared_path,
                        languages,
                        psm,
                    )
                    attempt_metadata = {
                        "psm": psm,
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
    landscape_attempts = [attempt for attempt in attempts if attempt.metadata["rotation"] in {90, 270}]
    preview_rotation = best.metadata["rotation"]
    if landscape_attempts:
        best_landscape = max(landscape_attempts, key=lambda attempt: attempt.score)
        if best_landscape.score >= best.score * 0.7:
            preview_rotation = best_landscape.metadata["rotation"]
    return OcrResult(
        text=best.text,
        metadata={
            "engine": "tesseract",
            "languages": languages,
            "elapsedMs": elapsed_ms,
            "selectedRotation": best.metadata["rotation"],
            "selectedPsm": best.metadata["psm"],
            "selectedPreviewRotation": preview_rotation,
            "selectedScore": best.score,
            "attempts": [attempt.metadata for attempt in attempts],
            "errors": errors,
            "command": [
                "tesseract",
                "<preprocessed-input>",
                "stdout",
                "-l",
                languages,
                "--psm",
                "<selected-psm>",
            ],
            "textLength": len(best.text),
        },
    )
