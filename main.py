import asyncio
import aiohttp
import random
import re
import base64
from dataclasses import dataclass
from typing import Dict, List, Optional, Any

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
    score: Optional[str] = None
    scorer: Optional[str] = None
    status: Optional[str] = None
    platform: Optional[str] = None
    category: Optional[str] = None
    tags: List[str] = None
    categories: List[str] = None
    word_count: Optional[Any] = None
    update_time: Optional[str] = None
    last_chapter: Optional[str] = None
    meat_ratio: Optional[str] = None
    popularity: Optional[str] = None
    synopsis: Optional[str] = None
    link: Optional[str] = None
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

# 常量定义
YS_PLATFORMS = {"他站", "本站", "起点", "晋江", "番茄", "刺猬猫", "纵横", "飞卢", "17K", "有毒", "息壤", "铁血", "逐浪", "掌阅", "塔读", "独阅读", "少年梦", "SF", "豆瓣", "知乎", "公众号"}
YS_CATEGORIES = {"玄幻", "奇幻", "武侠", "仙侠", "都市", "现实", "军事", "历史", "悬疑", "游戏", "竞技", "科幻", "灵异", "二次元", "同人", "其他", "穿越时空", "架空历史", "总裁豪门", "都市言情", "仙侠奇缘", "幻想言情", "悬疑推理", "耽美纯爱", "衍生同人", "轻小说", "综合其他"}
YS_STATUSES = {"连载中", "已完结", "已太监"}

# 状态管理器
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
                "search_type": "",  # "ys" 或 "hs"
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
            # 将 Book 对象转换为字典存储
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

from .sources.youshu_source import YoushuSource, YS_API1_HEADERS, YS_API2_HEADERS
from .sources.uaa_source import UaaSource
from .sources.qidian_source import QidianSource

