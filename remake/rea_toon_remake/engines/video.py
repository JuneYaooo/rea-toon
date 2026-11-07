"""Video engines: MiniMax H3 on AutoDL.Art (ComfyUI workflows), self-hosted H3 ComfyUI, and Seedance 2.0.

One interface: generate(prompt, out, duration, aspect, first=, last=, refs=, audio=, video=, mode=).
mode: "first_last" (first+last frame), "first" (image-to-video), "lipsync" (image+speech audio),
"ref_video" (self-hosted H3 Ref2VA: new keyframes + cast sheets + the original shot as motion reference).
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import time
from pathlib import Path

import requests

from rea_toon.env import need

LOG = logging.getLogger(__name__)


def _data_uri(p: Path) -> str:
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    if p.suffix.lower() == ".wav":
        mime = "audio/wav"
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


class SubmittedError(RuntimeError):
    """The provider accepted (and bills) the task but we lost it: timed out, no video, download failed.
    Submitting again would pay twice, so generate() does not retry these."""


def _download_retry(url: str, out: Path, what: str) -> None:
    for k in range(4):
        try:
            return _download(url, out)
        except requests.RequestException as exc:
            if k == 3:
                raise SubmittedError(f"{what} 已完成但下载失败（已计费，不重新提交）：{exc}；地址 {url}")
            time.sleep(10 * (k + 1))


def _download(url: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part")
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    tmp.replace(out)


class VideoEngine:
    name = "base"
    min_s = 1
    max_s = 15
    modes = ("first_last", "first")

    def describe(self) -> str:
        return self.name

    def clamp(self, seconds: float) -> int:
        import math
        return max(self.min_s, min(self.max_s, math.ceil(seconds - 0.05)))

    def generate(self, prompt: str, out: Path, duration: int, aspect: str = "16:9", *, first: Path | None = None,
                 last: Path | None = None, refs: list[Path] | None = None, audio: Path | None = None,
                 video: Path | None = None, mode: str = "first_last", retries: int = 2) -> Path:
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                t = time.time()
                self._run(prompt, out, duration, aspect, first, last, refs or [], audio, mode, video)
                LOG.info("[video:%s] %s ok %.0fs", self.name, out.name, time.time() - t)
                return out
            except Exception as exc:
                last_exc = exc
                if isinstance(exc, SubmittedError):
                    break  # already paid for: a resubmit pays again
                if "余额不足" in str(exc) or "insufficient" in str(exc).lower():
                    break  # retrying cannot help
                LOG.warning("[video:%s] %s attempt %d failed: %s", self.name, out.name, attempt, str(exc)[:300])
                time.sleep(10 * attempt)
        raise RuntimeError(f"{self.name} 视频生成失败：{last_exc}")

    def _run(self, *a):  # pragma: no cover
        raise NotImplementedError


class H3AutoDL(VideoEngine):
    """MiniMax H3 ComfyUI workflows on autodl.art (same contract as capsule-cinema's adapter)."""

    name = "h3"
    min_s, max_s = 1, 15
    modes = ("first_last", "first", "lipsync")
    RES = {"16:9": "768p横", "9:16": "768p竖", "1:1": "768p(1:1)"}

    def __init__(self, poll: int = 6, max_wait: int = 1800):
        self.token = need("AUTODL_ART_TOKEN")
        self.base = (os.getenv("AUTODL_ART_BASE_URL") or "https://autodl.art").rstrip("/")
        self.poll, self.max_wait = poll, max_wait

    def _payload(self, prompt, duration, aspect, first, last, refs, audio, mode):
        res = self.RES.get(aspect, "768p横")
        if mode == "first" and first and not last and not refs:
            mode, last = "first_last", first  # H3 has no plain first-frame workflow: pin start and end to it
        if mode == "first_last" and first and last:
            return "minimax_h3_lightx2v", {"prompt": prompt, "duration": duration, "resolution": res,
                                           "first_frame": _data_uri(first), "last_frame": _data_uri(last)}
        if mode == "lipsync" and first and audio:
            # multimodal workflow: prompt (acting, camera) + new first frame + the shot's original voice
            wf = "minimax_h3_image_audio_to_video_v2_15s" if duration > 10 else "minimax_h3_image_audio_to_video_v2"
            return wf, {"prompt": prompt, "duration": duration, "resolution": res,
                        "ref_image_0": _data_uri(first), "ref_audio_0": _data_uri(audio)}
        images = [p for p in [first, *refs] if p]
        if not images:
            raise ValueError("H3 需要至少一张参考图")
        wf = "minimax_h3_lightx2v_v5_15s" if duration > 10 else "minimax_h3_lightx2v_v5"
        body = {"prompt": prompt, "duration": duration, "resolution": res}
        for i, p in enumerate(images[:9]):
            body[f"ref_image_{i}"] = _data_uri(p)
        return wf, body

    def _run(self, prompt, out, duration, aspect, first, last, refs, audio, mode, video=None):
        wf, body = self._payload(prompt, duration, aspect, first, last, refs, audio, mode)
        headers = {"Authorization": self.token, "Content-Type": "application/json"}
        r = requests.post(f"{self.base}/api/v1/comfyui/comfyui_workflow/{wf}", json=body, headers=headers, timeout=300)
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code >= 400 or data.get("code") != "Success":
            raise RuntimeError(f"H3 提交失败 HTTP {r.status_code}: {str(data.get('msg') or r.text)[:300]}")
        task = (data.get("data") or {}).get("task_id")
        LOG.info("[video:h3] %s submitted %s task=%s", out.name, wf, task)
        started = time.time()
        while time.time() - started < self.max_wait:
            time.sleep(self.poll)
            try:  # a network blip while polling must not abandon (and re-pay for) a submitted task
                q = requests.get(f"{self.base}/api/v1/comfyui/comfyui_workflow/result/{task}", headers=headers, timeout=120)
                qd = (q.json() or {}).get("data") or {}
            except (requests.RequestException, ValueError) as exc:
                LOG.warning("[video:h3] %s poll error (will retry): %s", out.name, str(exc)[:120])
                time.sleep(10)
                continue
            status = str(qd.get("status") or "").upper()
            if status in {"SUCCESS", "SUCCEEDED", "COMPLETED"}:
                for item in qd.get("results") or []:
                    url = item.get("url") or item.get("download_url") or ""
                    if url.split("?")[0].lower().endswith((".mp4", ".mov", ".webm")) or item.get("type") == "video":
                        return _download_retry(url, out, f"H3 任务 {task}")
                raise SubmittedError(f"H3 任务 {task} 成功但没有返回视频")
            if status in {"FAILED", "FAILURE", "ERROR", "CANCELED", "CANCELLED", "EXPIRED"}:
                raise RuntimeError(f"H3 任务失败 {status}: {str(qd.get('msg') or qd.get('message') or '')[:300]}")
        raise SubmittedError(f"H3 任务 {task} 等待超时（可能还在跑，已计费，不重新提交）")


class H3Local(VideoEngine):
    """Self-hosted MiniMax H3 ComfyUI (minimax-h3-autodl deployment), reached through an SSH tunnel.

    first_last / first -> H3-Base FL2VA; lipsync / ref_video -> H3-Base Ref2VA.
    ref_video: <Picture 1..> = new keyframes and cast sheets, <Video 1> = the original shot (motion, camera,
    timing), its soundtrack = the shot's voice. Generated on the shot's own clock (24 fps, 17n+5 frames).
    """

    name = "h3local"
    min_s, max_s = 1, 15
    modes = ("first_last", "first", "lipsync", "ref_video")
    FPS = 24
    MIN_FRAMES = 56            # ~2.3s; Ref2VA reference videos must be 2-15s
    RES = {"16:9": (1024, 576), "9:16": (576, 1024), "1:1": (768, 768)}
    FL2VA = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
    REF2VA = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"

    def __init__(self, poll: int = 5, max_wait: int = 3600):
        self.base = (os.getenv("H3_COMFY_URL") or "http://127.0.0.1:18188").rstrip("/")
        self.steps = int(os.getenv("H3_LOCAL_STEPS") or 10)
        self.seed = int(os.getenv("H3_LOCAL_SEED") or 20261102)
        self.poll, self.max_wait = poll, max_wait

    def describe(self) -> str:
        return f"h3local:{self.steps}steps"

    @classmethod
    def frames_for(cls, seconds: float) -> int:
        import math
        n = math.ceil((max(seconds, 0) * cls.FPS - 5) / 17 - 1e-3)
        return max(cls.MIN_FRAMES, min(362, 17 * max(n, 0) + 5))

    def clamp(self, seconds: float) -> float:
        return self.frames_for(seconds) / self.FPS

    # ---- ComfyUI plumbing ----
    def _upload(self, p: Path) -> str:
        from ..cache import file_digest
        name = f"{file_digest(p)}{p.suffix.lower()}"
        with p.open("rb") as f:
            r = requests.post(f"{self.base}/upload/image", files={"image": (name, f)},
                              data={"subfolder": "rea-toon", "overwrite": "true"}, timeout=300)
        r.raise_for_status()
        d = r.json()
        return f"{d['subfolder']}/{d['name']}" if d.get("subfolder") else d["name"]

    def _graph(self, unet: str, cond: dict, length: int, prefix: str) -> dict:
        return {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
                                                          "type": "minimax", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
            "4": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}},
            "5": cond,
            "6": {"class_type": "BasicScheduler", "inputs": {"model": ["1", 0], "scheduler": "simple",
                                                              "steps": self.steps, "denoise": 1.0}},
            "7": {"class_type": "RandomNoise", "inputs": {"noise_seed": self.seed}},
            "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
            "9": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["5", 0]}},
            "10": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["7", 0], "guider": ["9", 0],
                   "sampler": ["8", 0], "sigmas": ["6", 0], "latent_image": ["5", 1]}},
            "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["3", 0]}},
            "12": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["10", 0], "vae": ["4", 0]}},
            "13": {"class_type": "CreateVideo", "inputs": {"images": ["11", 0], "audio": ["12", 0],
                                                            "fps": float(self.FPS), "bit_depth": 8}},
            "14": {"class_type": "SaveVideo", "inputs": {"video": ["13", 0], "filename_prefix": prefix,
                                                          "format": "auto", "codec": "auto"}},
        }

    def build(self, prompt, duration, aspect, first, last, refs, audio, mode, video, upload) -> dict:
        w, h = self.RES.get(aspect, self.RES["16:9"])
        length = self.frames_for(duration)
        base = {"prompt": prompt, "width": w, "height": h, "length": length}
        extra: dict[str, dict] = {}
        if mode in ("first_last", "first") and not refs:
            cond = {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"clip": ["2", 0], "vae": ["3", 0], **base}}
            for k, p in (("first_frame", first), ("last_frame", last if mode != "first" else None)):
                if p:
                    nid = str(20 + len(extra))
                    extra[nid] = {"class_type": "LoadImage", "inputs": {"image": upload(p)}}
                    cond["inputs"][k] = [nid, 0]
            unet = self.FL2VA
        else:
            cond = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
                "clip": ["2", 0], "vae": ["3", 0], "audio_vae": ["4", 0], **base, "ref_image_size": "match"}}
            images = [p for p in ([first, last] if mode == "ref_video" else [first]) if p] + list(refs)
            if not images and not video:
                raise ValueError("H3 Ref2VA 需要至少一张参考图或一段参考视频")
            for i, p in enumerate(images[:9]):
                nid = str(20 + len(extra))
                extra[nid] = {"class_type": "LoadImage", "inputs": {"image": upload(p)}}
                cond["inputs"][f"ref_images.ref_image_{i}"] = [nid, 0]
            if video:
                lid, cid = str(20 + len(extra)), str(21 + len(extra))
                extra[lid] = {"class_type": "LoadVideo", "inputs": {"file": upload(video)}}
                extra[cid] = {"class_type": "GetVideoComponents", "inputs": {"video": [lid, 0]}}
                cond["inputs"]["ref_videos.ref_video_0"] = [cid, 0]
                cond["inputs"]["ref_video_audios.ref_video_audio_0"] = [cid, 1]
            elif audio:
                nid = str(20 + len(extra))
                extra[nid] = {"class_type": "LoadAudio", "inputs": {"audio": upload(audio)}}
                cond["inputs"]["ref_audios.ref_audio_0"] = [nid, 0]
            unet = self.REF2VA
        graph = self._graph(unet, cond, length, "rea-toon/clip")
        graph.update(extra)
        return graph

    def generate(self, *a, retries: int = 4, **k) -> Path:
        # the deployment restarts ComfyUI after each task / under memory pressure: allow a few resubmits
        return super().generate(*a, retries=retries, **k)

    def _fetch(self, prefix: str, out: Path, wait: int = 240) -> bool:
        """Download <prefix>_00001_.mp4 from the output folder, riding out a service restart."""
        sub, stem = prefix.rsplit("/", 1)
        q = requests.compat.urlencode({"filename": f"{stem}_00001_.mp4", "subfolder": sub, "type": "output"})
        deadline = time.time() + wait
        while True:
            try:
                r = requests.head(f"{self.base}/view?{q}", timeout=30)
                if r.status_code == 404:
                    return False
                if r.ok:
                    _download(f"{self.base}/view?{q}", out)
                    return True
            except requests.RequestException:
                pass
            if time.time() > deadline:
                return False
            time.sleep(10)

    def _wait_ready(self, wait: int = 600) -> None:
        """The service restarts after every task; wait until it answers again before uploading."""
        deadline = time.time() + wait
        while True:
            try:
                if requests.get(f"{self.base}/system_stats", timeout=15).ok:
                    return
            except requests.RequestException:
                pass
            if time.time() > deadline:
                raise RuntimeError(f"H3 本地服务 {self.base} 无响应（SSH 隧道是否开着？）")
            time.sleep(10)

    def _run(self, prompt, out, duration, aspect, first, last, refs, audio, mode, video=None):
        import uuid
        self._wait_ready()
        graph = self.build(prompt, duration, aspect, first, last, refs, audio, mode, video, self._upload)
        prefix = f"rea-toon/{out.stem}_{uuid.uuid4().hex[:8]}"
        graph["14"]["inputs"]["filename_prefix"] = prefix
        r = requests.post(f"{self.base}/prompt", json={"prompt": graph}, timeout=120)
        if r.status_code >= 400:
            raise RuntimeError(f"H3 本地提交失败 HTTP {r.status_code}: {r.text[:500]}")
        pid = r.json()["prompt_id"]
        LOG.info("[video:h3local] %s submitted %s %s frames=%s", out.name, mode,
                 graph["1"]["inputs"]["unet_name"].split("_")[2], graph["5"]["inputs"]["length"])
        started, lost = time.time(), 0
        while time.time() - started < self.max_wait:
            time.sleep(self.poll)
            try:  # the SSH tunnel can blip, the service can restart: keep looking for the queued job
                item = requests.get(f"{self.base}/history/{pid}", timeout=60).json().get(pid)
                if not item:
                    qd = requests.get(f"{self.base}/queue", timeout=60).json()
                    queued = any(pid in map(str, x) for x in qd.get("queue_running", []) + qd.get("queue_pending", []))
                    lost = 0 if queued else lost + 1
                    if lost >= 3:  # gone from queue and history: the service restarted
                        if self._fetch(prefix, out, wait=30):
                            return
                        raise RuntimeError("H3 本地任务丢失（ComfyUI 重启），重新提交")
                    continue
            except (requests.RequestException, ValueError) as exc:
                LOG.warning("[video:h3local] %s poll error (will retry): %s", out.name, str(exc)[:120])
                time.sleep(10)
                continue
            status = item.get("status") or {}
            if status.get("status_str") == "error":
                msgs = [m[1] for m in status.get("messages") or [] if m and m[0] == "execution_error"]
                detail = (msgs[0].get("exception_message") if msgs else "") or ""
                raise RuntimeError(f"H3 本地任务失败：{detail[:300]}")
            if status.get("completed"):
                if self._fetch(prefix, out):
                    return
                raise RuntimeError("H3 本地任务完成但没有视频输出")
        raise RuntimeError("H3 本地任务超时")


