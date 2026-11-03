# 悬浮翻译 FloatTrans · macOS 版

Windows 版的 macOS 移植。界面、操作、翻译引擎都和 Windows 版一致，平台层换成了 macOS 原生能力：

| 功能 | Windows 版 | macOS 版 |
| --- | --- | --- |
| 截屏取词 | GDI BitBlt + Windows.Media.Ocr | `screencapture` + Vision 框架（本地离线） |
| 全局热键 | `RegisterHotKey` | Carbon `RegisterEventHotKey` + 按键状态轮询兜底（**不需要**辅助功能授权） |
| 发音 | MCI 播放 mp3 | `afplay`，失败退回 `say` |
| 开机自启 | 注册表 Run 键 | `~/Library/LaunchAgents` plist |
| 圆角 | `SetWindowRgn` | 不做圆角（macOS 无对应的公开 API） |
| 配置文件 | `%APPDATA%\FloatTrans` | `~/Library/Application Support/FloatTrans` |

翻译核心（有道 / Google / MyMemory、语言与引擎设置、有道只做中外互译的判断）放在 `trans_core.py`，**和 Windows 版共用同一份代码**。

---

## 一、准备（只做一次）

### 1. 装 Python 3

你的机器是 macOS 12.6.1（Monterey）+ Intel，装 **python.org 的 Python 3.12** 最省事（自带 tkinter）：

<https://www.python.org/downloads/macos/>

> 不要用系统自带的 `/usr/bin/python3`：那套 Python 的 tkinter 绑的是废弃的 Tk 8.5，界面会很难看，甚至直接报错。
> 也不要用 `brew install python`，Homebrew 的 Python 默认**不带** tkinter，还得另外装 `python-tk`。

### 2. 把文件拷到 Mac

整个项目文件夹拷过去最省事；只想拷最小的，这 5 个是必须的：

```
float_trans_mac.py     # 主程序（macOS 版）
trans_core.py          # 翻译核心（Windows/macOS 共用）
mac_ocr.py             # 截屏 + Vision 识别
mac_hotkey.py          # Carbon 全局热键
start_mac.command      # 双击启动
```

可选：`check_mac.command`（自检）、`build_mac.sh`（打包成 `.app` / `.dmg`）、`icon_mac.png`（1024×1024 图标）。

### 3. 给脚本执行权限

打开「终端」，`cd` 到文件夹后执行一次：

```bash
chmod +x start_mac.command check_mac.command build_mac.sh
```

### 4. 双击 `start_mac.command`

第一次会提示「无法打开，因为来自身份不明的开发者」——**右键 → 打开 → 仍要打开**。之后就能双击了。

脚本会自动做三件事：检查 python3 / 检查 tkinter / 缺 `pyobjc` 就自动 `pip install`（第一次会慢一两分钟）。

---

## 二、授权（截图取词必须）

第一次点「截图」时，系统会弹窗要求「屏幕录制」权限：

**系统设置 → 隐私与安全性 → 屏幕录制** → 勾选 **Terminal**（用脚本跑）或 **FloatTrans**（用 .app 跑）。

勾完要**完全退出终端再重开**（⌘Q，不是关窗口），权限才生效。

> 悬停取词不需要辅助功能授权——修饰键状态和光标位置用的是 Quartz 事件 API，读文字靠截屏 + Vision。
> 但如果**没给屏幕录制权限**，截出来的是桌面壁纸而不是真实画面，识别结果会是空的。

---

## 三、怎么用

和 Windows 版一样：

| 操作 | 说明 |
| --- | --- |
| 直接输入 | 输入框敲单词或句子，回车翻译 |
| 复制即译 | 任意软件里选中文字 ⌘C，面板自动翻译 |
| 截图取词 | ⌃⌥S 或点「截图」，拖框选区域，松手自动识别翻译 |
| 悬停取词 | 点「悬停」打开，之后**按住 Option** 把鼠标停在文字上，旁边弹小气泡 |
| 呼出面板 | ⌃⌥T（被占用会自动换 ⌘⌥T / ⌃⇧T / ⌘⇧T） |
| 收起 | 点标题栏 ─ 变成小圆球，点圆球展开 |
| 发音 / 复制 | 底部按钮，或双击译文复制 |
| 字号 | 按住 Control 滚滚轮 |
| 语言 / 引擎 | 输入框下面那条 `源 自动 → 中文 · 引擎 自动`，点一下就选 |
| 设置 | ⚙：不透明度、字号、自动翻译、悬停取词、开机自启、语言、引擎 |

