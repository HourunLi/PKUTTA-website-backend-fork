# `backend/server.py`

## 启动和访问

从项目根目录运行 `python -m backend.server`。服务器只绑定 `127.0.0.1:4000`，浏览器打开 `http://localhost:4000/`。后台与公开页共用 `webpage/` 中的 HTML、JS、CSS；提供后台首页时仅将页面模式改成 `admin`，并使用 `/assets/` 资源地址。数据库直接存于 `webpage/database/`，修改后可直接同步 `webpage/` 到服务器，无需重新构建页面。服务器检查 `Host` 和请求的 `Origin`，只接受本地来源。

请求/响应均为 UTF-8 JSON，错误统一为 `{ "error": "说明" }`。创建或修改后返回更新的赛事或项目详情。JSON 请求体最大 10 MiB。

左上角标志与浏览器图标共用 `webpage/icon.jpg`。后台仅为 `/icon.jpg` 开放该 JPG，返回 `image/jpeg`；页面中的相对图标链接改为这个绝对路径，使 `/admin` 等入口也能显示。其他静态路由仍只开放指定的 HTML、JS、CSS 文件，不允许任意路径访问。

## 查询接口

| 方法 | 路径 | 响应 |
| --- | --- | --- |
| `GET` | `/api/health` | `{ok:true}` |
| `GET` | `/api/data` | 所有赛事及项目的完整快照 |
| `GET` | `/api/submit` | `{running,success,error}`，查询当前或最近一次 Submit 的状态 |
| `GET` | `/api/competitions` | `{competitions:[赛事元数据]}`，按开始日期降序 |
| `GET` | `/api/competitions/{赛事}` | `{competition:赛事元数据,events:[项目元数据]}` |
| `GET` | `/api/competitions/{赛事}/events/{项目}` | `{competition,event,stages:{阶段名:{metadata,data}}}` |

## 修改接口

| 方法 | 路径 | JSON 请求体 |
| --- | --- | --- |
| `POST` | `/api/competitions` | `{name,start_date,end_date,venue,slug?}`；省略 `slug` 时自动生成，明确提供则严格校验（空值也拒绝），重名返回 409 |
| `PATCH` | `/api/competitions/{赛事}` | 任意可修改的 `name`、`start_date`、`end_date`、`venue` |
| `DELETE` | `/api/competitions/{赛事}` | 无 |
| `POST` | `/api/competitions/{赛事}/events` | `{discipline,format,schedule,csv,group_spec?,knockout_rounds?}` |
| `DELETE` | `/api/competitions/{赛事}/events/{项目}` | 无 |
| `POST` | `/api/competitions/{赛事}/events/{项目}/stages/{阶段}/draw` | `{}` |
| `PATCH` | `/api/competitions/{赛事}/events/{项目}/groups/{小组ID}` | `{scheduled_time?,scheduled_table?,scheduled_referee?}`，至少一个文字字段；小组 ID 为正整数 |
| `PATCH` | `/api/competitions/{赛事}/events/{项目}/round-robin/schedule` | `{scheduled_time?,scheduled_table?,scheduled_referee?}`，更新独立循环赛统一安排及对应场次字段 |
| `PATCH` | `/api/competitions/{赛事}/events/{项目}/stages/{阶段}/matches/{场次ID}` | `{finished?,table?,time?,referee?}` |
| `POST` | `/api/competitions/{赛事}/events/{项目}/stages/{阶段}/matches/{场次ID}/games` | `{a:非负整数,b:非负整数}`，A/B 不能同分 |
| `PATCH` | `.../games/{从零开始的局下标}` | `{a,b}` |
| `DELETE` | `.../games/{从零开始的局下标}` | 无 |
| `POST` | `/api/submit` | `{}`；启动异步提交，HTTP 202 返回 `{running:true,success:null,error:null}`；已有提交运行时返回 HTTP 409 |

项目创建后属性不可修改；删除项目再创建才能改变赛程。比赛与项目删除由前端提供确认对话框，后端只删除已验证的数据库直属目录。

小组和独立循环赛的统一预定时间、桌号及裁判初始为空。保存统一安排会覆盖本组或循环赛所有场次的对应字段；空字符串用于清空。直接修改单场安排不会反向改变统一计划。所有数据库 API 的读取和写入都持有同一个锁，避免请求之间读到部分更新。

Submit 路由独立于数据库锁，调用 `publish.py` 的单任务执行器。Git 暂存阶段才短暂持有数据库锁；提交和网络推送由后台线程执行，不阻塞赛事操作。Git 输出显示在后台终端，界面根据轮询状态恢复按钮并在失败时警告。
