# rea-toon：给 Agent 的说明

- 用户要复刻短剧、换角色、换画风时，**先读 `skills/rea-toon/SKILL.md`**，按里面的 8 步做：拆剧本 → 和用户确认换角范围 → plan（看"保持原样的角色"）→ 先审关键帧 → `run --pilot` 试跑 3 镜 → 出全片 → 拼成片 → 自动自查 + 人工看成片。模型行为先在试跑里验证，不要假设；没看过成片不要说做好了。
- 代码结构：
  - `rea_toon/analyze.py`：调 rea-video 切镜/分轨 + 字幕 OCR + VLM 角色表和逐镜拆解 → `story.json`
  - `rea_toon/remake.py`：按 `remake.yaml` 规划和生成（设定图 → 关键帧 → 视频），输入 hash 缓存在 `cache.py`
  - `rea_toon/assemble.py`：每镜只读磁盘上的片段和 `.meta.json`（不依赖运行状态）；擦掉模型自己烧的字幕、按原时间线拼回（口型时差平移、逐段核对帧数）、重烧字幕、铺原音轨、出对比片
  - `rea_toon/qa.py`：交付前自查（时间线、漏换、口型分数、中段抽帧、字幕抽帧）
  - `rea_toon/media.py`：ffmpeg/OpenCV 工具：参考视频打码、口型同步测量、字幕擦除
  - `rea_toon/engines/`：gpt-image-2、H3（自部署 ComfyUI `h3local` / AutoDL 托管 `h3`）、Seedance（方舟）、VLM
  - 所有提示词在 `rea_toon/prompts.py`
- 改完跑 `python -m pytest -q`。
- 生成调用会计费：先 `plan`，再 `--until frames`，确认后再出视频。
- 不要提交密钥、服务器密码和 `work/`。
