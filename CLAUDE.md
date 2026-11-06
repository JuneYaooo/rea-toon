# rea-toon：给 Agent 的说明

这个仓库是"逆向拆解一切短剧"：主包 `rea_toon` 只做拆解；换角色复刻是下游的单独包 `remake/`（`rea-toon-remake`）。

- 用户要拆解短剧（这条为什么火、套路、整部剧的结构）时，**先读 `skills/rea-toon/SKILL.md`**：
  `analyze`（剧本源码 → 深度拆解 → 爆款拆解报告）→ 和用户一起审 breakdown.html → 要复刻就 `lock`；整部剧用 `series`。
- 用户要复刻、换角色、换画风时，先按上面拆完并 lock，再**读 `skills/rea-toon-remake/SKILL.md`**，按里面的步骤做
  （定换角范围 → plan → 先审关键帧 → `--pilot` 试跑 → 全片 → 拼片 → 自查 + 人工看成片）。模型行为先在试跑里验证，不要假设；没看过成片不要说做好了。
- 代码结构（拆解，`rea_toon/`）：
  - `ingest.py` 导入视频；`analyze.py` 切镜/分轨（rea-video）+ 字幕 OCR + VLM 角色表和逐镜拆解 → `story.json`，
    再做全剧级核对：逐句标说话人、合并乔装/换装的同一角色（`rea-toon refine` 给旧目录补做）
  - `breakdown.py`：深度拆解（画风档案、分场造型、戏剧节拍和逐镜看点、风险标签、一致性检查）→ `breakdown.html`；`lock` 审定
  - `report.py`：爆款拆解报告（代码算节奏曲线/爽点间隔/戏份/对手戏，一次模型调用写卖点、套路、可复用公式）→ `report.html`；
    不写 story.json，不会让锁失效
  - `genre.py`：按数据判断类型（真人/AI 真人感/2D/3D/动态漫/解说）和每种类型要看的点
  - `series.py`：整部剧（每集一个 `epNN/` 单集目录，可续跑；跨集合并角色只记在 series.json，不改各集 story.json）→ `series.html`
  - `page.py` 审核页共用样式和 SVG 图表；`media.py` 分析用的 ffmpeg/OpenCV；`vlm.py`；所有拆解提示词在 `prompts.py`
- 代码结构（复刻，`remake/rea_toon_remake/`）：`remake.py`（设定图 → 关键帧 → 视频，`_guard` 检查）、`assemble.py`、`qa.py`、
  `spec.py`、`cache.py`、`media.py`（生成用的检查和渲染）、`engines/`（gpt-image-2、H3、Seedance）、`prompts.py`、`examples/`。
  复刻只读锁定的 story.json，**不要在 remake 里加任何分析原片的调用**，缺什么在拆解阶段补；复刻可以 import `rea_toon`，反过来不行。
- 安装：`pip install -e . -e ./remake`；改完在仓库根目录跑 `python -m pytest -q`（两个包的测试都会跑）。
- 改拆解提示词会让已有工作目录的 VLM 缓存失效（重跑要重新付费），没必要不改措辞。
- 生成调用会计费：复刻先 `rea-toon-remake plan`，再 `--until frames`，确认后再出视频。
- 不要提交密钥、服务器密码和 `work/`。
