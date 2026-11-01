"""图像能力：OCR、基础处理、AI 生图。

OCR 默认走本地开源引擎（RapidOCR / PaddleOCR / Tesseract），
本地没装才回落到云端视觉模型。
基础处理（裁剪/缩放/水印/转格式）用 Pillow，纯本地。
生图必须走云端——本地跑不动扩散模型。
"""
from __future__ import annotations

import base64
import json
import mimetypes
import urllib.request
from pathlib import Path

from .. import backends
from .base import Engine, fail, ok

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff"}


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def _post_json(url: str, key: str, payload: dict, timeout: float = 180) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = (exc.read() or b"").decode("utf-8", "replace")[:400]
        raise RuntimeError(f"{exc.code} {detail}") from exc


# ------------------------------------------------------------------- OCR

def _ocr_rapidocr(path: Path) -> str:
    from rapidocr_onnxruntime import RapidOCR

    result, _ = RapidOCR()(str(path))
    lines = [str(item[1]).strip() for item in (result or []) if len(item) > 1]
    return "\n".join(line for line in lines if line)


def _new_paddleocr(PaddleOCR):
    """PaddleOCR 3.x 去掉了 show_log / use_angle_cls，2.x 只认那一套。

    先按 2.x 构造，不行再按 3.x 构造——升级不该让整条 OCR 直接挂掉。
    """
    try:
        return PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    except (TypeError, ValueError):
        return PaddleOCR(lang="ch")


def _paddle_texts(page) -> list[str]:
    """从一页结果里取文字。

    2.x: [[box, (text, score)], ...]
    3.x: Result / dict，文字在 rec_texts
    """
    if isinstance(page, dict):
        return [str(t).strip() for t in (page.get("rec_texts") or []) if str(t).strip()]
    for attr in ("res", "json"):
        data = getattr(page, attr, None)
        if isinstance(data, dict):
            data = data.get("res") or data
            if isinstance(data, dict) and data.get("rec_texts"):
                return [str(t).strip() for t in data["rec_texts"] if str(t).strip()]
    return []


def _paddle_lines(result) -> list[str]:
    lines: list[str] = []
    for page in result or []:
        if isinstance(page, dict) or hasattr(page, "res") or hasattr(page, "json"):
            lines.extend(_paddle_texts(page))
            continue
        for item in page or []:
            try:
                lines.append(str(item[1][0]).strip())
            except (IndexError, TypeError):
                continue
    return [line for line in lines if line]


def _ocr_paddleocr(path: Path) -> str:
    from paddleocr import PaddleOCR

    ocr = _new_paddleocr(PaddleOCR)
    # 3.x 的入口是 predict()；拿不到文字再退回 2.x 的 ocr()
    if hasattr(ocr, "predict"):
        try:
            lines = _paddle_lines(ocr.predict(str(path)))
        except Exception:  # noqa: BLE001
            lines = []
        if lines:
            return "\n".join(lines)

    try:
        result = ocr.ocr(str(path), cls=True)
    except TypeError:
        result = ocr.ocr(str(path))
    return "\n".join(_paddle_lines(result))


def _ocr_tesseract(path: Path) -> str:
    import pytesseract
    from PIL import Image

    # chi_sim 没装时退回英文，至少不会整个失败
    for lang in ("chi_sim+eng", "eng"):
        try:
            text = pytesseract.image_to_string(Image.open(path), lang=lang)
            return text.strip()
        except pytesseract.TesseractError:
            continue
    return ""


def ocr_local(path: Path, engine_id: str) -> str:
    if engine_id == "rapidocr":
        return _ocr_rapidocr(path)
    if engine_id == "paddleocr":
        return _ocr_paddleocr(path)
    if engine_id == "tesseract":
        return _ocr_tesseract(path)
    raise RuntimeError(f"未知的本地 OCR 引擎: {engine_id}")


class Ocr(Engine):
    id = "image.ocr"
    kinds = ("image",)

    def run(self, ctx) -> dict:
        src = Path(ctx.inputs.get("path") or "")
        if not src.exists():
            return fail("图片不存在")
        if src.suffix.lower() not in IMAGE_EXTS:
            return fail(f"不是图片: {src.suffix}")

        target = backends.resolve(ctx.config, self.id, ctx.params)
        if not target["ready"]:
            return fail(target["problem"] or "OCR 后端不可用")

        if target["backend"] == "local":
            ctx.progress(None, "识别中（本地）")
            try:
                text = ocr_local(src, target["local_engine"])
            except Exception as exc:  # noqa: BLE001
                return fail(f"本地 {target['local_engine']}: {exc}")
        else:
            ctx.progress(None, "识别中")
            prompt = (ctx.params.get("prompt")
                      or "把这张图片里的所有文字完整提取出来，保持原有换行和阅读顺序。"
                         "只输出提取到的文字，不要任何解释。")
            payload = {
                "model": target["model"],
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image_url",
                         "image_url": {"url": _data_uri(src)}},
                        {"type": "text", "text": prompt},
                    ],
                }],
                "temperature": 0.0,
            }
            try:
                data = _post_json(target["base"] + "/chat/completions",
                                  target["key"], payload)
                text = data["choices"][0]["message"]["content"].strip()
            except Exception as exc:  # noqa: BLE001
                return fail(f"{target['provider_name']}: {type(exc).__name__}: {exc}")

        if not text.strip():
            return fail("没有识别出文字")

        out_dir = Path(ctx.params.get("dir") or Path(ctx.config.home) / "documents")
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / (ctx.params.get("name") or f"{src.stem}.txt")
        out.write_text(text.strip() + "\n", encoding="utf-8")

        try:
            ctx.record_usage(target["engine"], self.id, 1, 0.0,
                             detail=target["model"])
        except Exception:  # noqa: BLE001
            pass
        return ok(out)


