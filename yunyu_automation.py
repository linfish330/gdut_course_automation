import asyncio
import logging
import os
import time
from urllib.parse import parse_qs, urlparse
from typing import Optional, Tuple

from dotenv import load_dotenv
from playwright.async_api import Page, async_playwright


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

load_dotenv()

DEFAULT_COURSE_URL = "https://courses.gdut.edu.cn/mod/fsresource/view.php?id=200644"
DEFAULT_LOGIN_ENTRY_URL = "https://courses.gdut.edu.cn/?service=https://courses.gdut.edu.cn"


async def _perform_login(page: Page, login_entry_url: str, username: str, password: str) -> None:
    """
    登录流程（按页面结构）：
    1) 打开 login_entry_url
    2) 点击 .forgotpass 内“登录”
    3) 点击“统一身份认证”
    4) 账号登录页填充用户名密码并提交
    """
    logger.info(f"打开登录入口: {login_entry_url}")
    await page.goto(login_entry_url)
    await page.wait_for_load_state("domcontentloaded")

    if "authserver.gdut.edu.cn" not in page.url:
        login_anchor = await page.query_selector(
            'div.forgotpass a[href*="/login/index.php"]'
        )
        if login_anchor:
            logger.info("点击首屏登录按钮")
            await login_anchor.click()
            await page.wait_for_load_state("domcontentloaded")
        else:
            logger.info("未找到首屏登录按钮，继续尝试后续认证流程")

    if "authserver.gdut.edu.cn" not in page.url:
        unified_auth_btn = await page.query_selector(
            'div.btninner-left:has(h4:has-text("统一身份认证"))'
        )
        if unified_auth_btn:
            logger.info("点击统一身份认证入口")
            await unified_auth_btn.click()
            await page.wait_for_load_state("domcontentloaded")
        else:
            logger.info("未找到统一身份认证按钮，可能已处于认证页")

    if "authserver.gdut.edu.cn" not in page.url:
        logger.warning("当前不在统一认证域名，可能已登录或页面结构变化")
        return

    user_input = await page.query_selector("#username")
    pass_input = await page.query_selector("#password")
    submit_btn = await page.query_selector("#login_submit")

    if user_input and pass_input and submit_btn:
        try:
            account_tab = await page.query_selector("#userNameLogin_a")
            if account_tab:
                await account_tab.click()
        except Exception:
            pass

        if username and password:
            logger.info("填充统一身份认证账号密码并提交")
            await page.fill("#username", username)
            await page.fill("#password", password)
            await submit_btn.click()
            await asyncio.sleep(1)
        else:
            print("【请接管】未配置 USERNAME/PASSWORD，请手动登录后按回车继续...")
            input()
    else:
        logger.info("未检测到账号密码登录表单，可能已登录或需人工处理")
        print("【请接管】请手动完成统一认证登录后按回车继续...")
        input()


async def _get_video_state(page: Page) -> Optional[Tuple[str, float, float, bool, bool]]:
    """返回 (scope, current, duration, paused, ended)，找不到视频时返回 None。"""
    page_video = await page.query_selector("video")
    if page_video:
        state = await page.evaluate(
            """() => {
                const v = document.querySelector("video");
                if (!v) return null;
                return {
                    current: Number(v.currentTime || 0),
                    duration: Number(v.duration || 0),
                    paused: Boolean(v.paused),
                    ended: Boolean(v.ended)
                };
            }"""
        )
        if state:
            return ("page", state["current"], state["duration"], state["paused"], state["ended"])

    for frame in page.frames:
        frame_video = await frame.query_selector("video")
        if frame_video:
            state = await frame.evaluate(
                """() => {
                    const v = document.querySelector("video");
                    if (!v) return null;
                    return {
                        current: Number(v.currentTime || 0),
                        duration: Number(v.duration || 0),
                        paused: Boolean(v.paused),
                        ended: Boolean(v.ended)
                    };
                }"""
            )
            if state:
                return (f"frame:{frame.url[:80]}", state["current"], state["duration"], state["paused"], state["ended"])

    return None


async def _play_all_videos(page: Page, rate: float) -> bool:
    """尝试在页面及各 iframe 内恢复播放并设置倍速。"""
    script = """
        (rate) => {
            const videos = Array.from(document.querySelectorAll("video"));
            if (!videos.length) return 0;
            for (const v of videos) {
                v.muted = true;
                v.playbackRate = rate;
                try { v.play(); } catch (_) {}
            }
            return videos.length;
        }
    """

    played = 0
    played += int(await page.evaluate(script, rate))
    for frame in page.frames:
        try:
            played += int(await frame.evaluate(script, rate))
        except Exception:
            continue

    return played > 0