**看视频全屏也能浮在上面**：程序给窗口设了 `canJoinAllSpaces + fullScreenAuxiliary`，播放器全屏时会一起显示。如果没浮起来，多半是播放器用了独占全屏（少见），换成窗口化全屏即可。

---

## 四、出问题先跑自检

双击 `check_mac.command`，或者在终端里：

```bash
python3 float_trans_mac.py --check
```

它会打印：Python 版本、tkinter 是否可用、取词模块（pyobjc）状态、屏幕录制权限、全局热键注册结果、配置目录。**把这段输出发回来就能定位问题。**

日志在：

```
~/Library/Application Support/FloatTrans/app.log
```

### 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| 双击脚本只闪一下 | 没有执行权限 → `chmod +x start_mac.command`；或没有 python3 → 装 python.org 版本 |
| 窗口一片空白 / 字体发虚 | 用了系统自带 python3 的 Tk 8.5 → 换 python.org 的 Python 3.12 |
| 翻译报 `SSL: CERTIFICATE_VERIFY_FAILED` | 找不到根证书。v1.1.0 起会自动找 certifi / `/etc/ssl/cert.pem`，都找不到就降级为不校验（日志里会记一行）。源码运行可手动补：`python3 -m pip install --user certifi` |
| 点「截图」没反应或识别为空 | 屏幕录制权限没给，或给了但没重启终端 |
| 截图时整屏全黑 | v1.1.0 已修：改为**先截屏再建窗口**（原来截到的是自己的黑窗口），并且不再依赖 macOS 不支持的 stipple 压暗 |
| 热键按了没反应 | v1.1.0 已修：Carbon 注册成功但 Tk 事件循环不派发，改成轮询按键状态（`CGEventSourceKeyState`，同样不需要辅助功能授权）。设置里能看到实际生效的组合 |
| 呼出热键「未注册」 | ⌃⌥T 被别的 App 占了，程序会自动试备用键；设置里能看到实际注册成什么 |
| 悬停取词不触发 | 要先点「悬停」按钮打开开关，然后**按住 Option** 且鼠标**停住不动**约半秒 |
| 窗口在全屏视频上不显示 | 换成播放器的「窗口化全屏 / 无边框窗口」 |
| 提示「取词模块缺失」 | `python3 -m pip install pyobjc-framework-Vision pyobjc-framework-Quartz` |
| 4G 内存有点卡 | 正常。Vision 首次识别要加载模型，约 1～2 秒，之后就快了；把「查词自动发音」关掉能省一点 |

---

## 五、打包成 .app / .dmg（可选）

先分清三个东西：

| 格式 | 是什么 | 说明 |
| --- | --- | --- |
| `.app` | 应用程序包（本质是个文件夹） | 真正能双击运行的程序 |
| `.dmg` | 磁盘映像（压缩容器） | 分发用：双击挂载 → 把图标拖进 Applications → 推出 |
| `.pkg` | 安装器 | 要写系统目录时才用，这里不需要 |

`.dmg` 不是程序格式，它里面装的还是 `.app`。日常自用有 `.app` 就够了；要发给别人或备份才需要 `.dmg`。

### 一条命令打包

```bash
chmod +x build_mac.sh      # 第一次
./build_mac.sh             # 生成 .app + .dmg
./build_mac.sh app         # 只生成 .app，不封 dmg
```

产出：

```
dist/FloatTrans.app            ← 拖进「应用程序」即可
dist/FloatTrans-1.0.0.dmg      ← 分发用
```

脚本做的事：装 PyInstaller + pyobjc → 用 `sips`/`iconutil` 生成 `.icns` → PyInstaller `--windowed` 打包 → ad-hoc 签名 → `hdiutil` 封 dmg（里面带一个指向 `/Applications` 的软链接，方便拖拽安装）。

> **必须在 Mac 上跑这个脚本**：PyInstaller 不能跨平台编译（Windows 上打不出 Mach-O 的 .app），ad-hoc 签名 `codesign` 也只有 macOS 有。我这边是 Windows，只能把脚本准备好。

### 装好后还要做两件事

1. **首次打开**：右键 → 打开（未签名，Gatekeeper 会拦一次）。
   如果提示「已损坏，无法打开」，在终端里执行：
   ```bash
   xattr -dr com.apple.quarantine /Applications/FloatTrans.app
   ```
2. **给屏幕录制权限**：系统设置 → 隐私与安全性 → 屏幕录制 → 勾选 **FloatTrans**（不是 Terminal，打包后权限按 app 单独记）。

