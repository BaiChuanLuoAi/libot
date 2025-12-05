# 环境变量设置说明

本项目使用环境变量来保护敏感信息（如API密钥）。

## 设置步骤

1. 复制 `env.example` 文件并重命名为 `.env`：
   ```bash
   copy env.example .env
   ```

2. 编辑 `.env` 文件，填入你的实际API密钥（不要修改 `env.example` 模板文件）：
   ```
   RUNPOD_API_KEY_I2V=你的图生视频API密钥
   RUNPOD_API_KEY_T2V=你的文生视频API密钥
   ```

## 重要提示

- ⚠️ **绝对不要**将 `.env` 文件提交到Git仓库
- `.env` 文件已经在 `.gitignore` 中，确保不会被意外提交
- 可以安全地将 `.env.example` 提交到仓库作为模板

## 依赖

确保安装了 `python-dotenv`：
```bash
pip install python-dotenv
```

或者通过 requirements.txt 安装：
```bash
pip install -r requirements.txt
```
