import asyncio
import aiohttp
import random
import re
import base64
from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from urllib.parse import urljoin, quote

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
import astrbot.api.message_components as Comp
from astrbot.api import logger

# Models
@dataclass
class Book:
    """统一的书籍数据对象"""
    id: str
    title: str
    author: str
    score: str = "N/A"
    scorer: str = "0"
    status: str = "未知"
    platform: str = "未知"
    category: str = "未知"
    tags: List[str] = None
    categories: List[str] = None  # For HS-specific display of multiple categories
    word_count: Optional[float] = None
    update_time: str = "未知"
    synopsis: str = "无"
    link: str = ""
    image_url: Optional[str] = None
    reviews: List[Dict[str, Any]] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []
        if self.categories is None:
            self.categories = []
        if self.reviews is None:
            self.reviews = []

@dataclass
class SearchResult:
    """搜索结果对象"""
    books: List[Book]
    total_pages: int
    current_page: int = 1

# Constants
YS_PLATFORMS = {"他站", "本站", "起点", "晋江", "番茄", "刺猬猫", "纵横", "飞卢", "17K", "有毒", "息壤", "铁血", "逐浪", "掌阅", "塔读", "独阅读", "少年梦", "SF", "豆瓣", "知乎", "公众号"}
YS_CATEGORIES = {"玄幻", "奇幻", "武侠", "仙侠", "都市", "现实", "军事", "历史", "悬疑", "游戏", "竞技", "科幻", "灵异", "二次元", "同人", "其他", "穿越时空", "架空历史", "总裁豪门", "都市言情", "仙侠奇缘", "幻想言情", "悬疑推理", "耽美纯爱", "衍生同人", "轻小说", "综合其他"}
YS_STATUSES = {"连载中", "已完结", "已太监"}

YS_API1_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

YS_API2_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:143.0) Gecko/20100101 Firefox/143.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

HS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}

# State Manager
class SearchStateManager:
    """专门管理用户的搜索状态"""

    def __init__(self):
        self.states: Dict[str, Dict] = {}

    def get_state(self, user_id: str) -> Dict:
        """获取用户搜索状态"""
        if user_id not in self.states:
            self.states[user_id] = {
                "keyword": "",
                "current_page": 1,
                "max_pages": 1,
                "search_type": "",  # "ys" or "hs"
                "results": []  # 保存当前页的搜索结果
            }
        return self.states[user_id]

    def update_state(self, user_id: str, keyword: str, current_page: int, max_pages: int, search_type: str, results: List[Book] = None):
        """更新用户搜索状态"""
        state = self.get_state(user_id)
        state["keyword"] = keyword
        state["current_page"] = current_page
        state["max_pages"] = max_pages
        state["search_type"] = search_type
        if results is not None:
            # Convert Book objects to dictionaries for storage
            state["results"] = [
                {
                    "id": book.id,
                    "title": book.title,
                    "author": book.author,
                    "score": book.score,
                    "scorer": book.scorer
                } for book in results
            ]

    def get_item_by_number(self, user_id: str, number: int, search_type: str) -> Optional[Dict]:
        """根据序号和搜索类型获取书籍信息"""
        state = self.get_state(user_id)
        if state.get("search_type") != search_type:
            return None
        results = state.get("results", [])
        if not results or number < 1 or number > len(results):
            return None
        return results[number - 1]

from .sources.youshu_source import YoushuSource
from .sources.uaa_source import UaaSource

