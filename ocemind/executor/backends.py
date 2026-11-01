"""执行后端选择：每一项能力，用本地开源引擎还是云端 API。

原则
----
1. **能本地就本地**。OCR 用 RapidOCR、转写用 faster-whisper —— 不联网、
   不花钱、不上传素材。默认 `auto` 就是"本地装了就用本地的"。
2. **要大模型时才走云端**。AI 生图、声音克隆这类本地跑不动的，直接标成
   只能云端，不在界面上假装有本地选项。
3. **云端服务商按能力分别选**。转写可以用 Groq、翻译可以用 DeepSeek、
   OCR 可以用百炼，互不影响，随时在「设置 · 引擎」里改。

这个模块只负责"选谁"，不加载模型、不发请求。判断引擎装没装用
`find_spec`，不会把 onnxruntime 这类重依赖 import 进来。
"""
from __future__ import annotations

import importlib.util
import shutil

# ------------------------------------------------------------------ 服务商

PROVIDERS: dict[str, dict] = {
    "siliconflow": {
        "name": "SiliconFlow 硅基流动",
        "base": "https://api.siliconflow.cn/v1",
        "keyField": "siliconflowKey",
        "home": "https://cloud.siliconflow.cn/account/ak",
        "note": "国内直连，转写 / 视觉 / 生图 / 配音都有，有免费额度",
    },
    "dashscope": {
        "name": "阿里云百炼",
        "base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "keyField": "dashscopeKey",
        "home": "https://bailian.console.aliyun.com/",
        "note": "通义千问 / Qwen-VL，兼容 OpenAI 协议（对话与视觉）",
    },
    "deepseek": {
        "name": "DeepSeek",
        "base": "https://api.deepseek.com/v1",
        "keyField": "deepseekKey",
        "home": "https://platform.deepseek.com/api_keys",
        "note": "只有对话模型，适合字幕翻译",
    },
    "groq": {
        "name": "Groq",
        "base": "https://api.groq.com/openai/v1",
        "keyField": "groqKey",
        "home": "https://console.groq.com/keys",
        "note": "Whisper 转写又快又便宜；国内访问需要代理",
    },
    "openai": {
        "name": "OpenAI",
        "base": "https://api.openai.com/v1",
        "keyField": "openaiKey",
        "home": "https://platform.openai.com/api-keys",
        "note": "通用兜底；国内访问需要代理",
    },
    "custom": {
        "name": "自定义（OpenAI 兼容）",
        "base": "",
        "keyField": "customKey",
        "home": "",
        "note": "任何兼容 /v1 的服务：填地址 + 密钥即可",
    },
}

# ------------------------------------------------------------ 本地引擎清单
#
# `module` 用来判断装没装；`package` 是 pip 安装名。体积是给用户看的，
# 别让人一键装完才发现占了 2 GB。

LOCAL_ENGINES: dict[str, tuple[dict, ...]] = {
    "asr": (
        {
            "id": "faster-whisper",
            "name": "faster-whisper",
            "module": "faster_whisper",
            "package": "faster-whisper",
            "size": "约 200 MB",
            "note": "CPU 也能跑，int8 量化；模型首次使用时自动下载",
        },
        {
            "id": "whisper",
            "name": "openai-whisper",
            "module": "whisper",
            "package": "openai-whisper",
            "size": "约 2.5 GB（含 PyTorch）",
            "note": "需要 torch，体积大；没有 CUDA 时比 faster-whisper 慢",
        },
    ),
    "ocr": (
        {
            "id": "rapidocr",
            "name": "RapidOCR",
            "module": "rapidocr_onnxruntime",
            "package": "rapidocr-onnxruntime",
            "size": "约 90 MB",
            "note": "ONNX 离线推理，中英混排；纯 CPU，不需要联网",
        },
        {
            "id": "paddleocr",
            "name": "PaddleOCR",
            "module": "paddleocr",
            # paddleocr 只是壳，真正跑推理的是 paddlepaddle。少了它，
            # 构造 PaddleOCR() 会直接失败——不能算「装了」。
            "requires": ("paddle",),
            "package": "paddleocr paddlepaddle",
            "size": "约 500 MB",
            "note": "中文识别效果最好，安装体积大；需要同时装 paddlepaddle",
        },
        {
            "id": "tesseract",
            "name": "Tesseract",
            "module": "pytesseract",
            "package": "pytesseract",
            "size": "约 50 MB + 系统程序",
            "note": "系统里还得装 Tesseract 本体，中文要 chi_sim 语言包",
        },
    ),
}

# ---------------------------------------------------------- 能力 → 能选什么

