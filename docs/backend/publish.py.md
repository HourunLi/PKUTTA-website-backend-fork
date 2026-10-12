# `backend/publish.py`

后台 Submit 操作的异步执行器。`Publisher` 维护 `{running,success,error}` 状态，通过专用锁保证同时只有一次提交；任务在后台线程运行。界面可持续查询状态，失败信息保留最近 8000 字符的 Git 输出。

提交前检查 `webpage/` 是项目内的独立目录、具有自己的 `.git`、Git 仓库根目录确为 `webpage/`，并处于 `master` 分支，避免误提交上级仓库或其他分支。然后严格顺序执行 `git add .`、`git commit -m "automatic update"`、`git push origin master`，工作目录始终是 `webpage/`。进程使用参数列表启动，不经过 shell；stdout/stderr 合并实时输出到后台终端。

只在 `git add .` 时持有数据库读写锁，确保暂存不会读到写了一半的数据库；提交与网络推送期间可以继续编辑比赛，新编辑将在下一次 Submit 中提交。未运行任何 build。

若此前提交成功但推送失败，重新点击 Submit 时 `git commit` 可能返回“没有要提交的修改”。当退出码为 1 且暂存区确实无改动时继续推送已有提交；其他命令失败立即停止，恢复按钮可用状态并保留错误说明。未配置仓库、非 master 分支或无法启动 Git 同样会通过状态返回失败。

`runner` 可注入，用于在测试中模拟 Git 执行，不影响实际网页仓库或远端。
