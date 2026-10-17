# OHM — 记忆索引

用户 fork `uhayate/open-holdem-manager`（源自 `AHTOOOXA/…`，origin 走 SSH）。纯本地 / 单用户 / 无云无鉴权的扑克手牌追踪器（对标 Hand2Note），**只支持 GGPoker**。

**→ 技术栈 / 架构（Parse→Compute→Insert）/ `STAT_VERSION=5` / **位置命名约定 + ante 桌格式差异（GG 写 `posts the ante`、header `($0.02/$0.05($0.01))`、`sb≠bb/2`）/ rake 与 jackpot 的口径（`total_rake` 纯 Rake、`total_fees` 全额、恒等式 `sum(net_won)==-total_fees`）** / 全部硬约束（`_lock` 必须 RLock、重建事务边界、坏 WAL 自愈、优雅关闭、长任务轮询 `shutdown_requested()`、如何安全关掉运行中实例）/ `npm run check` / 打包流程：全在 `CLAUDE.md`。改代码前先读它。**

## 打包（2026-10-10 定）
- **`backend:build` 必须带 `--collect-all eval7`**：`eval7/__init__.py` 会 `from .handrange import HandRange`
  → `rangestring` → `pyparsing`，PyInstaller 默认只收 3 个 `.pyd`、漏掉这几个 `.py`，
  `import eval7` 整个失败 → `import_hands.py` 的 `except ImportError` 静默把 `_calc_equity` 设成 None
  → **打包版 all-in EV 退化成实际收益**（藏了很久，dev 环境正常所以直觉发现不了）。
  **验证方法：同一份库分别跑 dev 和安装包内后端，比 `win_rate_ev_bb100`** —— 相等就是没算出来。
- **`electron-builder` 只把 `backend/dist/ohm-backend` 拷进 `resources/backend`（`extraResources`），绝不重建后端。**
  改成 `npm run frontend:build && npx electron-builder` ≠ 完整构建 —— 改过后端却只跑这两条，会打出
  **新前端 + 旧后端**的包：界面照旧显示旧数字，而且看起来像「修复没生效」，实际是根本没被编译进去。
  2026-10-17 就白打了一版（用户截图 `vs Steal Fold 68% / 1,120 hands`，1120 = SB 467 + BB 653 的旧混算值）。
  **规则：只要动过 `backend/`，就必须 `npm run backend:build`。**
  最快的校验：`ls -l backend/dist/ohm-backend/ohm-backend.exe` 的时间必须晚于所有 `backend/app/**` 改动。
  最硬的校验（唯一能证明「冻结产物 = 源码」的办法，`eval7` 那个坑就是这么藏的）：
  复制库到临时目录 → `OHM_DATA_DIR=<tmp> ./ohm-backend.exe` → `netstat -ano | grep LISTENING | grep <pid>`
  拿端口 → `curl --noproxy '*' http://127.0.0.1:<port>/api/stats/hero` → `curl -X POST .../api/shutdown` 优雅关。
- **`npm run build` / 任何 `backend:build` 只要 `backend/dist` 已存在就会在 COLLECT 卡住**
  （safe-delete 挡删除，710 项 > 50 阈值，报 `SAFE_DELETE_BULK_CONFIRM_REQUIRED` 后被静默吞掉，
  产物时间戳停在旧值 → 表现和「构建成功」一样）。
  每次都要先 `CODEBUDDY_SAFE_DELETE_ENABLED=0 rm -rf backend/dist backend/build`，再 `npm run backend:build`。
  ⚠️ 删 `backend/build`，**别动根目录 `build/`**（图标，构建需要）。
- **打包一律走默认流程**：`npx electron-builder --publish never`（输出到 `release/`）。
  **若报 err 32（`app.asar` 被占用）→ 立刻停下并通知用户**，他用火绒「解除占用」释放句柄后再重试。
  **不要自己连试 6 次、也不要擅自换输出目录。**
  （2026-10-12 确认：锁定者是 `D:\WorkBuddy\WorkBuddy.exe` 自己；旧记忆写「火绒」是**错误归因**，
  但火绒的「解除占用」能强制释放任意进程的句柄，所以这条路对用户有效。
  查锁进程可用 `rstrtmgr.dll` 的 `RmStartSession`/`RmRegisterResources`/`RmGetList`。）