@register(
    "astrbot_plugin_youshusearch",  # 插件ID
    "Foolllll",                    # 作者名
    "优书搜索助手",                  # 插件显示名称
    "1.5",                         # 版本号 (updated for refactoring)
    "https://github.com/Foolllll-J/astrbot_plugin_youshusearch", # 插件仓库地址
)
class YoushuSearchPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        if config is None:
            config = {}
        
        # Initialize sources
        self.youshu_source = YoushuSource(config)
        self.uaa_source = UaaSource(config)
        
        # Initialize state manager
        self.state_mgr = SearchStateManager()
        
        # Initialize global session
        self.session = aiohttp.ClientSession()

    def _get_item_by_number(self, user_id: str, number: int, search_type: str) -> Optional[Dict]:
        """根据序号和搜索类型获取书籍信息"""
        return self.state_mgr.get_item_by_number(user_id, number, search_type)

    def _render_search_results(self, keyword: str, results: SearchResult, page_to_list: int) -> str:
        """渲染搜索结果列表"""
        results_per_page = 20
        start_num = (page_to_list - 1) * results_per_page + 1
        message_text = f"以下是【{keyword}】的第 {page_to_list}/{results.total_pages} 页搜索结果:\n"
        
        for i, book in enumerate(results.books):
            num = start_num + i
            message_text += f"{num}. {book.title}\n    作者：{book.author} | 评分: {book.score} ({book.scorer}人)\n"
        
        message_text += f"\n💡 请使用 `/ys ls <序号>` 查看详情"
        if results.total_pages > 1:
            message_text += f"\n💡 使用 /ys next 下一页，/ys prev 上一页"
        return message_text

    async def _render_ys_book_details(self, event: AstrMessageEvent, book: Book):
        """渲染优书网书籍详情并返回事件结果"""
        message_text = f"---【{book.title}】---\n"
        message_text += f"作者: {book.author}\n"

        if book.platform and book.platform != "未知":
            message_text += f"平台: {book.platform}\n"
        if book.category and book.category != "未知":
            message_text += f"分类: {book.category}\n"

        if book.tags:
            message_text += f"标签: {' '.join(book.tags)}\n"

        if book.word_count is not None:
            message_text += f"字数: {book.word_count / 10000:.2f}万字\n"
        else:
            message_text += f"字数: 无\n"

        scorer_text = f"{book.scorer}人评分" if book.scorer and book.scorer != '0' else "无人评分"
        message_text += f"评分: {book.score} ({scorer_text})\n"
        message_text += f"状态: {book.status}\n"
        message_text += f"更新: {book.update_time}\n"
        message_text += f"简介: {book.synopsis}\n"
        message_text += f"链接: {book.link}\n"

        if book.reviews:
            message_text += "\n--- 📝 最新书评 ---\n"
            for review in book.reviews:
                author = review.get('author', '匿名')
                rating = review.get('rating', '无')
                content = review.get('content', '无')
                message_text += f"{author} ({rating}分): {content}\n"

        chain = []
        if book.image_url:
            try:
                timeout = aiohttp.ClientTimeout(total=10)
                async with self.session.get(book.image_url, timeout=timeout) as img_response:
                    img_response.raise_for_status()
                    image_bytes = await img_response.read()
                image_base64 = base64.b64encode(image_bytes).decode()
                image_component = Comp.Image(file=f"base64://{image_base64}")
                chain.append(image_component)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"❌ 下载封面图片失败 (超时或链接无效): {e}")
                message_text = "🖼️ 封面加载失败\n\n" + message_text

        chain.append(Comp.Plain(message_text))
        yield event.chain_result(chain)

    async def _render_hs_book_details(self, event: AstrMessageEvent, book: Book):
        """渲染UAA书籍详情并返回事件结果"""
        message_text = f"---【{book.title}】---\n"
        message_text += f"作者: {book.author}\n"
        message_text += f"评分: {book.score}\n"
        message_text += f"状态: {book.status}\n"

        # Show categories as "题材" (like original)
        if hasattr(book, 'categories') and book.categories:  # If we have separate categories field
            message_text += f"题材: {' '.join(book.categories)}\n"
        elif book.category and book.category != "未知" and book.category != "UAA":  # If category field contains categories
            message_text += f"题材: {book.category}\n"

        # Show tags as "标签" (like original)
        if book.tags:
            message_text += f"标签: {' '.join(book.tags)}\n"

        message_text += f"更新: {book.update_time}\n"
        message_text += f"简介: {book.synopsis}\n"

        if book.reviews:
            message_text += "\n--- 📝 最新书评 ---\n"
            for r in book.reviews:
                author = r.get('author', '匿名')
                score = r.get('score', r.get('rating', '无'))
                time_str = r.get('time', r.get('createTimeFormat', ''))
                content = r.get('content', '')
                message_text += f"{author} ({score}分, {time_str}): {content}\n"

        chain = []
        if book.image_url:
            try:
                timeout = aiohttp.ClientTimeout(total=10)
                async with self.session.get(book.image_url, timeout=timeout) as img_response:
                    img_response.raise_for_status()
                    image_bytes = await img_response.read()
                image_base64 = base64.b64encode(image_bytes).decode()
                image_component = Comp.Image(file=f"base64://{image_base64}")
                chain.append(image_component)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"❌ 下载封面图片失败 (超时或链接无效): {e}")
                message_text = "🖼️ 封面加载失败\n\n" + message_text

        chain.append(Comp.Plain(message_text))
        yield event.chain_result(chain)

    @filter.command("ys")
    async def youshu_search_command(self, event: AstrMessageEvent):
        command_text = event.message_str.strip()
        command_parts = command_text.split()
        if not command_parts or command_parts[0].lower() != 'ys' or len(command_parts) < 2:
            yield event.plain_result("❌ 用法: /ys <书名> [序号 | -页码]\n💡 或使用 /ys ls <序号>、/ys next、/ys prev")
            return

        # 如果是 next、prev 或 ls，跳过处理，交给命令组子命令
        if len(command_parts) >= 2 and command_parts[1].lower() in ['next', 'prev', 'ls']:
            return

        user_id = event.get_sender_id()
        args = command_parts[1:]
        book_name, page_to_list, item_index = "", 1, None
        last_arg = args[-1] if args else ""
        if len(args) > 1 and last_arg.startswith('-') and last_arg[1:].isdigit():
            page_to_list = int(last_arg[1:])
            if page_to_list == 0: page_to_list = 1
            book_name = " ".join(args[:-1]).strip()
        elif len(args) > 1 and last_arg.isdigit():
            item_index = int(last_arg)
            if item_index == 0: item_index = None
            book_name = " ".join(args[:-1]).strip()
        else:
            book_name = " ".join(args).strip()
        if not book_name:
            yield event.plain_result("❌ 请提供有效的书名进行搜索。")
            return
        logger.info(f"用户 {user_id} 触发 /ys, 搜索:'{book_name}', 序号:{item_index}, 列表页:{page_to_list}")
        
        try:
            # Search for books
            search_result = await self.youshu_source.search(self.session, book_name, page_to_list)
            if search_result is None or not search_result.books:
                yield event.plain_result(f"😢 未找到关于【{book_name}】的任何书籍信息。")
                return

            if page_to_list > search_result.total_pages and search_result.total_pages > 0:
                yield event.plain_result(f"❌ 您请求的第 {page_to_list} 页不存在，【{book_name}】的搜索结果最多只有 {search_result.total_pages} 页。")
                return

            # Update user search state
            self.state_mgr.update_state(user_id, book_name, page_to_list, search_result.total_pages, "ys", search_result.books)

            if item_index is None and len(search_result.books) == 1 and search_result.total_pages == 1:
                # If only one result and only one page, show details directly
                selected_book = search_result.books[0]
                book_details = await self.youshu_source.get_book_details(self.session, selected_book.id)
                if book_details:
                    async for result in self._render_ys_book_details(event, book_details):
                        yield result
                else:
                    yield event.plain_result(f"😢 无法获取书籍详情。")
                return
            
            if item_index is None:
                # Show search results list
                message_text = self._render_search_results(book_name, search_result, page_to_list)
                yield event.plain_result(message_text)
            else:
                # Show details for specific book
                results_per_page = 20
                index_on_page = (item_index - 1) % results_per_page
                correct_page = (item_index - 1) // results_per_page + 1

                if correct_page != page_to_list:
                    yield event.plain_result(f"⏳ 序号【{item_index}】位于第 {correct_page} 页，正在为您跳转...")
                    page_to_fetch = correct_page
                    search_result = await self.youshu_source.search(self.session, book_name, page_to_fetch)
                    if search_result is None or not search_result.books:
                        yield event.plain_result(f"😢 未在第 {correct_page} 页找到关于【{book_name}】的信息。")
                        return
                    # Update state to correct page
                    self.state_mgr.update_state(user_id, book_name, page_to_fetch, search_result.total_pages, "ys", search_result.books)

                if not (0 <= index_on_page < len(search_result.books)):
                    yield event.plain_result(f"❌ 序号【{item_index}】在第 {page_to_fetch} 页上不存在。")
                    return

                selected_book = search_result.books[index_on_page]
                book_details = await self.youshu_source.get_book_details(self.session, selected_book.id)
                if book_details:
                    async for result in self._render_ys_book_details(event, book_details):
                        yield result
                else:
                    yield event.plain_result(f"😢 无法获取书籍详情。")
        except Exception as e:
            logger.error(f"搜索书籍 '{book_name}' 失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 搜索书籍时发生未知错误: {str(e)}")

    @filter.command("hs")
    async def hs_search_command(self, event: AstrMessageEvent):
        command_text = event.message_str.strip()
        command_parts = command_text.split()

        if not command_parts or command_parts[0].lower() != 'hs' or len(command_parts) < 2:
            yield event.plain_result("❌ 用法: /hs <书名> [序号 | -页码]\n💡 或使用 /hs ls <序号>、/hs next、/hs prev")
            return

        # 如果是 next、prev 或 ls，跳过处理，交给命令组子命令
        if len(command_parts) >= 2 and command_parts[1].lower() in ['next', 'prev', 'ls']:
            return

        user_id = event.get_sender_id()
        args = command_parts[1:]
        book_name, page_to_list, item_index = "", 1, None
        last_arg = args[-1] if args else ""
        if len(args) > 1 and last_arg.startswith('-') and last_arg[1:].isdigit():
            page_to_list = int(last_arg[1:])
            if page_to_list == 0: page_to_list = 1
            book_name = " ".join(args[:-1]).strip()
        elif len(args) > 1 and last_arg.isdigit():
            item_index = int(last_arg)
            if item_index == 0: item_index = None
            book_name = " ".join(args[:-1]).strip()
        else:
            book_name = " ".join(args).strip()
        if not book_name:
            yield event.plain_result("❌ 请提供有效的书名进行搜索。")
            return

        logger.info(f"用户 {user_id} 触发 /hs, 搜索:'{book_name}', 序号:{item_index}, 列表页:{page_to_list}")

        try:
            # Search for books
            search_result = await self.uaa_source.search(self.session, book_name, page_to_list)
            if search_result is None or not search_result.books:
                yield event.plain_result(f"😢 未找到关于【{book_name}】的任何书籍信息。")
                return

            if page_to_list > search_result.total_pages and search_result.total_pages > 0:
                yield event.plain_result(f"❌ 您请求的第 {page_to_list} 页不存在，【{book_name}】的搜索结果最多只有 {search_result.total_pages} 页。")
                return

            # Update user search state
            self.state_mgr.update_state(user_id, book_name, page_to_list, search_result.total_pages, "hs", search_result.books)

            if item_index is None: # 显示列表
                results_per_page = 20
                start_num = (page_to_list - 1) * results_per_page + 1
                message_text = f"以下是【{book_name}】的第 {page_to_list}/{search_result.total_pages} 页搜索结果:\n"
                for i, book in enumerate(search_result.books):
                    num = start_num + i
                    score_value = book.score
                    if isinstance(score_value, (int, float)):
                        score = f"{score_value:.2f}"
                    else:
                        score = 'N/A'

                    message_text += f"{num}. {book.title}\n    作者：{book.author} | 评分: {score}\n"

                message_text += f"\n💡 请使用 `/hs ls <序号>` 查看详情"
                if search_result.total_pages > 1:
                    message_text += f"\n💡 使用 /hs next 下一页，/hs prev 上一页"
                yield event.plain_result(message_text)
            else: # 显示详情
                results_per_page = 20
                index_on_page = (item_index - 1) % results_per_page
                correct_page = (item_index - 1) // results_per_page + 1

                if correct_page != page_to_list:
                    yield event.plain_result(f"⏳ 序号【{item_index}】位于第 {correct_page} 页，正在为您跳转...")
                    page_to_fetch = correct_page
                    search_result = await self.uaa_source.search(self.session, book_name, page_to_fetch)
                    if search_result is None or not search_result.books:
                        yield event.plain_result(f"😢 未在第 {correct_page} 页找到关于【{book_name}】的信息。")
                        return
                    # Update state to correct page
                    self.state_mgr.update_state(user_id, book_name, page_to_fetch, search_result.total_pages, "hs", search_result.books)

                if not (0 <= index_on_page < len(search_result.books)):
                    yield event.plain_result(f"❌ 序号【{item_index}】在第 {page_to_fetch} 页上不存在。")
                    return

                selected_book = search_result.books[index_on_page]
                book_details = await self.uaa_source.get_book_details(self.session, selected_book.id)
                if book_details:
                    async for result in self._render_hs_book_details(event, book_details):
                        yield result
                else:
                    yield event.plain_result(f"😢 无法获取书籍详情。")
        except Exception as e:
            logger.error(f"搜索hs书籍 '{book_name}' 失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 搜索hs书籍时发生未知错误: {str(e)}")

    @filter.command_group("ys")
    def ys_group(self):
        """优书搜索命令组"""
        pass

    @ys_group.command("next")
    async def ys_next_page(self, event: AstrMessageEvent):
        """下一页"""
        user_id = event.get_sender_id()
        state = self.state_mgr.get_state(user_id)

        if not state.get("keyword") or state.get("search_type") != "ys":
            yield event.plain_result("🤔 没有可供翻页的搜索结果，请先使用 /ys <书名> 进行搜索。")
            return

        current_page = state.get("current_page", 1)
        max_pages = state.get("max_pages", 1)

        if current_page >= max_pages:
            yield event.plain_result("➡️ 已经是最后一页了。")
            return

        next_page = current_page + 1
        keyword = state["keyword"]

        try:
            search_result = await self.youshu_source.search(self.session, keyword, next_page)
            if search_result is None or not search_result.books:
                yield event.plain_result(f"😢 无法加载第 {next_page} 页。")
                return

            # Update state
            self.state_mgr.update_state(user_id, keyword, next_page, search_result.total_pages, "ys", search_result.books)

            message_text = self._render_search_results(keyword, search_result, next_page)
            yield event.plain_result(message_text)
        except Exception as e:
            logger.error(f"翻页失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 翻页时发生错误: {str(e)}")

    @ys_group.command("prev")
    async def ys_prev_page(self, event: AstrMessageEvent):
        """上一页"""
        user_id = event.get_sender_id()
        state = self.state_mgr.get_state(user_id)

        if not state.get("keyword") or state.get("search_type") != "ys":
            yield event.plain_result("🤔 没有可供翻页的搜索结果，请先使用 /ys <书名> 进行搜索。")
            return

        current_page = state.get("current_page", 1)

        if current_page <= 1:
            yield event.plain_result("⬅️ 已经是第一页了。")
            return

        prev_page = current_page - 1
        keyword = state["keyword"]
        max_pages = state.get("max_pages", 1)

        try:
            search_result = await self.youshu_source.search(self.session, keyword, prev_page)
            if search_result is None or not search_result.books:
                yield event.plain_result(f"😢 无法加载第 {prev_page} 页。")
                return

            # Update state
            self.state_mgr.update_state(user_id, keyword, prev_page, search_result.total_pages, "ys", search_result.books)

            message_text = self._render_search_results(keyword, search_result, prev_page)
            yield event.plain_result(message_text)
        except Exception as e:
            logger.error(f"翻页失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 翻页时发生错误: {str(e)}")

    @ys_group.command("ls")
    async def ys_list_or_detail(self, event: AstrMessageEvent, index: str = ""):
        """查看指定序号的书籍详情"""
        user_id = event.get_sender_id()
        state = self.state_mgr.get_state(user_id)

        if not state.get("keyword") or state.get("search_type") != "ys":
            yield event.plain_result("🤔 没有可用的搜索结果，请先使用 /ys <书名> 进行搜索。")
            return

        if not index or not index.isdigit():
            yield event.plain_result("❌ 请提供有效的序号，例如：/ys ls 1")
            return

        item_index = int(index)
        results_per_page = 20
        current_page = state.get("current_page", 1)

        # 计算该序号应该在哪一页
        correct_page = (item_index - 1) // results_per_page + 1

        # 如果不在当前页，需要先加载对应页
        if correct_page != current_page:
            keyword = state["keyword"]
            try:
                yield event.plain_result(f"⏳ 序号【{item_index}】位于第 {correct_page} 页，正在为您跳转...")
                search_result = await self.youshu_source.search(self.session, keyword, correct_page)
                if search_result is None or not search_result.books:
                    yield event.plain_result(f"😢 无法加载第 {correct_page} 页。")
                    return
                # Update state
                self.state_mgr.update_state(user_id, keyword, correct_page, search_result.total_pages, "ys", search_result.books)
            except Exception as e:
                logger.error(f"加载页面失败: {e}", exc_info=True)
                yield event.plain_result(f"❌ 加载页面时发生错误: {str(e)}")
                return

        # 从当前页结果中获取对应的书籍
        index_on_page = (item_index - 1) % results_per_page
        results = state.get("results", [])

        if not (0 <= index_on_page < len(results)):
            yield event.plain_result(f"❌ 序号【{item_index}】不存在。")
            return

        selected_book = results[index_on_page]
        novel_id = selected_book.get('id')
        if not novel_id:
            yield event.plain_result(f"❌ 无法获取序号为【{item_index}】的书籍ID。")
            return

        try:
            book_details = await self.youshu_source.get_book_details(self.session, str(novel_id))
            if book_details:
                async for result in self._render_ys_book_details(event, book_details):
                    yield result
            else:
                yield event.plain_result(f"😢 无法获取书籍详情。")
        except Exception as e:
            logger.error(f"获取书籍详情失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 获取详情时发生错误: {str(e)}")

    @filter.command_group("hs")
    def hs_group(self):
        """皇叔搜索命令组"""
        pass

    @hs_group.command("next")
    async def hs_next_page(self, event: AstrMessageEvent):
        """下一页"""
        user_id = event.get_sender_id()
        state = self.state_mgr.get_state(user_id)

        if not state.get("keyword") or state.get("search_type") != "hs":
            yield event.plain_result("🤔 没有可供翻页的搜索结果，请先使用 /hs <书名> 进行搜索。")
            return

        current_page = state.get("current_page", 1)
        max_pages = state.get("max_pages", 1)

        if current_page >= max_pages:
            yield event.plain_result("➡️ 已经是最后一页了。")
            return

        next_page = current_page + 1
        keyword = state["keyword"]

        try:
            search_result = await self.uaa_source.search(self.session, keyword, next_page)
            if search_result is None or not search_result.books:
                yield event.plain_result(f"😢 无法加载第 {next_page} 页。")
                return

            # Update state
            self.state_mgr.update_state(user_id, keyword, next_page, search_result.total_pages, "hs", search_result.books)

            results_per_page = 20
            start_num = (next_page - 1) * results_per_page + 1
            message_text = f"以下是【{keyword}】的第 {next_page}/{search_result.total_pages} 页搜索结果:\n"
            for i, book in enumerate(search_result.books):
                num = start_num + i
                score_value = book.score
                if isinstance(score_value, (int, float)):
                    score = f"{score_value:.2f}"
                else:
                    score = 'N/A'
                message_text += f"{num}. {book.title}\n    作者：{book.author} | 评分: {score}\n"
            message_text += f"\n💡 请使用 `/hs ls <序号>` 查看详情"
            if search_result.total_pages > 1:
                message_text += f"\n💡 使用 /hs next 下一页，/hs prev 上一页"
            yield event.plain_result(message_text)
        except Exception as e:
            logger.error(f"翻页失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 翻页时发生错误: {str(e)}")

    @hs_group.command("prev")
    async def hs_prev_page(self, event: AstrMessageEvent):
        """上一页"""
        user_id = event.get_sender_id()
        state = self.state_mgr.get_state(user_id)

        if not state.get("keyword") or state.get("search_type") != "hs":
            yield event.plain_result("🤔 没有可供翻页的搜索结果，请先使用 /hs <书名> 进行搜索。")
            return

        current_page = state.get("current_page", 1)

        if current_page <= 1:
            yield event.plain_result("⬅️ 已经是第一页了。")
            return

        prev_page = current_page - 1
        keyword = state["keyword"]
        max_pages = state.get("max_pages", 1)

        try:
            search_result = await self.uaa_source.search(self.session, keyword, prev_page)
            if search_result is None or not search_result.books:
                yield event.plain_result(f"😢 无法加载第 {prev_page} 页。")
                return

            # Update state
            self.state_mgr.update_state(user_id, keyword, prev_page, search_result.total_pages, "hs", search_result.books)

            results_per_page = 20
            start_num = (prev_page - 1) * results_per_page + 1
            message_text = f"以下是【{keyword}】的第 {prev_page}/{search_result.total_pages} 页搜索结果:\n"
            for i, book in enumerate(search_result.books):
                num = start_num + i
                score_value = book.score
                if isinstance(score_value, (int, float)):
                    score = f"{score_value:.2f}"
                else:
                    score = 'N/A'
                message_text += f"{num}. {book.title}\n    作者：{book.author} | 评分: {score}\n"
            message_text += f"\n💡 请使用 `/hs ls <序号>` 查看详情"
            if search_result.total_pages > 1:
                message_text += f"\n💡 使用 /hs next 下一页，/hs prev 上一页"
            yield event.plain_result(message_text)
        except Exception as e:
            logger.error(f"翻页失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 翻页时发生错误: {str(e)}")

    @hs_group.command("ls")
    async def hs_list_or_detail(self, event: AstrMessageEvent, index: str = ""):
        """查看指定序号的书籍详情"""
        user_id = event.get_sender_id()
        state = self.state_mgr.get_state(user_id)

        if not state.get("keyword") or state.get("search_type") != "hs":
            yield event.plain_result("🤔 没有可用的搜索结果，请先使用 /hs <书名> 进行搜索。")
            return

        if not index or not index.isdigit():
            yield event.plain_result("❌ 请提供有效的序号，例如：/hs ls 1")
            return

        item_index = int(index)
        results_per_page = 20
        current_page = state.get("current_page", 1)

        # 计算该序号应该在哪一页
        correct_page = (item_index - 1) // results_per_page + 1

        # 如果不在当前页，需要先加载对应页
        if correct_page != current_page:
            keyword = state["keyword"]
            try:
                yield event.plain_result(f"⏳ 序号【{item_index}】位于第 {correct_page} 页，正在为您跳转...")
                search_result = await self.uaa_source.search(self.session, keyword, correct_page)
                if search_result is None or not search_result.books:
                    yield event.plain_result(f"😢 无法加载第 {correct_page} 页。")
                    return
                # Update state
                self.state_mgr.update_state(user_id, keyword, correct_page, search_result.total_pages, "hs", search_result.books)
            except Exception as e:
                logger.error(f"加载页面失败: {e}", exc_info=True)
                yield event.plain_result(f"❌ 加载页面时发生错误: {str(e)}")
                return

        # 从当前页结果中获取对应的书籍
        index_on_page = (item_index - 1) % results_per_page
        results = state.get("results", [])

        if not (0 <= index_on_page < len(results)):
            yield event.plain_result(f"❌ 序号【{item_index}】不存在。")
            return

        selected_book = results[index_on_page]
        novel_id = selected_book.get('id')
        if not novel_id:
            yield event.plain_result(f"❌ 无法获取序号为【{item_index}】的书籍ID。")
            return

        try:
            book_details = await self.uaa_source.get_book_details(self.session, str(novel_id))
            if book_details:
                async for result in self._render_hs_book_details(event, book_details):
                    yield result
            else:
                yield event.plain_result(f"😢 无法获取书籍详情。")
        except Exception as e:
            logger.error(f"获取书籍详情失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 获取详情时发生错误: {str(e)}")

    async def _get_latest_novel_id(self) -> Optional[int]:
        """获取最新小说ID"""
        # Use the appropriate source based on the current API being used
        # For now, we'll try to get the latest ID from the youshu source
        # This is a simplified implementation - in reality this would need to be
        # implemented in the source classes
        try:
            # Determine which URL to use based on the config
            config = self.youshu_source.config
            base_url = config.get("base_url", "https://www.ypshuo.com/")

            if base_url == "https://www.ypshuo.com/":
                url = "https://www.ypshuo.com/"
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Connection": "keep-alive",
                }
            else:
                url = "https://youshu.me/"
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:143.0) Gecko/20100101 Firefox/143.0",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2",
                    "Accept-Encoding": "gzip, deflate",
                    "Connection": "keep-alive",
                }

            async with self.session.get(url, headers=headers, timeout=10) as response:
                response.raise_for_status()
                html_content = await response.text()

                # Look for novel IDs in the HTML
                matches = re.findall(r'href="/novel/(\d+)\.html"|href="/book/(\d+)"', html_content)
                if matches:
                    # Flatten the matches (each match is a tuple of (id1, id2))
                    all_ids = []
                    for match in matches:
                        id1, id2 = match
                        if id1:
                            all_ids.append(int(id1))
                        elif id2:
                            all_ids.append(int(id2))
                    if all_ids:
                        latest_id = max(all_ids)
                        return latest_id
        except Exception as e:
            logger.warning(f"获取最新小说ID时出错: {e}")
            return None

    @filter.command("随机小说")
    async def youshu_random_command(self, event: AstrMessageEvent):
        max_retries = 10
        try:
            latest_id = await self._get_latest_novel_id()
            if not latest_id:
                yield event.plain_result("❌ 抱歉，未能获取到最新的小说ID，无法进行随机搜索。")
                return
        except Exception as e:
            logger.error(f"获取最新ID时发生错误: {e}", exc_info=True)
            yield event.plain_result("❌ 获取最新小说ID时出错，请稍后再试。")
            return
        
        for attempt in range(max_retries):
            random_id = random.randint(1, latest_id)
            logger.info(f"第 {attempt + 1}/{max_retries} 次尝试随机ID: {random_id}")
            try:
                book_details = await self.youshu_source.get_book_details(self.session, str(random_id))
                if book_details:
                    async for result in self._render_ys_book_details(event, book_details):
                        yield result
                    return
            except Exception as e:
                logger.warning(f"处理随机ID {random_id} 失败: {e}，正在重试...")
                continue
        
        yield event.plain_result("😢 抱歉，多次尝试后仍未找到有效的小说页面。请稍后再试。")

    async def terminate(self):
        """插件销毁时的清理工作"""
        if not self.session.closed:
            await self.session.close()
        logger.info("小说搜索插件已卸载")