SPECS: dict[str, dict] = {
    "transcribe.asr": {
        "name": "音视频转文本",
        "local": "asr",
        "providers": ("siliconflow", "groq", "openai", "custom"),
        "models": {
            # 硅基流动：SenseVoiceSmall 快但只回纯文本；Qwen/Qwen3-ASR-1.7B
            # 断句标点更好、同样没有时间轴；要 SRT 用
            # XingChenAGI/XingChenASR-V3.2（支持 verbose_json 分段）
            "siliconflow": "FunAudioLLM/SenseVoiceSmall",
            "groq": "whisper-large-v3",
            "openai": "whisper-1",
        },
        "defaultLocalModel": "small",
    },
    "image.ocr": {
        "name": "图片 OCR",
        "local": "ocr",
        "providers": ("siliconflow", "dashscope", "openai", "custom"),
        "models": {
            "siliconflow": "Qwen/Qwen2.5-VL-72B-Instruct",
            "dashscope": "qwen-vl-max",
            "openai": "gpt-4o-mini",
        },
    },
    "image.generate": {
        "name": "AI 生图",
        "providers": ("siliconflow", "openai", "custom"),
        "models": {
            "siliconflow": "Kwai-Kolors/Kolors",
            "openai": "dall-e-3",
        },
    },
    "audio.tts": {
        "name": "TTS 配音",
        "providers": ("siliconflow", "openai", "custom"),
        "models": {
            "siliconflow": "FunAudioLLM/CosyVoice2-0.5B",
            "openai": "tts-1",
        },
    },
    "audio.voiceclone": {
        "name": "声音克隆",
        "providers": ("siliconflow",),
        "models": {"siliconflow": "FunAudioLLM/CosyVoice2-0.5B"},
    },
    "media.subtitle": {
        "name": "字幕翻译",
        "providers": ("siliconflow", "deepseek", "dashscope", "openai", "custom"),
        "models": {
            "siliconflow": "Qwen/Qwen2.5-7B-Instruct",
            "deepseek": "deepseek-chat",
            "dashscope": "qwen-plus",
            "openai": "gpt-4o-mini",
        },
    },
}


# ------------------------------------------------------------ 本地引擎探测

def _module_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _engine_available(engine: dict) -> bool:
    """只看模块在不在，不 import 重依赖。

    `requires` 里的模块是运行必需的（比如 PaddleOCR 离不开 paddlepaddle），
    少一个就当作没装——否则 UI 会显示「可用」，一点就报 ModuleNotFoundError。
    """
    if not _module_present(engine["module"]):
        return False
    for extra in engine.get("requires", ()):
        if not _module_present(extra):
            return False
    if engine["id"] == "tesseract":
        return bool(shutil.which("tesseract"))
    return True


def local_engines(kind: str) -> list[dict]:
    """列出某类本地引擎以及装没装。"""
    out = []
    for engine in LOCAL_ENGINES.get(kind, ()):
        item = dict(engine)
        item["available"] = _engine_available(engine)
        out.append(item)
    return out


def first_local(kind: str) -> str:
    for engine in local_engines(kind):
        if engine["available"]:
            return engine["id"]
    return ""


def local_engine_info(kind: str, engine_id: str) -> dict | None:
    for engine in local_engines(kind):
        if engine["id"] == engine_id:
            return engine
    return None


# ------------------------------------------------------------------ 解析

def _choice(cfg, capability_id: str) -> dict:
    """用户为这项能力保存的选择。空 dict 表示"没选过，走自动"。"""
    models = cfg.get("models") or {}
    choice = models.get(capability_id) if isinstance(models, dict) else None
    choice = dict(choice or {})
    if capability_id == "transcribe.asr" and not choice:
        # 兼容老版本：只存了 asrProvider / asrModel 的设置
        legacy = (cfg.get("asrProvider") or "").strip()
        if legacy and legacy in SPECS["transcribe.asr"]["providers"]:
            choice["provider"] = legacy
            if cfg.get("asrModel"):
                choice["model"] = cfg.get("asrModel")
    return choice


def _provider_for(cfg, spec: dict, wanted: str) -> str:
    allowed = spec.get("providers") or ()
    if wanted in allowed:
        return wanted
    # 用户没选或选了个不适用的：挑第一个已经填了密钥的
    for key in allowed:
        if (cfg.get(PROVIDERS[key]["keyField"]) or "").strip():
            return key
    return allowed[0] if allowed else ""


def _base_for(cfg, provider: str) -> str:
    if provider == "custom":
        return ((cfg.get("customBaseUrl") or cfg.get("asrBaseUrl") or "")
                .strip().rstrip("/"))
    return PROVIDERS[provider]["base"].rstrip("/")


