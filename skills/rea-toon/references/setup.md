# 安装和密钥

## 安装（第一次用时由 agent 做）

```bash
git clone https://github.com/JuneYaooo/rea-toon && cd rea-toon
pip install -e .                                          # Python ≥ 3.10，需要 ffmpeg
pip install git+https://github.com/JuneYaooo/rea-video    # 切镜、人声 / 配乐分轨
```

本仓库不允许商用（PolyForm Noncommercial 1.0.0，见 LICENSE）；用户要拿去做商业用途时，提醒他先找作者授权。

## 密钥

拆解默认由 agent 自己看图作答，不需要密钥（见"谁来看图作答"）。要用接口模型时，从 `--env-file`、`./.env` 或仓库里的 `.env`
（不进仓库）读取 `REA_TOON_VLM_BASE_URL / _API_KEY / _MODEL`（配了就默认用它）。

- 密钥放在别的项目的 `.env` 里：写 `REA_TOON_ENV_FILES=<那个 .env 的路径>`，会接着读它。
- 想借用那里别的前缀的模型（如 `OTHER_BASE_URL / _API_KEY / _MODEL_NAME`）当备选：`REA_TOON_VLM_FALLBACK_PREFIXES=OTHER`，只在 `--backend api` 时用。
- rea-video 不在 PATH 上：`REA_VIDEO_BIN=<rea-video 的路径>`。
服务器密码、密钥都不要写进仓库。