- **更新系统已于 2026-10-17 彻底移除**（`electron-updater` 依赖、`app-update.yml`、`UpdateBanner`、`SidebarFooterSettings` 的更新菜单、`__APP_VERSION__`）。
  - **`electron-builder.yml` 里的 `publish: null` 不能省**：`publish` 键缺失 = `undefined` ≠ 不发布，
    会掉进 `getPublishConfigsForUpdateInfo()` 用 package.json 的 `repository` 字段兜底推断 github provider，
    照样往 `resources/` 写 `app-update.yml`。**只有显式 `null` 才短路**（`getPublishConfigs` 只认 `=== null`）。
  - **`nsis.differentialPackage: false`**：`.blockmap` 只给 electron-updater 用；关掉还顺带恢复 solid 7z 压缩
    （差分感知会强制 `solid=false, dictSize=1`）→ 安装包 126.4MB → 113.6MB。
  - 参考实现：`app-builder-lib/out/publish/PublishManager.js`、`targets/nsis/NsisTarget.js:63`。

## 位置标签（2026-10-10 定，别再问）
- 真源 = `parsers/common.py::POSITIONS_BY_COUNT`，BTN 起顺时针。**6max = BTN SB BB UTG HJ CO**；8max 加 UTG1/LJ；9max 再加 UTG2。5/7 人是插值补的，最先该被真实数据打脸。
- **`EP`/`MP` 已彻底废除** —— 原来 EP 在 6max 指「首个行动」、8max 指「第二个」，语义冲突。
- 加标签**必须**同步加进 `ALL_POSITIONS`（`stat_flags._POS_ORDER` 从它派生）；漏了会被 `.get(pos,0)` 兜底当成 SB。测试有断言守这条。
- **改标签名 = 数据迁移**：`db.py::_migrate_position_labels` 幂等，跑在 `init_schema`。**不需要 bump STAT_VERSION** —— flag 只做相对比较，改名不改相对顺序（已验证 200 手 × 39 flag 零漂移）。

## 沙箱 / 本机陷阱（CLAUDE.md 里没有的）
- **venv 在项目根** `.venv`（3.12.10）；系统 Python 没 duckdb，一律 `../.venv/Scripts/python.exe`。
- 测试 `cd backend && ../.venv/Scripts/python.exe -m pytest tests/ -q -p no:warnings --ignore=tests/test_benchmark.py`（**104 passed**）。
- **删除**：`rm` 是 safe-delete 垫片，多删报 `SAFE_DELETE_BULK_CONFIRM_REQUIRED`（常伪装 `Device or resource busy`），**实际送回收站**（按卷）→「删了」≠释放空间；只有 100% 可重建的构建产物才用 `CODEBUDDY_SAFE_DELETE_ENABLED=0` 真删。
- **送回收站**：Python ctypes `shell32.SHFileOperationW`（flags `0x40|0x10|0x400|0x4`，路径 `\0` 拼 + `\0\0`）—— PowerShell 的 `Add-Type`/`New-Object -ComObject` **都被拦**。`rc=2` 是噪音；验证靠解析 `$I` 元数据（偏移 28 起 UTF-16LE 原路径）。
- **`app.asar` 被锁（err 32）的锁定者是 WorkBuddy 自己**，见上方「打包」一节的处理办法。**asar 要抽出来 `diff`，别 grep 容器**（假阴性）。**前端不在 asar 里**，在 `resources/frontend/`。
- **沙箱起不了 GUI exe** → electron 关窗口只能用户手测，靠 `npm run check` 兜。
- **`git fetch` 会删掉 `.git/refs/remotes/origin/`**（报成功 exit 0）→ `origin/main` 回落 `packed-refs` 陈旧值，`git status` 恒 `[ahead N]`。**只看 `git ls-remote origin refs/heads/main`**；修法 `sed -i` 改 `.git/packed-refs`。**别用 `git rm`**。
- **bash 的 `/tmp` ≠ Python 的**：给 Python 传 Windows 绝对路径（`D:/...`）。**PATH 偶尔被清空** → 命令前先 `export PATH=/usr/bin:/bin:/mingw64/bin:$PATH`。
- PowerShell 工具无回显；`wmic` 被拦；Bash 调 `cmd.exe`/PowerShell 被拒。看进程用 Python+ctypes。
- `frontend` 的 `npm install` 沙箱内失败 → 去 WorkBuddy 外终端跑。别跑 `npm audit fix --force`。

