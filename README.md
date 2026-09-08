# Yunyu 课程自动播放脚本

基于 Playwright 的课程播放守护脚本，可用于自动播放/续播视频课程。请仅在获得课程平台和授课方许可的情况下使用。

## 适用页面

- 默认页面：`https://courses.gdut.edu.cn/mod/fsresource/view.php?id=200644`

## 功能

- 自动打开课程页面
- 支持自动执行“登录 -> 统一身份认证 -> 账号密码提交”
- 自动检测并播放页面或 iframe 内视频
- 自动设置倍速（默认 1.5x）
- 自动尝试点击常见“继续学习/继续播放/关闭/下一节”按钮
- 自动按课程目录切换到下一条 `fsresource` 网课资源
- 循环守护，视频暂停后会尝试恢复播放

## 安装

```bash
git clone https://github.com/linfish330/gdut_course_automation.git
cd gdut_course_automation
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
playwright install
```

## 可选环境变量

```bash
LOGIN_ENTRY_URL="https://courses.gdut.edu.cn/?service=https://courses.gdut.edu.cn"
COURSE_URL="https://courses.gdut.edu.cn/mod/fsresource/view.php?id=200644"
USERNAME="你的学号/工号"
PASSWORD="你的密码"
PLAYBACK_RATE=1.5
POLL_INTERVAL_SECONDS=2
AUTO_NEXT_COURSE=true
NO_VIDEO_RETRY_LIMIT=6
```

## 运行

```bash
python yunyu_automation.py
```

## 说明

- 如果遇到验证码或风控校验，脚本会提示手动接管后继续。
- 运行异常时会打印堆栈，便于定位页面结构变化问题。
- 不要把真实账号、密码或其他凭据提交到 Git；建议通过本地 `.env` 文件或环境变量提供配置。

## 开源协议

本项目采用 [MIT License](LICENSE)。