async def _click_common_buttons(page: Page) -> bool:
    """点击常见阻塞按钮，例如继续学习、继续播放、关闭。"""
    selectors = [
        'button:has-text("继续学习")',
        'button:has-text("继续播放")',
        'button:has-text("继续观看")',
        'a:has-text("继续学习")',
        'a:has-text("下一节")',
        'button:has-text("下一节")',
        'button:has-text("关闭")',
        'button:has-text("确定")',
        'span:has-text("关闭")',
    ]
    for sel in selectors:
        try:
            node = await page.query_selector(sel)
            if node and await node.is_visible():
                await node.click()
                logger.info(f"已点击按钮: {sel}")
                return True
        except Exception:
            continue
    return False


async def _open_next_course_resource(page: Page) -> bool:
    """
    从课程目录里定位下一个 fsresource 资源并打开。
    优先级：
    1) 当前资源后面的未完成项（completion_incomplete / completion_none）
    2) 当前资源后面的任意资源
    3) 全列表第一个未完成项
    4) 全列表第一个资源
    """
    current_url = page.url

    next_href = await page.evaluate(
        """(currentUrl) => {
            const normalize = (url) => {
                try {
                    const u = new URL(url, location.origin);
                    return `${u.origin}${u.pathname}?id=${u.searchParams.get("id") || ""}`;
                } catch (_) {
                    return url || "";
                }
            };

            const curNorm = normalize(currentUrl);
            const nodes = Array.from(document.querySelectorAll('#course-index li[data-for="cm"]'));
            const items = nodes
                .map((li) => {
                    const a = li.querySelector('a.courseindex-link[href*="/mod/fsresource/view.php?id="]');
                    if (!a) return null;
                    const href = a.getAttribute("href");
                    const completion = li.querySelector('span[data-for="cm_completion"]');
                    const completionClass = completion ? completion.className : "";
                    return {
                        href,
                        hrefNorm: normalize(href),
                        completionClass
                    };
                })
                .filter(Boolean);

            if (!items.length) return null;

            const currentIndex = items.findIndex((x) => x.hrefNorm === curNorm);
            const notCompleted = (x) =>
                x.completionClass.includes("completion_incomplete") ||
                x.completionClass.includes("completion_none");

            const candidatesAfter = currentIndex >= 0 ? items.slice(currentIndex + 1) : items;

            let target = candidatesAfter.find(notCompleted)
                || candidatesAfter[0]
                || items.find(notCompleted)
                || items[0];

            if (!target) return null;
            if (target.hrefNorm === curNorm) return null;
            return target.href;
        }""",
        current_url,
    )

    if not next_href:
        logger.info("未找到可切换的下一课程资源")
        return False

    try:
        anchor = await page.query_selector(
            f'#course-index a.courseindex-link[href="{next_href}"]'
        )
        if anchor:
            await anchor.scroll_into_view_if_needed()
            await anchor.click()
            await page.wait_for_load_state("domcontentloaded")
            logger.info(f"已切换到下一课程: {next_href}")
            return True
    except Exception as click_error:
        logger.warning(f"侧边栏点击下一课程失败，改为直接跳转: {click_error}")

    try:
        await page.goto(next_href)
        await page.wait_for_load_state("domcontentloaded")
        logger.info(f"已通过 URL 切换到下一课程: {next_href}")
        return True
    except Exception as goto_error:
        logger.warning(f"跳转下一课程失败: {goto_error}")
    return False


def _extract_resource_id(url: str) -> Optional[str]:
    try:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        rid = query.get("id", [None])[0]
        return rid
    except Exception:
        return None