@register(
    "astrbot_plugin_youshusearch",  # 插件ID
    "Foolllll",                    # 作者名
    "优书搜索助手",                  # 插件显示名称
    "1.5",                         # 版本号
    "https://github.com/Foolllll-J/astrbot_plugin_youshusearch", # 插件仓库地址
)
class YoushuSearchPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        if config is None:
            config = {}
        
        # 初始化数据源
        self.youshu_source = YoushuSource(config)
        self.uaa_source = UaaSource(config)
        self.qidian_source = QidianSource()
        
        # 插件配置
        self.enable_official_metadata = config.get("enable_official_metadata", False)
        
        # 初始化状态管理器
        self.state_mgr = SearchStateManager()
        
        # 初始化全局会话
        self.session = aiohttp.ClientSession()

    def _get_item_by_number(self, user_id: str, number: int, search_type: str) -> Optional[Dict]:
        """根据序号和搜索类型获取书籍信息"""
        return self.state_mgr.get_item_by_number(user_id, number, search_type)

    def _render_search_results(self, keyword: str, results: SearchResult, page_to_list: int, search_type: str = "ys") -> str:
        """统一渲染搜索结果列表"""
        results_per_page = 20
        start_num = (page_to_list - 1) * results_per_page + 1
        message_text = f"以下是【{keyword}】的第 {page_to_list}/{results.total_pages} 页搜索结果:\n"
        
        for i, book in enumerate(results.books):
            num = start_num + i
            # 兼容评分显示，统一截取两位小数
            score = book.score
            score_str = '暂无'
            if score and score != '暂无':
                try:
                    score_str = f"{float(score):.2f}"
                except (ValueError, TypeError):
                    score_str = str(score)
            
            scorer_info = f" ({book.scorer}人)" if book.scorer and book.scorer != '0' else ""
            message_text += f"{num}. {book.title}\n    作者：{book.author} | 评分: {score_str}{scorer_info}\n"
        
        cmd_prefix = f"/{search_type}"
        message_text += f"\n💡 请使用 `{cmd_prefix} <序号>` 查看详情"
        if results.total_pages > 1:
            message_text += f"\n💡 使用 {cmd_prefix} next 下一页，{cmd_prefix} prev 上一页"
        return message_text

    async def _get_enriched_book_details(self, source, session, novel_id: str, title: Optional[str] = None) -> Optional[Book]:
        """获取书籍详情，并根据配置进行正版元数据补全"""
        book = await source.get_book_details(session, novel_id)
        if not book:
            return None
            
        search_title = title or book.title
        if not search_title:
            return book
            
        # 仅对优书网源且开启配置时进行补全
        if self.enable_official_metadata and source == self.youshu_source:
            try:
                # 1. 搜索起点
                qidian_results = await self.qidian_source.search_book(search_title)
                if not qidian_results:
                    return book
                    
                # 2. 检查前两个结果是否有完全匹配的书名
                match_book = None
                for qb in qidian_results[:2]:
                    if qb.get('name') == search_title:
                        match_book = qb
                        break
                
                if match_book:
                    # 3. 获取起点详情
                    q_details = await self.qidian_source.get_book_details(match_book['url'])
                    if q_details:
                        # 4. 覆盖元数据（排除评分和评分人数）
                        if q_details.get('author'): book.author = q_details['author']
                        if q_details.get('status'): book.status = q_details['status']
                        if q_details.get('category'): book.category = q_details['category']
                        if q_details.get('tags'): book.tags = q_details['tags']
                        if q_details.get('word_count'): book.word_count = q_details['word_count']
                        if q_details.get('last_update'): book.update_time = q_details['last_update']
                        if q_details.get('last_chapter'): book.last_chapter = q_details['last_chapter']
                        if q_details.get('intro'): book.synopsis = q_details['intro']
                        if q_details.get('cover'): book.image_url = q_details['cover']
                        
                        # 5. 组合热度信息 (排行、收藏、推荐)
                        pop_parts = []
                        if q_details.get('rank') and q_details['rank'] != '未上榜':
                            pop_parts.append(f"排名:{q_details['rank']}")
                        if q_details.get('collection'):
                            pop_parts.append(f"收藏:{q_details['collection']}")
                        if q_details.get('all_recommend'):
                            pop_parts.append(f"推荐:{q_details['all_recommend']}")
                        
                        if pop_parts:
                            book.popularity = " | ".join(map(str, pop_parts))
                            
            except Exception as e:
                logger.error(f"正版元数据补全失败: {e}")
                
        return book

    def _clean_synopsis(self, text):
        """清理简介文本格式 (参考 webnovel_info)"""
        if not text:
            return ""
        # 移除HTML标签
        text = re.sub(r'</?p>|<br\s*/?>', '\n', text)
        text = re.sub(r'<[^>]+>', '', text)
        # 替换HTML特殊字符
        text = text.replace("&nbsp;", " ").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
        # 清理空行并格式化缩进
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        return "　　" + "\n　　".join(lines)

    async def _render_book_details(self, event: AstrMessageEvent, book: Book):
        """统一渲染书籍详情并返回事件结果"""
        has_detail_content = any(
            [
                book.title,
                book.author,
                book.score,
                book.status,
                book.platform,
                book.category,
                book.categories,
                book.tags,
                book.word_count is not None,
                book.update_time,
                book.last_chapter,
                book.meat_ratio,
                book.popularity,
                book.synopsis,
                book.link,
            ]
        )

        if book.title:
            message_text = f"---【{book.title}】---\n"
        elif has_detail_content:
            message_text = "---【书籍详情】---\n"
        else:
            message_text = ""
        
        # 核心信息：作者
        if book.author:
            message_text += f"👤 作者: {book.author}\n"

        # 评分数据
        if book.score:
            try:
                formatted_score = f"{float(book.score):.2f}"
            except (ValueError, TypeError):
                formatted_score = book.score
            scorer_info = f" ({book.scorer}人评分)" if book.scorer else ""
            message_text += f"⭐ 评分: {formatted_score}{scorer_info}\n"

        # 平台、分类/题材
        if book.platform:
            message_text += f"🌐 平台: {book.platform}\n"
            
        # HS 网站显示“题材”，其他显示“分类”
        if book.categories: # HS 特有
            message_text += f"🏷️ 题材: {' '.join(book.categories)}\n"
        elif book.category:
            message_text += f"📂 分类: {book.category}\n"

        # 标签
        if book.tags:
            message_text += f"🔖 标签: {' '.join(book.tags)}\n"

        # 字数
        if book.word_count is not None:
            if isinstance(book.word_count, str) and ('K' in book.word_count or 'M' in book.word_count):
                 message_text += f"📏 字数: {book.word_count}\n"
            else:
                 try:
                     message_text += f"📏 字数: {float(book.word_count) / 10000:.2f}万字\n"
                 except:
                     message_text += f"📏 字数: {book.word_count}\n"

        # 状态
        if book.status:
            message_text += f"🔄 状态: {book.status}\n"

        # 肉度 (uaa)
        if book.meat_ratio:
            message_text += f"🥩 肉度: {book.meat_ratio}\n"

        # 热度 / 收藏
        if book.popularity:
            message_text += f"🔥 热度: {book.popularity}\n"

        # 更新信息
        if book.update_time:
            message_text += f"🕒 最后更新: {book.update_time}\n"
        
        # 最新章节
        if book.last_chapter:
            message_text += f"🆕 最新章节: {book.last_chapter}\n"

        # 简介
        if book.synopsis:
            cleaned_synopsis = self._clean_synopsis(book.synopsis)
            message_text += f"📝 简介: \n{cleaned_synopsis}\n"

        # 链接
        if book.link:
            message_text += f"🔗 链接: {book.link}\n"

        # 书评内容
        if book.reviews:
            if message_text:
                message_text += "\n"
            message_text += "--- 📝 最新书评 ---\n"
            for review in book.reviews[:5]: # 最多显示5条
                author = review.get('author', '匿名')
                # 兼容不同的评分键名 (score or rating)，并统一格式化
                rating = review.get('score') or review.get('rating')
                try:
                    rating_str = f"{float(rating):.2f}" if rating and rating != '无' else "无"
                except (ValueError, TypeError):
                    rating_str = str(rating) if rating else "无"
                
                content = review.get('content', '无')
                time_str = review.get('time') or review.get('createTimeFormat')
                
                review_line = f"{author} ({rating_str}分"
                if time_str:
                    review_line += f", {time_str}"
                review_line += f"): {content}\n"
                message_text += review_line

        if not message_text.strip():
            message_text = "😢 无法获取书籍详情。"

        chain = []
        # 图片抓取 (HS 详情页未抓取封面)
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

        chain.append(Comp.Plain(message_text.strip()))
        yield event.chain_result(chain)

    async def _handle_next_page(self, event: AstrMessageEvent, search_type: str):
        """处理下一页逻辑"""
        user_id = event.get_sender_id()
        state = self.state_mgr.get_state(user_id)
        source = self.youshu_source if search_type == "ys" else self.uaa_source

        if not state.get("keyword") or state.get("search_type") != search_type:
            yield event.plain_result(f"🤔 没有可供翻页的搜索结果，请先使用 /{search_type} <书名> 进行搜索。")
            return

        current_page = state.get("current_page", 1)
        max_pages = state.get("max_pages", 1)

        if current_page >= max_pages:
            yield event.plain_result("➡️ 已经是最后一页了。")
            return

        next_page = current_page + 1
        keyword = state["keyword"]

        try:
            search_result = await source.search(self.session, keyword, next_page)
            if search_result is None or not search_result.books:
                yield event.plain_result(f"😢 无法加载第 {next_page} 页。")
                return

            self.state_mgr.update_state(user_id, keyword, next_page, search_result.total_pages, search_type, search_result.books)
            message_text = self._render_search_results(keyword, search_result, next_page, search_type)
            yield event.plain_result(message_text)
        except Exception as e:
            logger.error(f"翻页失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 翻页时发生错误: {str(e)}")

    async def _handle_prev_page(self, event: AstrMessageEvent, search_type: str):
        """处理上一页逻辑"""
        user_id = event.get_sender_id()
        state = self.state_mgr.get_state(user_id)
        source = self.youshu_source if search_type == "ys" else self.uaa_source

        if not state.get("keyword") or state.get("search_type") != search_type:
            yield event.plain_result(f"🤔 没有可供翻页的搜索结果，请先使用 /{search_type} <书名> 进行搜索。")
            return

        current_page = state.get("current_page", 1)

        if current_page <= 1:
            yield event.plain_result("⬅️ 已经是第一页了。")
            return

        prev_page = current_page - 1
        keyword = state["keyword"]

        try:
            search_result = await source.search(self.session, keyword, prev_page)
            if search_result is None or not search_result.books:
                yield event.plain_result(f"😢 无法加载第 {prev_page} 页。")
                return

            self.state_mgr.update_state(user_id, keyword, prev_page, search_result.total_pages, search_type, search_result.books)
            message_text = self._render_search_results(keyword, search_result, prev_page, search_type)
            yield event.plain_result(message_text)
        except Exception as e:
            logger.error(f"翻页失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 翻页时发生错误: {str(e)}")

    @filter.command("ys", alias={"优书"})
    async def youshu_search_command(self, event: AstrMessageEvent):
        """
        优书网搜索命令
        用法: /ys <书名> [序号 | -页码]
        别名: /优书
        """
        # 获取所有参数部分（排除开头的 /ys 或 /优书）
        message_str = event.message_str.strip()
        parts = message_str.split()
        if len(parts) < 2:
            yield event.plain_result("❌ 用法: /ys <书名> [序号 | -页码]\n💡 示例: /ys 剑来 1 (查看第一项)、/ys 剑来 -2 (查看第二页)")
            return
        
        # 获取指令名之后的实际参数列表
        args = parts[1:]

        # 整合子命令逻辑
        sub_cmd = args[0].lower()
        if sub_cmd == "next" or sub_cmd == "下一页":
            async for res in self._handle_next_page(event, "ys"): yield res
            return
        elif sub_cmd == "prev" or sub_cmd == "上一页":
            async for res in self._handle_prev_page(event, "ys"): yield res
            return

        user_id = event.get_sender_id()
        book_name, page_to_list, item_index = "", 1, None

        # 检查是否是 /ys <序号> 这种简写形式
        if len(args) == 1 and args[0].isdigit():
            # 获取用户最后一次搜索的状态
            state = self.state_mgr.get_state(user_id)
            if state.get("keyword") and state.get("search_type") == "ys":
                book_name = state["keyword"]
                item_index = int(args[0])
                page_to_list = state.get("current_page", 1)
            else:
                yield event.plain_result("🤔 请先使用 /ys <书名> 进行搜索。")
                return
        else:
            # 原有的参数解析逻辑
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
            # 搜索书籍
            search_result = await self.youshu_source.search(self.session, book_name, page_to_list)
            if search_result is None or not search_result.books:
                yield event.plain_result(f"😢 未找到关于【{book_name}】的任何书籍信息。")
                return

            if page_to_list > search_result.total_pages and search_result.total_pages > 0:
                yield event.plain_result(f"❌ 您请求的第 {page_to_list} 页不存在，【{book_name}】的搜索结果最多只有 {search_result.total_pages} 页。")
                return

            # 更新用户搜索状态
            self.state_mgr.update_state(user_id, book_name, page_to_list, search_result.total_pages, "ys", search_result.books)

            if item_index is None and len(search_result.books) == 1 and search_result.total_pages == 1:
                # 如果只有一页且只有一个结果，直接显示详情
                selected_book = search_result.books[0]
                book_details = await self._get_enriched_book_details(self.youshu_source, self.session, selected_book.id, selected_book.title)
                if book_details:
                    async for result in self._render_book_details(event, book_details):
                        yield result
                else:
                    yield event.plain_result(f"😢 无法获取书籍详情。")
                return
            
            if item_index is None:
                # 显示搜索结果列表
                message_text = self._render_search_results(book_name, search_result, page_to_list, "ys")
                yield event.plain_result(message_text)
            else:
                # 显示特定书籍的详情
                results_per_page = 20
                index_on_page = (item_index - 1) % results_per_page
                correct_page = (item_index - 1) // results_per_page + 1

                if correct_page != page_to_list:
                    yield event.plain_result(f"⏳ 序号【{item_index}】位于第 {correct_page} 页，正在为您跳转...")
                    search_result = await self.youshu_source.search(self.session, book_name, correct_page)
                    if search_result is None or not search_result.books:
                        yield event.plain_result(f"😢 未在第 {correct_page} 页找到关于【{book_name}】的信息。")
                        return
                    # 更新状态至正确页面
                    self.state_mgr.update_state(user_id, book_name, correct_page, search_result.total_pages, "ys", search_result.books)

                if not (0 <= index_on_page < len(search_result.books)):
                    yield event.plain_result(f"❌ 序号【{item_index}】在第 {correct_page} 页上不存在。")
                    return

                selected_book = search_result.books[index_on_page]
                book_details = await self._get_enriched_book_details(self.youshu_source, self.session, selected_book.id, selected_book.title)
                if book_details:
                    async for result in self._render_book_details(event, book_details):
                        yield result
                else:
                    yield event.plain_result(f"😢 无法获取书籍详情。")
        except Exception as e:
            logger.error(f"搜索书籍 '{book_name}' 失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 搜索书籍时发生未知错误: {str(e)}")

    @filter.command("hs", alias={"皇叔", "黄书"})
    async def hs_search_command(self, event: AstrMessageEvent):
        """
        皇叔搜索命令
        用法: /hs <书名> [序号 | -页码]
        别名: /皇叔, /黄书
        """
        # 获取所有参数部分（排除开头的 /hs 或 /皇叔等）
        message_str = event.message_str.strip()
        parts = message_str.split()
        if len(parts) < 2:
            yield event.plain_result("❌ 用法: /hs <书名> [序号 | -页码]\n💡 示例: /hs 剑来 1 (查看第一项)、/hs 剑来 -2 (查看第二页)")
            return
        
        # 获取指令名之后的实际参数列表
        args = parts[1:]

        # 整合子命令逻辑
        sub_cmd = args[0].lower()
        if sub_cmd == "next" or sub_cmd == "下一页":
            async for res in self._handle_next_page(event, "hs"): yield res
            return
        elif sub_cmd == "prev" or sub_cmd == "上一页":
            async for res in self._handle_prev_page(event, "hs"): yield res
            return

        user_id = event.get_sender_id()
        book_name, page_to_list, item_index = "", 1, None

        # 检查是否是 /hs <序号> 这种简写形式
        if len(args) == 1 and args[0].isdigit():
            # 获取用户最后一次搜索的状态
            state = self.state_mgr.get_state(user_id)
            if state.get("keyword") and state.get("search_type") == "hs":
                book_name = state["keyword"]
                item_index = int(args[0])
                page_to_list = state.get("current_page", 1)
            else:
                yield event.plain_result("🤔 请先使用 /hs <书名> 进行搜索。")
                return
        else:
            # 原有的参数解析逻辑
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
            # 搜索书籍
            search_result = await self.uaa_source.search(self.session, book_name, page_to_list)
            if search_result is None or not search_result.books:
                yield event.plain_result(f"😢 未找到关于【{book_name}】的任何书籍信息。")
                return

            if page_to_list > search_result.total_pages and search_result.total_pages > 0:
                yield event.plain_result(f"❌ 您请求的第 {page_to_list} 页不存在，【{book_name}】的搜索结果最多只有 {search_result.total_pages} 页。")
                return

            # 更新用户搜索状态
            self.state_mgr.update_state(user_id, book_name, page_to_list, search_result.total_pages, "hs", search_result.books)

            if item_index is None and len(search_result.books) == 1 and search_result.total_pages == 1:
                # 如果只有一页且只有一个结果，直接显示详情
                selected_book = search_result.books[0]
                book_details = await self._get_enriched_book_details(self.uaa_source, self.session, selected_book.id, selected_book.title)
                if book_details:
                    async for result in self._render_book_details(event, book_details):
                        yield result
                else:
                    yield event.plain_result(f"😢 无法获取书籍详情。")
                return

            if item_index is None: # 显示列表
                message_text = self._render_search_results(book_name, search_result, page_to_list, "hs")
                yield event.plain_result(message_text)
            else: # 显示详情
                results_per_page = 20
                index_on_page = (item_index - 1) % results_per_page
                correct_page = (item_index - 1) // results_per_page + 1

                if correct_page != page_to_list:
                    yield event.plain_result(f"⏳ 序号【{item_index}】位于第 {correct_page} 页，正在为您跳转...")
                    search_result = await self.uaa_source.search(self.session, book_name, correct_page)
                    if search_result is None or not search_result.books:
                        yield event.plain_result(f"😢 未在第 {correct_page} 页找到关于【{book_name}】的信息。")
                        return
                    # 更新状态至正确页面
                    self.state_mgr.update_state(user_id, book_name, correct_page, search_result.total_pages, "hs", search_result.books)

                if not (0 <= index_on_page < len(search_result.books)):
                    yield event.plain_result(f"❌ 序号【{item_index}】在第 {correct_page} 页上不存在。")
                    return

                selected_book = search_result.books[index_on_page]
                book_details = await self._get_enriched_book_details(self.uaa_source, self.session, selected_book.id, selected_book.title)
                if book_details:
                    async for result in self._render_book_details(event, book_details):
                        yield result
                else:
                    yield event.plain_result(f"😢 无法获取书籍详情。")
        except Exception as e:
            logger.error(f"搜索hs书籍 '{book_name}' 失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 搜索hs书籍时发生未知错误: {str(e)}")

    async def _get_latest_novel_id(self) -> Optional[int]:
        """获取最新小说ID"""
        try:
            # 根据配置决定使用的 URL
            config = self.youshu_source.config
            base_url = config.get("base_url", "https://www.ypshuo.com/")

            if base_url == "https://www.ypshuo.com/":
                url = "https://www.ypshuo.com/"
                headers = YS_API1_HEADERS
            else:
                url = "https://youshu.me/"
                headers = YS_API2_HEADERS

            async with self.session.get(url, headers=headers, timeout=10) as response:
                response.raise_for_status()
                html_content = await response.text()

                # 在 HTML 中查找小说 ID
                matches = re.findall(r'href="/novel/(\d+)\.html"|href="/book/(\d+)"', html_content)
                if matches:
                    # 展开匹配结果 (每个匹配项是 (id1, id2) 的元组)
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
        """
        随机小说推荐
        用法: /随机小说
        """
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
                book_details = await self._get_enriched_book_details(self.youshu_source, self.session, str(random_id))
                if book_details:
                    async for result in self._render_book_details(event, book_details):
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
        logger.info("优书搜索插件已卸载")
