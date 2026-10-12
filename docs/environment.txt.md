# `environment.txt`

从已创建的 `pkutta` Conda 环境提取 Python 3.12 显式依赖，保存为 Conda 的纯文本包规格 `python>=3.12,<3.13`，允许获取该小版本的最新修订。Conda 会将 `.txt` 识别为包列表，故重建命令是 `conda create -n pkutta --file environment.txt`。本项目后台仅使用 Python 标准库。