async def _is_current_resource_completed(page: Page) -> bool:
    """
    根据侧边栏 course-index 判断当前课程是否完成。
    判定规则：
    - completion class 包含 completion_complete / completion_completed
    - 或 data-value 为 100（百分比）/ 1（归一化进度）
    - 或完成图标包含 check
    """
    resource_id = _extract_resource_id(page.url)
    if not resource_id:
        return False

    result = await page.evaluate(
        """(rid) => {
            const li = document.querySelector(`#course-index-cm-${rid}`);
            if (!li) return {found:false, completed:false, reason:"not-found"};

            const completion = li.querySelector('span[data-for="cm_completion"]');
            if (!completion) return {found:true, completed:false, reason:"no-completion-node"};

            const cls = completion.className || "";
            if (cls.includes("completion_complete") || cls.includes("completion_completed")) {
                return {found:true, completed:true, reason:"class-complete"};
            }

            const iconClass = completion.querySelector("i")?.className || "";
            if (iconClass.includes("check")) {
                return {found:true, completed:true, reason:"icon-check"};
            }

            const raw = completion.getAttribute("data-value");
            const value = Number(raw);
            if (!Number.isNaN(value)) {
                if (value >= 100) {
                    return {found:true, completed:true, reason:`value-${value}`};
                }
                if (value === 1) {
                    return {found:true, completed:true, reason:"value-1"};
                }
                if (value > 1 && value < 100) {
                    return {found:true, completed:false, reason:`value-${value}`};
                }
            }

            return {found:true, completed:false, reason:`class-${cls}-value-${raw}`};
        }""",
        resource_id,
    )

    completed = bool(result and result.get("completed"))
    reason = result.get("reason") if isinstance(result, dict) else "unknown"
    logger.info(f"当前课程完成检测 id={resource_id} completed={completed} reason={reason}")
    return completed


async def _wait_for_video_or_user_takeover(page: Page) -> None:
    """若短时间未检测到视频，提示用户手动登录/进入播放页。"""
    for _ in range(12):
        if await _get_video_state(page):
            return
        await asyncio.sleep(1)

    print("【请接管】未检测到视频元素，请先手动登录并进入可播放页面后按回车继续...")
    input()


async def yunyu_automation() -> None:
    course_url = os.getenv("COURSE_URL", DEFAULT_COURSE_URL)
    login_entry_url = os.getenv("LOGIN_ENTRY_URL", DEFAULT_LOGIN_ENTRY_URL)
    username = os.getenv("USERNAME", "")
    password = os.getenv("PASSWORD", "")
    playback_rate = float(os.getenv("PLAYBACK_RATE", "1.5"))
    poll_interval = float(os.getenv("POLL_INTERVAL_SECONDS", "2"))
    auto_next = os.getenv("AUTO_NEXT_COURSE", "true").lower() == "true"
    no_video_retry_limit = int(os.getenv("NO_VIDEO_RETRY_LIMIT", "6"))

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            channel="msedge",
            args=["--mute-audio"],
        )
        page = await browser.new_page()

        try:
            await _perform_login(page, login_entry_url, username, password)

            logger.info(f"打开课程页面: {course_url}")
            await page.goto(course_url)
            await page.wait_for_load_state("domcontentloaded")

            await _wait_for_video_or_user_takeover(page)
            logger.info("进入自动播放守护模式")
            no_video_rounds = 0
            last_next_switch_at = 0.0

            while True:
                await _click_common_buttons(page)
                found = await _play_all_videos(page, playback_rate)
                state = await _get_video_state(page)

                if found and state:
                    scope, current, duration, paused, ended = state
                    logger.info(
                        f"[{scope}] 播放状态 current={current:.1f}s duration={duration:.1f}s paused={paused} ended={ended}"
                    )
                    no_video_rounds = 0

                    completed = await _is_current_resource_completed(page)
                    should_switch = (
                        auto_next
                        and completed
                        and ended
                        and (time.monotonic() - last_next_switch_at > 10)
                    )
                    if should_switch:
                        switched = await _open_next_course_resource(page)
                        if switched:
                            last_next_switch_at = time.monotonic()
                            await asyncio.sleep(2)
                            await _play_all_videos(page, playback_rate)
                elif found:
                    logger.info("已尝试恢复播放")
                    no_video_rounds = 0
                else:
                    logger.warning("未检测到视频元素，等待页面变化或手动切换章节")
                    no_video_rounds += 1
                    completed = await _is_current_resource_completed(page)
                    if (
                        auto_next
                        and completed
                        and no_video_rounds >= no_video_retry_limit
                        and (time.monotonic() - last_next_switch_at > 10)
                    ):
                        switched = await _open_next_course_resource(page)
                        if switched:
                            last_next_switch_at = time.monotonic()
                            no_video_rounds = 0

                await asyncio.sleep(poll_interval)

        except Exception as e:
            logger.error(f"执行过程中出现错误: {e}")
            import traceback

            traceback.print_exc()
        finally:
            logger.info("脚本结束，浏览器保持打开状态 1 小时便于人工检查")
            await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(yunyu_automation())