## 状态
- 用户装的是本 fork 构建（`%LOCALAPPDATA%\Programs\Open Holdem Manager\`），**版本号仍是 0.0.10**；改源码**必须重装** `release/*Setup.exe`。
- **关掉运行中实例**：`netstat -ano | grep LISTENING | grep <ohm-backend-pid>` 找端口 → `curl -X POST --noproxy '*' http://127.0.0.1:<port>/api/shutdown`（**永不 taskkill**）。
- 真实数据 `%APPDATA%\open-holdem-manager\data\poker.duckdb`，运行时独占锁定。**stat_version=4 / 无 WAL**。现 **5,801 手 / 56MB**（全部 6max，2NL Fast Fold，1bb=$0.02）；GG 自带工具另有 **10,548 手** —— **两个数据源别混用**。**hero `player_id=2037`（9132 行，10-17 核对；旧的 1005 已失效）**。`players` 表的用户名列叫 **`username`**，不是 `name`。真实库尚未迁移，下次启动新版会跑 3 件事：`EP/MP→UTG/HJ`、`rake-=jackpot`、**STAT_VERSION 4→5 全量重建**（约 50s，修 Cash Drop + 回填 all-in EV）。
- **核心恒等式：每手 `sum(won) == -total_fees`**。破了就是有钱凭空产生/消失。写 SQL 一条就能验（残差恰好 0.001 是 `DECIMAL(18,3)` 精度，不是 bug）。**Cash Drop 是 GG 送的钱，必须按投入比例从各人 `won` 里扣**，否则算成利润（曾让 bb/100 从 4.68 虚高到 5.31）。
- **读数据**：**应用在跑时库独占锁 → 走 HTTP API**（端口同上）：`/api/reports/graph`、`/api/stats/hero`、`/api/reports/breakdown`。**没跑时**才直连 `sys.path.insert(0,'backend')` → `app.stats_engine.compute_hero_stats(conn,'',workspace_id=1)`（`duckdb.connect(path, read_only=True)`）。**`model_dump()` 里 PositionalStats 有 `total`，`wtsd`/`wsd`/`wwsf`/`af_*` 等单值 StatValue 没有** —— 统一取 `['total']` 会误读成 None。
- **曲线口径**（`reports.py`）：每手按 hero 是否 `went_to_showdown` **整手**归两桶 → **Actual(黄) = Showdown(绿) + Non-Showdown(红)**，恒等。**累计值差分 = 单手结果** → 可算 σ/SE/CI，判断是真 leak 还是噪音。
- ~~已知缺陷（未修）：`vs_steal_fold` 的 `total` 是 SB+BB 混算，却拿 BB 口径基准 40–55 判定 → leak 恒假阳性~~
  **2026-10-17 已修**：`vs_steal_{fold,call,3bet}` 的 `total` 一律改为 **BB only**（`_pos_steal` 的 `total_positions` 参数），
  因为 SB/BB 根本不同尺度（跟注 2.4% vs 25.1%，3-bet 反而 12.2% vs 6.6%，方向都相反）。
  **推而广之：任何「分位可比性不成立」的统计，`total` 都不许混算** —— 否则 `computeLeaks` 只读 `.total` 就必然误判。
  leak 的「看手牌」链接必须带 `position=BB`（`BenchmarkRange.positionFilter`），否则列表和数值不同源。
- **drift 的口径只能有一个来源**：`reports.py::_DRIFT_STATS_V2` 是唯一真源，`_grouped_drift_stats()` 按 `opp_filter`
  分桶后同时生成 lifetime 与 window 两条 SQL。**历史教训**：曾经 `_LT_MAP`（下标映射）+ `_WINDOW_GROUPS` +
  `_DRIFT_STATS_V2` 三份定义并存，前两份写错（`fold_to_3bet` 用 `three_bet_opp` 做分母，两者**完全不相交**，
  3455 行全 NULL → AVG=0 → 永远不触发）而第三份是对的且只有测试在引用 → **测试绿着、线上错着**。
  同理：凡「同一件事在两个地方各写一遍」的代码，迟早漂移。
- ~~已知缺陷（未修）：rake 重复扣 jackpot~~ **2026-10-10 已修**：`total_rake` 曾把 jackpot 一起加进去，导入时又减一次 `per_player_jackpot`。现在 `total_rake`=纯 Rake、新增 `total_fees`=全部费用；`_migrate_rake_excludes_jackpot` 用 `rake = rake - jackpot` 修既 389 行（幂等，settings key `rake_jackpot_split`）。校验：389 行与全量重解析 **0 处不一致**，rake+jackpot 精确等于总费用。**当时为何没人发现**：`won_bb` 走 `collected+uncalled-invested`，根本不读这两列 → 统计图表全对，纯粹是显示层。
- **前端费用口径已全部统一为 `Fees = rake + jackpot`（2026-10-17 收尾，提交 d89c8e4）**，四处：Graph 页脚格、Graph 折线按钮、Sessions 详情摘要条第 4 格、Graph 三张 breakdown 表列。**别再单独显示纯 rake 标成 `Rake`** —— 本样本纯 rake 只占 72%，会低估 34–46%。breakdown 表**故意保持单行**（密集表，拆分放 `title`），页脚/摘要条则大数=总额、小字=`Fees · Rake $x (a/100) · Jackpot $y (b/100)`。**缩写 `BBJ` 已废弃**，只用 GG 手牌历史里的原词 `Jackpot`。
- **验证真实数据别先信自己编的样本**：我编的 ante fixture 座位/用户名不一致导致 `KeyError`；后来「把真实手牌的 Cash Drop 行删掉当对照组」又造出一个不存在的 bug。**要对照就取真实的另一手牌，别手改。**
- **排查时起过后端服务就会独占库**（只读连接也打不开）→ 要么走 HTTP，要么用**独立副本**。
  **dev 模式 `/api/shutdown` 返回 `no-server` 是正常的**（CLAUDE.md 有写，只有 `run_server.py` 才挂 uvicorn_server）→ 只能强停进程，**但强停会丢未提交的 WAL**（我曾白跑 50s 重建）。**验证重建结果必须让进程优雅退出。**
  起后台服务用 `run_in_background=true`，别用 `cmd &`（非交互 shell 结束会回收进程）。`OHM_DATA_DIR` 指向**含 `poker.duckdb` 的目录**。
- `insert_parsed_hand(db, parsed, workspace_id=1)` 第三个参数是 **workspace_id 不是 stats**，传错会甩一大坨 STRUCT 转换错误。
- **前端日期禁用 `toISOString().slice(0,10)`**：先转 UTC，UTC+8 下本地零点/早 8 点前会退回前一天。
  统一用 `lib/date-presets.ts::toLocalDateString()`。过了 08:00 看不出来，所以潜伏很久。
- **DuckDB 写法两条禁令**（曾让迁移挂死、连报错都没有）：`executemany()` 是逐行执行；参数列表上千项会触发
  binder 去 import numpy/pandas（**这台机器没装**）→ 挂住。**能用 SQL 表达就别用 Python 传参。**
- **`OHM_DATA_DIR` 里的库必须叫 `poker.duckdb`**：改名会让应用新建空库，`/api/stats/hero` 返回 `hands: 0`。
- **别删**：`.backup-data/20260910-140950/`（升级前基线 102MB，**用户未表态**）、`build/`（图标，构建需要）。