def resolve(cfg, capability_id: str, params: dict | None = None) -> dict:
    """决定这次用谁。返回的 `key` 只在引擎内部用，不要直接吐给前端。"""
    spec = SPECS.get(capability_id)
    if spec is None:
        return {"capability": capability_id, "backend": "local", "ready": True,
                "problem": "", "engine": "", "provider": "", "base": "",
                "key": "", "model": ""}

    params = params or {}
    choice = _choice(cfg, capability_id)
    backend = (params.get("backend") or choice.get("backend") or "auto").lower()
    local_kind = spec.get("local") or ""

    if backend not in ("auto", "local", "cloud"):
        backend = "auto"

    local_id = ""
    if local_kind:
        chosen = ""
        wanted = (choice.get("localEngine") or "").strip()
        if wanted:
            info = local_engine_info(local_kind, wanted)
            chosen = wanted if (info and info["available"]) else ""
        if backend == "local":
            # 明确选了某个引擎但它没装，就报错，不偷偷换成别的
            local_id = chosen or ("" if wanted else first_local(local_kind))
            if not local_id:
                return {
                    "capability": capability_id, "backend": "local",
                    "engine": "", "local_engine": "", "local_kind": local_kind,
                    "local_name": "", "provider": "", "provider_name": "",
                    "base": "", "key": "", "model": "", "ready": False,
                    "problem": "选了本地，但本机没有装可用的本地引擎",
                }
        else:  # auto / cloud
            local_id = chosen if backend == "auto" else ""
            if backend == "auto" and not local_id and not wanted:
                local_id = first_local(local_kind)
    elif backend == "local":
        return {
            "capability": capability_id, "backend": "local", "engine": "",
            "local_engine": "", "local_kind": "", "local_name": "",
            "provider": "", "provider_name": "", "base": "", "key": "",
            "model": "", "ready": False,
            "problem": "这项能力需要大模型，本机不提供本地选项，只能走 API",
        }
    backend = "local" if local_id else "cloud"

    if backend == "local":
        info = local_engine_info(local_kind, local_id) or {}
        model = (params.get("local_model") or choice.get("localModel")
                 or spec.get("defaultLocalModel") or "")
        return {
            "capability": capability_id,
            "backend": "local",
            "engine": local_id,
            "local_engine": local_id,
            "local_kind": local_kind,
            "local_name": info.get("name", local_id),
            "provider": "",
            "provider_name": "本地",
            "base": "",
            "key": "",
            "model": model,
            "ready": True,
            "problem": "",
        }

    provider = _provider_for(cfg, spec, (params.get("provider") or choice.get("provider") or ""))
    if not provider:
        return {"capability": capability_id, "backend": "cloud", "engine": "",
                "local_engine": "", "local_kind": local_kind, "local_name": "",
                "provider": "", "provider_name": "", "base": "", "key": "",
                "model": "", "ready": False,
                "problem": "这项能力只能走 API，但没有可用的服务商"}

    info = PROVIDERS[provider]
    base = (params.get("base_url") or _base_for(cfg, provider)).strip()
    key = (params.get("api_key") or cfg.get(info["keyField"]) or "").strip()
    model = (params.get("model") or choice.get("model")
             or spec["models"].get(provider) or "")

    problems = []
    if not base:
        problems.append("还没填接口地址")
    if not key:
        problems.append(f"还没填 {info['name']} 的密钥")
    if not model:
        problems.append("还没填模型名")

    return {
        "capability": capability_id,
        "backend": "cloud",
        "engine": provider,
        "local_engine": "",
        "local_kind": local_kind,
        "local_name": "",
        "provider": provider,
        "provider_name": info["name"],
        "base": base,
        "key": key,
        "model": model,
        "ready": not problems,
        "problem": "；".join(problems),
    }


# ------------------------------------------------------------------ 视图

def summary(cfg) -> dict[str, dict]:
    """给界面和 Agent 看的只读状态（不含密钥）。"""
    out: dict[str, dict] = {}
    for cap_id, spec in SPECS.items():
        item = resolve(cfg, cap_id)
        item.pop("key", None)
        item["choice"] = _choice(cfg, cap_id)
        item["providers"] = list(spec.get("providers") or ())
        item["localOptions"] = (local_engines(spec["local"])
                                if spec.get("local") else [])
        item["cloudOnly"] = not spec.get("local")
        out[cap_id] = item
    return out


def providers_view(cfg) -> list[dict]:
    out = []
    for key, info in PROVIDERS.items():
        out.append({
            "key": key,
            "name": info["name"],
            "base": _base_for(cfg, key),
            "home": info["home"],
            "note": info["note"],
            "hasKey": bool((cfg.get(info["keyField"]) or "").strip()),
            "keyField": info["keyField"],
        })
    return out


def provider_capabilities(provider: str) -> list[str]:
    return [cap for cap, spec in SPECS.items()
            if provider in (spec.get("providers") or ())]


def set_choice(cfg, capability_id: str, patch: dict) -> dict:
    """保存一项能力的选择。只写认识的字段，避免界面把垃圾塞进设置。"""
    if capability_id not in SPECS:
        raise ValueError(f"未知能力: {capability_id}")
    models = dict(cfg.get("models") or {})
    current = dict(models.get(capability_id) or {})
    for key in ("backend", "provider", "model", "localModel", "localEngine"):
        if key in patch:
            value = str(patch.get(key) or "").strip()
            if value:
                current[key] = value
            else:
                current.pop(key, None)
    if "backend" in current and current["backend"] not in ("auto", "local", "cloud"):
        current.pop("backend")
    if current.get("provider") and current["provider"] not in (
            SPECS[capability_id].get("providers") or ()):
        current.pop("provider")
    models[capability_id] = current
    cfg.update({"models": models})
    return current