class SeedanceArk(VideoEngine):
    """Seedance 2.0 via Volcengine Ark contents/generations/tasks."""

    name = "seedance"
    min_s, max_s = 4, 15
    modes = ("first_last", "first")

    def __init__(self, poll: int = 8, max_wait: int = 1200, resolution: str = "720p"):
        self.key = need("ARK_API_KEY")
        self.model = os.getenv("ARK_SEEDANCE_MODEL") or "doubao-seedance-2-0-260128"
        self.base = (os.getenv("ARK_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
        self.poll, self.max_wait, self.resolution = poll, max_wait, resolution

    def _run(self, prompt, out, duration, aspect, first, last, refs, audio, mode, video=None):
        content: list[dict] = [{"type": "text", "text": prompt}]
        if first:
            content.append({"type": "image_url", "image_url": {"url": _data_uri(first)}, "role": "first_frame"})
        if mode == "first_last" and last:
            content.append({"type": "image_url", "image_url": {"url": _data_uri(last)}, "role": "last_frame"})
        body = {"model": self.model, "content": content, "ratio": aspect, "resolution": self.resolution,
                "duration": duration, "generate_audio": False, "watermark": False}
        url = f"{self.base}/contents/generations/tasks"
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        r = requests.post(url, json=body, headers=headers, timeout=180)
        if r.status_code >= 400:
            raise RuntimeError(f"Seedance 提交失败 HTTP {r.status_code}: {r.text[:300]}")
        task = r.json().get("id")
        LOG.info("[video:seedance] %s submitted task=%s", out.name, task)
        started = time.time()
        while time.time() - started < self.max_wait:
            time.sleep(self.poll)
            try:  # a network blip while polling must not abandon (and re-pay for) a submitted task
                q = requests.get(f"{url}/{task}", headers=headers, timeout=60).json()
            except (requests.RequestException, ValueError) as exc:
                LOG.warning("[video:seedance] %s poll error (will retry): %s", out.name, str(exc)[:120])
                time.sleep(10)
                continue
            status = str(q.get("status") or "").lower()
            if status == "succeeded":
                v = (q.get("content") or {}).get("video_url")
                v = v if isinstance(v, str) else (v or {}).get("url")
                if not v:
                    raise SubmittedError(f"Seedance 任务 {task} 成功但没有返回视频")
                return _download_retry(v, out, f"Seedance 任务 {task}")
            if status in {"failed", "error", "cancelled", "canceled", "expired", "rejected"}:
                raise RuntimeError(f"Seedance 任务失败: {str(q.get('error'))[:300]}")
        raise SubmittedError(f"Seedance 任务 {task} 等待超时（可能还在跑，已计费，不重新提交）")


def make_video_engine(name: str) -> VideoEngine:
    name = (name or "h3").lower()
    if name in {"h3", "autodl", "autodl-h3", "minimax-h3"}:
        return H3AutoDL()
    if name in {"h3local", "h3-local", "local-h3"}:
        return H3Local()
    if name in {"seedance", "seedance2", "ark"}:
        return SeedanceArk()
    raise ValueError(f"未知视频引擎 {name}；可选 h3 / h3local / seedance")