> 注意：`.app` 里的「开机自动启动」开关会写 LaunchAgent 指向 app 内的 python，不如直接用系统设置 → 通用 → 登录项 里添加 FloatTrans.app 稳。


---

## 六、在 GitHub 上打包（不碰自己的 Mac 也行）

仓库里已经带好了工作流 `.github/workflows/build-mac.yml`，推上去就能在 GitHub 的 macOS 机器上打出 `.app` + `.dmg`，本机连 Python 都不用装。

### 关键：必须用 Intel 的 runner

| runner 标签 | 架构 | 能不能给你的 MacBook Air 2015 用 |
| --- | --- | --- |
| `macos-15-intel` | **Intel x86_64** | ✅ 就是它 |
| `macos-latest` / `macos-14` / `macos-15` | arm64 (M 芯片) | ❌ 打出来的包在 Intel 上跑不了 |
| `macos-13` | — | ❌ 已下线 |

PyInstaller 不能跨架构编译，所以工作流里写死了 `runs-on: macos-15-intel`。工作流最后会用 `file` 打印主程序的架构，确认是 `x86_64`。

### 步骤

1. 在 GitHub 建一个空仓库（Public / Private 都行，Private 也能免费用 macOS runner，只是计入额度）
2. 本地已经初始化好 git 仓库并提交过了，把远程地址加上再推：

```bash
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

3. 打开仓库页面 → **Actions** → 左侧 **Build macOS app** → 右上 **Run workflow** → 运行
4. 等 3～5 分钟，进这次运行，页面底部 **Artifacts** 下载 `FloatTrans-macOS-x86_64`：
   - 里面有 `FloatTrans-1.1.0.dmg`（双击挂载后拖进「应用程序」）
   - 还有 `FloatTrans-app-x86_64.zip`（解压得到 `.app`，省一步挂载）

> 如果某次卡在「上传产物」这一步报 `Failed to CreateArtifact ... Request timeout`，
> 那是 GitHub 的 artifact 存储服务临时超时，**和代码无关**（打包其实已经成功）。
> 直接在该次运行页右上角点 **Re-run failed jobs** 重跑即可。

> 推送前如果想把提交作者改成你自己：
> `git config user.name "你的名字" && git config user.email "你的邮箱" && git commit --amend --reset-author --no-edit`

### 为什么仓库里要放 `.gitattributes`

`*.sh` / `*.command` 强制 `eol=lf`。Windows 上 `core.autocrlf=true` 时 Git 会把它们转成 CRLF，到 macOS 上 bash 直接报：

```
bad interpreter: /bin/bash^M: no such file or directory
```

这个坑很隐蔽，`.gitattributes` 已经挡掉了。

### 下载后注意

- 产物是 **ad-hoc 签名**（没有开发者证书），首次打开仍要右键 → 打开；提示「已损坏」就 `xattr -dr com.apple.quarantine /Applications/FloatTrans.app`
- 打包后的权限是按 app 单独记的，要在「屏幕录制」里勾 **FloatTrans**
- Artifact 默认保留 30 天，想要长期下载就建个 Release 把 dmg 传上去

### 想同时出 Apple Silicon 版本

把工作流里注释掉的那个 `build-macos-arm` job 打开即可（`runs-on: macos-latest`）。两个包不能混用，按机器选。

---

## 七、已知限制

- **只支持主屏**的截图取词（多显示器时框选范围是主屏）。
- **没有圆角**：macOS 没有像 Windows `SetWindowRgn` 那样的公开接口，硬做要用私有 API，不值得。
- 有道免费接口**只做中外互译**，外外互译（英→日等）会自动跳到 Google / MyMemory（详见 README.md 的实测表）。
- 有道免费接口对繁体中文也返回 102，选「繁体中文」时会跳过有道。

---

## 八、这个版本没法在 Windows 上验证的部分

代码是在 Windows 上写的，界面布局、翻译流程、降级逻辑都实跑过（在 Windows 上能启动 macOS 版界面，只是取词/热键降级），但下面这些**必须在你的 Mac 上才能确认**：

1. Vision OCR 的识别效果和速度
2. Carbon 全局热键能否正常注册（Tk 的事件循环是否转发 Carbon 事件）
3. `screencapture -R` 的坐标与 Retina 缩放（你这台是 1440×900 非 Retina，缩放比应该是 1）
4. 无边框窗口能否正常拿到键盘焦点

如果哪一项不对，跑 `check_mac.command` 把输出发我，我按实际报错改。
