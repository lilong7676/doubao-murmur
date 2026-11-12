# 豆包语音输入 (Doubao Murmur) - Linux/SteamOS 版

使用豆包 ASR（自动语音识别）服务实现全局语音转文字输入。适用于 SteamOS Desktop Mode (Steam Deck) 和其他 Linux 发行版。

> **macOS 用户**: 请使用项目根目录的 macOS 版本。

## ✨ 功能

- ⌨️ **全局热键**: 右 `Alt` 开始/停止录音，`ESC` 取消（X11 桌面下任何应用均可用；Wayland 见 [下文](#-wayland--gnome-用户须知)）
- 🎮 **手柄一键语音输入**: 在 Steam Input 桌面布局中把手柄按键映射为右 Alt 即可（见下文）
- 📝 **实时转写**: 说话时文字实时显示在屏幕顶部悬浮条中（不抢焦点、不挡输入）
- 📋 **自动粘贴**: 识别结果自动粘贴到当前输入框；终端自动改用 `Ctrl+Shift+V`
- 🔐 **登录一次**: 通过内置 WebView 登录豆包，凭证持久化存储
- 🛎 **系统托盘**: 常驻托盘 🎤 图标，左键打开控制面板，右键菜单登录/退出（KDE 等支持 StatusNotifierItem 的桌面）
- ⌨️ **屏幕触摸键盘**: SteamOS 桌面模式下的可拖动、可缩放软键盘，专为掌机握持设计的**分体**与**左/右单手**布局（见下文）

## ⌨️ 屏幕触摸键盘

SteamOS 桌面模式自带的虚拟键盘不能移动、不能缩放，常挡住输入框。本项目内置了一个可拖动、可缩放的触摸软键盘，把按键注入到当前焦点窗口（不抢焦点），适合 Steam Deck 等掌机。

<p align="center">
  <img src="../docs/screenshots/keyboard_full.png" width="760" alt="屏幕键盘 - 全键模式">
</p>

- **打开方式**: 托盘菜单「⌨ 软键盘」，或全局快捷键 `Ctrl + Super + Shift`（三个修饰键同时按住）
  - 快捷键和桌面环境（如 KDE）的快捷键冲突、或者用不到软键盘时，可在托盘菜单或控制面板里取消勾选「Ctrl+Super+Shift 呼出软键盘」关闭它；托盘菜单仍可打开软键盘。设置保存在 `~/.config/doubao-murmur/settings.json`
- **布局模式**（顶部按钮循环切换）:
  - **全键**: 整块标准键盘，居中
  - **分体**: 左右两个半键盘分别贴住屏幕两侧、中间留空——掌机双手握持时两个拇指正好够到
  - **左手 / 右手**: 整块键盘压成手机宽度、吸附到屏幕最左 / 最右，单拇指即可输入
- **移动 / 缩放**: 拖动顶部条移动，右上角 `⤡` 拖拽缩放，或 `S / M / L` 一键预设；位置和模式会被记住
- **符号层**: `?123` 切换符号层；`Shift / Ctrl / Alt` 为吸附式（点一下临时生效、双击锁定），可打出 `Ctrl+C`、`Ctrl+Shift+V` 等组合键
- 纯 ASCII / 拉丁键盘——中文请用本项目的语音输入

<p align="center">
  <img src="../docs/screenshots/keyboard_split.png" width="820" alt="屏幕键盘 - 分体模式">
</p>

> 仅 X11 桌面（如 SteamOS 的 KDE Plasma）；按键注入依赖宿主机的 `xdotool`（SteamOS 自带）。

## 📋 系统要求

- **OS**: SteamOS 3 / Arch Linux / 其他支持 GTK4 的 Linux 发行版
- **音频**: PipeWire (SteamOS 默认) 或 PulseAudio
- **Python**: 3.11+
- **桌面环境**: KDE Plasma (推荐) 或 GNOME
- **显示协议**: **X11 会话开箱即用**；Wayland 会话需要额外配置且功能受限，见 [下文](#-wayland--gnome-用户须知)

## 🚀 安装

### 方法一: Flatpak (推荐)

从 [Releases](../../../../releases) 页面下载 `doubao-murmur.flatpak`：

```bash
flatpak install --user doubao-murmur.flatpak
flatpak run com.doubao.Murmur
```

WebKitGTK 等依赖打包在 GNOME runtime 中，**不受 SteamOS 系统更新影响**。

自动粘贴依赖宿主机的 `xdotool`（SteamOS 自带）。

> **Wayland 桌面（GNOME 等）还需要把自己加进 `input` 组并重新登录**，
> 否则热键不工作。详见 [Wayland / GNOME 用户须知](#-wayland--gnome-用户须知)。

也可以从源码自行构建：

```bash
cd linux
flatpak install flathub org.flatpak.Builder org.gnome.Platform//49 org.gnome.Sdk//49
make flatpak-install
```

### 方法二: 直接运行 (开发模式)

```bash
# 1. 安装系统依赖（SteamOS 需要先 sudo steamos-readonly disable）
sudo pacman -S python python-pip python-gobject gtk4 webkitgtk-6.0 xdotool

# 2. 安装 Python 依赖
cd linux
python3 -m venv --system-site-packages .venv
.venv/bin/pip install websockets sounddevice python-xlib

# 3. 运行
PYTHONPATH=src .venv/bin/python -m doubao_murmur
```

> ⚠️ SteamOS 系统更新会清除 pacman 安装的包，届时需重装 `webkitgtk-6.0`。推荐用 Flatpak。

## 🎮 使用方法

1. 首次启动会弹出控制面板，点击 **登录豆包**，在 WebView 中完成登录
2. 登录后应用驻留在系统托盘（右下角 🎤 图标）
3. 将光标放到任意输入框，按 **右 Alt**，屏幕顶部出现悬浮条，开始说话
4. 悬浮条实时显示识别文字（超长时自动滚动显示最新内容）
5. 再按一次 **右 Alt** 结束，文字自动粘贴到输入框
6. 想中途放弃按 `ESC`，不复制也不粘贴

> 悬浮条本身只显示文字，没有按钮 —— 早期版本那个屏幕底部的 ⏹ 一键录音按钮
> 已在 v1.4.6 移除。开始和结束都只能用热键。

### 快捷键

| 快捷键 | 功能 |
|--------|------|
| 右 Alt 键 | 开始 / 停止录音 |
| ESC 键 | 取消当前录音（不粘贴） |
| Ctrl + Super + Shift | 显示 / 隐藏软键盘（可在托盘菜单中关闭） |

### 🎮 手柄一键语音输入 (Steam Deck / 掌机)

桌面模式下 Steam 接管了手柄，原生按键事件不会透传，但可以让 Steam 把手柄按键转成键盘键：

1. 打开 **Steam → 设置 → 控制器 → 桌面布局 → 编辑**
2. 把 **R3（右摇杆按下）** 或任意顺手的按键 → 添加命令 → **键盘 → 右 Alt**
3. （可选）把 **B 键** → **键盘 → Escape**，用于取消录音

之后在任何应用里按 R3 即可开始/结束语音输入。应用通过 X11 层（XRecord）监听按键，
Steam 注入的按键和物理键盘都能识别，无需额外权限。

## ⚠️ Wayland / GNOME 用户须知

本项目最初是为 SteamOS 的 KDE Plasma（X11）写的，**在 Wayland 会话下功能受限**。
Fedora Silverblue / Bluefin、Ubuntu 22.10+、Fedora Workstation 等默认 GNOME Wayland
的系统都属于这一类。

**为什么右 Alt 没反应**

- 默认的按键监听走 X11 的 XRecord 扩展。Wayland 会话下应用跑在 XWayland 里，
  XRecord 只能看到送进 XWayland 的按键 —— 焦点在原生 Wayland 窗口时，按键
  压根不经过 XWayland，所以监听不到。
- 后备的 evdev 监听读 `/dev/input/event*`，需要额外授权（见下）。

**让热键能用**

```bash
# 1. 加入 input 组（读 /dev/input 需要），之后必须注销重新登录
sudo usermod -aG input "$USER"

# 2. Flatpak 用户：v1.5.0 之后的包已内置 --device=input，无需操作。
#    v1.5.0 及更早的版本手动放开：
flatpak override --user --device=input com.doubao.Murmur
```

重新登录后启动应用，日志里出现 `evdev listener active` 就说明通了。

**自动粘贴仍然不行**

粘贴依赖 `xdotool`（X11 专用）或 `ydotool`（需要 `ydotoold` 常驻）。
Wayland 下 xdotool 粘不进原生窗口；装了 ydotool 并启动 `ydotoold` 才能自动粘贴：

```bash
sudo systemctl enable --now ydotoold
```

**没装 ydotool 也能用** —— 文字始终会复制到剪贴板，手动 `Ctrl+V` 即可
（终端里是 `Ctrl+Shift+V`）。

> Fedora Silverblue / Bluefin 这类原子系统装 ydotool 要 `rpm-ostree install` 加重启，
> 代价较大，建议直接用手动粘贴。

**托盘图标看不到**

原版 GNOME 不支持 StatusNotifierItem，需要装
[AppIndicator 扩展](https://extensions.gnome.org/extension/615/appindicator-support/)。
Bluefin 等定制版默认带这个扩展。没有托盘时，再执行一次
`flatpak run com.doubao.Murmur` 会唤出控制面板（单实例应用）。

**彻底的 Wayland 支持**（全局热键走 XDG GlobalShortcuts portal 或 libei、
粘贴走 wtype/libei）尚未实现，欢迎 PR。

## 🏗 项目结构

```
linux/
├── src/doubao_murmur/
│   ├── __main__.py          # 入口点
│   ├── app.py               # 主 GtkApplication
│   ├── app_state.py         # 应用状态管理
│   ├── config.py            # 配置常量
│   ├── gpu_quirks.py        # NVIDIA 渲染兼容处理
│   ├── asr_client.py        # WebSocket ASR 客户端
│   ├── audio_capture.py     # 麦克风音频采集
│   ├── transcription.py     # 录音状态机
│   ├── params_store.py      # 凭证持久化
│   ├── hotkey/              # 输入管理
│   │   ├── manager.py       # 热键管理器（统一封送到 GTK 主线程）
│   │   ├── overlay_button.py # 屏幕 PTT 按钮
│   │   ├── x11_listener.py  # X11/XRecord 全局键监听（主用）
│   │   └── evdev_listener.py # /dev/input 监听（非 X11 后备）
│   ├── ui/                  # 用户界面
│   │   ├── overlay.py       # 录音悬浮窗
│   │   ├── windowing.py     # X11 置顶/定位/无焦点处理
│   │   ├── tray_icon.py     # 系统托盘 + 控制面板
│   │   ├── sni_tray.py      # StatusNotifierItem/dbusmenu 纯 DBus 实现
│   │   └── login_window.py  # WebView 登录
│   ├── paste/               # 剪贴板/粘贴
│   │   └── paste_helper.py
│   └── resources/           # JS 注入脚本
├── flatpak/                 # Flatpak 打包
├── tests/                   # 单元测试
└── run.sh                   # 开发启动脚本
```

## 🔧 配置

配置文件存储在 `~/.config/doubao-murmur/asr_params.json`。

删除此文件可以强制重新登录：

```bash
rm ~/.config/doubao-murmur/asr_params.json
```

## ❓ 常见问题

### 启动后什么都没出现
- 已登录时应用驻留系统托盘，查看右下角是否有 🎤 图标，按右 Alt 即可录音
- 点击托盘图标或再启动一次应用（单实例）可打开控制面板
- 桌面不支持托盘（如原版 GNOME）时没有图标，功能不受影响
- 界面上**没有**麦克风按钮，开始录音只能按右 Alt

### 按右 Alt 没反应
- Wayland 会话（GNOME 默认）需要额外配置，见 [Wayland / GNOME 用户须知](#-wayland--gnome-用户须知)
- X11 会话下从终端启动看日志：`flatpak run com.doubao.Murmur`，
  应该出现 `X11 key listener active` 或 `evdev listener active`，两条都没有就是监听没起来
- 键盘布局没有右 Alt（只有 AltGr）时也能触发，但如果 AltGr 被输入法占用则可能失效

### 没有声音/录音失败
- 检查麦克风权限: `arecord -l` 查看可用设备
- 检查 PipeWire: `pactl info` 确认音频系统正常
- 测试 Python 音频: `python3 -c "import sounddevice; print(sounddevice.query_devices())"`

### 自动粘贴不工作
- 确认宿主机有 `xdotool`（SteamOS 自带；其他发行版 `sudo pacman -S xdotool`）
- 文字始终会复制到剪贴板，粘贴失败时可手动 `Ctrl+V`（终端 `Ctrl+Shift+V`）

### 手柄按键不触发
- 确认是在 **桌面布局**（Desktop Layout）里设置的映射，不是某个游戏的布局
- 确认映射的目标是键盘的 **右 Alt**（Right Alt），不是左 Alt

### WebView 无法加载
- 安装 WebKitGTK: `sudo pacman -S webkitgtk-6.0`
- 确认网络连接正常

### NVIDIA 显卡：窗口闪烁 / 登录页白屏
- 检测到 NVIDIA 闭源驱动时，应用会自动设置 `GSK_RENDERER=cairo`（消除 GTK 窗口闪烁）
  和 `WEBKIT_DISABLE_DMABUF_RENDERER=1`（修复登录页白屏）
- 自己设置过这两个环境变量时以你的设置为准，例如想换回 GPU 渲染：
  `flatpak run --env=GSK_RENDERER=ngl com.doubao.Murmur`
- 登录页提示 DNS 解析失败时，检查系统代理设置（GNOME：设置 → 网络 → 网络代理），
  代理主机一栏只填主机名，不要带 `http://` 前缀

## 📝 开发

```bash
# 安装开发依赖
pip3 install --user -e ".[dev]"

# 运行测试
make test

# 运行应用
make run
```

## 📄 许可证

MIT License - 详见项目根目录的 LICENSE 文件。

## 🔗 相关链接

- [macOS 版本](../README.md)
- [豆包官网](https://www.doubao.com)
- [SteamOS](https://www.steamdeck.com)