# ------------------------------------------------------------- 基础处理

class ImageEdit(Engine):
    id = "image.edit"
    kinds = ("image",)

    def run(self, ctx) -> dict:
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return fail("需要 Pillow：pip install pillow")

        src = Path(ctx.inputs.get("path") or "")
        if not src.exists():
            return fail("图片不存在")
        op = ctx.params.get("op") or "resize"
        out_dir = Path(ctx.params.get("dir") or src.parent)
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            image = Image.open(src)
            image.load()
        except Exception as exc:  # noqa: BLE001
            return fail(f"打不开图片: {exc}")

        # Pillow 靠扩展名决定存成什么格式。只给 "cover_small" 这种名字时，
        # 直接 save 会报 unknown file extension，所以补上源文件的扩展名。
        name = (ctx.params.get("name") or "").strip()
        out = out_dir / name if name else out_dir / f"{src.stem}_{op}{src.suffix}"
        if not out.suffix:
            out = out.with_suffix(src.suffix or ".png")
        try:
            if op == "resize":
                width = int(ctx.params.get("width") or 1280)
                ratio = width / image.width
                image = image.resize((width, max(1, int(image.height * ratio))),
                                     Image.LANCZOS)
            elif op == "crop":
                w = int(ctx.params.get("width") or image.width)
                h = int(ctx.params.get("height") or image.height)
                left = (image.width - w) // 2
                top = (image.height - h) // 2
                image = image.crop((max(0, left), max(0, top),
                                    min(image.width, left + w),
                                    min(image.height, top + h)))
            elif op == "watermark":
                text = ctx.params.get("text") or "Ocemind"
                draw = ImageDraw.Draw(image)
                try:
                    font = ImageFont.truetype("arial.ttf", 36)
                except Exception:  # noqa: BLE001
                    font = ImageFont.load_default()
                draw.text((24, image.height - 60), text, fill=(255, 255, 255),
                          font=font, stroke_width=2, stroke_fill=(0, 0, 0))
            elif op == "convert":
                fmt = (ctx.params.get("format") or "png").lower()
                out = out.with_suffix("." + fmt)
                image = image.convert("RGB") if fmt in ("jpg", "jpeg") else image
            elif op == "thumb":
                image.thumbnail((int(ctx.params.get("width") or 512),) * 2)
            else:
                return fail(f"未知图像操作: {op}")
            image.save(out)
        except Exception as exc:  # noqa: BLE001
            return fail(f"{type(exc).__name__}: {exc}")
        return ok(out)


# ---------------------------------------------------------------- 生图

class ImageGen(Engine):
    id = "image.generate"
    kinds = ("image",)

    def run(self, ctx) -> dict:
        prompt = (ctx.params.get("prompt") or "").strip()
        if not prompt:
            return fail("缺少提示词")

        target = backends.resolve(ctx.config, self.id, ctx.params)
        if not target["ready"]:
            return fail(target["problem"] or "生图服务不可用")
        key = target["key"]
        base = target["base"]
        model = target["model"]
        size = ctx.params.get("size") or "1024x1024"

        ctx.progress(None, "生成中")
        payload = {"model": model, "prompt": prompt, "image_size": size,
                   "batch_size": 1, "num_inference_steps": 25}
        try:
            data = _post_json(base + "/images/generations", key, payload,
                              timeout=300)
            images = data.get("images") or data.get("data") or []
            if not images:
                return fail(f"接口没有返回图片: {str(data)[:200]}")
            url = images[0].get("url") or ""
        except Exception as exc:  # noqa: BLE001
            return fail(f"{type(exc).__name__}: {exc}")

        out_dir = Path(ctx.params.get("dir") or Path(ctx.config.home) / "images")
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / (ctx.params.get("name") or f"gen_{int(__import__('time').time())}.png")
        try:
            urllib.request.urlretrieve(url, out)
        except Exception as exc:  # noqa: BLE001
            return fail(f"下载生成结果失败: {exc}")

        try:
            ctx.record_usage(target["engine"], "image.generate", 1,
                             float(ctx.config.get("imagePrice") or 0.0), model)
        except Exception:  # noqa: BLE001
            pass
        return ok(out)
