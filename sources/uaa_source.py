import aiohttp
from typing import Optional, List, Dict, Any
from urllib.parse import urljoin
from cachetools import TTLCache
from astrbot.api import logger

from .base_source import BaseSource
from ..main import Book, SearchResult

HS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}

class UaaSource(BaseSource):
    """UAA 网站数据源 (hs)
    
    搜索结果 JSON 已包含绝大部分详情字段，不再需要请求详情页 HTML。
    get_book_details() 仅从搜索缓存中获取数据 + 可选书评。
    """
    
    def __init__(self, config: dict):
        super().__init__(config)
        self.uaa_base_url = "https://www.uaa001.com"
        self._book_cache: TTLCache = TTLCache(maxsize=500, ttl=1800)

    def _parse_book_from_search(self, raw: dict) -> Book:
        """从搜索 API 返回的一条数据中解析完整的 Book 对象"""
        book_id = str(raw.get('id', ''))
        book = Book(
            id=book_id,
            title=raw.get('title') or '',
            author=raw.get('authors') or ''
        )
        book.score = None
        score = raw.get('score')
        if score is not None:
            try:
                book.score = f"{float(score):.2f}"
            except (ValueError, TypeError):
                book.score = str(score)

        finished = raw.get('finished', 0)
        book.status = '已完结' if finished == 1 else '连载中'

        categories_str = raw.get('categories', '')
        if categories_str:
            book.categories = [c.strip() for c in categories_str.split(',') if c.strip()]
            if book.categories:
                book.category = book.categories[0]

        tags_str = raw.get('tags', '')
        if tags_str:
            book.tags = [t.strip() for t in tags_str.split(',') if t.strip()]

        wc = raw.get('wordCount')
        if wc is not None:
            book.word_count = wc

        book.update_time = raw.get('updateTimeFormat')
        book.last_chapter = raw.get('latestUpdate')
        book.meat_ratio = raw.get('pornRateDesc')

        view_count = raw.get('viewCountFormat')
        collect_count = raw.get('collectCountFormat')
        pop_parts = []
        if view_count:
            pop_parts.append(f"热度:{view_count}")
        if collect_count:
            pop_parts.append(f"收藏:{collect_count}")
        if pop_parts:
            book.popularity = " | ".join(pop_parts)

        brief = raw.get('brief')
        if brief:
            book.synopsis = brief

        if book_id:
            book.link = f"{self.uaa_base_url}/novel/intro?id={book_id}"

        return book

    async def search(self, session: aiohttp.ClientSession, keyword: str, page: int = 1) -> Optional[SearchResult]:
        """搜索书籍 — 解析全部可用字段并缓存"""
        search_api_url = urljoin(self.uaa_base_url, "/api/novel/app/novel/search")
        params = {
            "keyword": keyword,
            "page": page,
            "searchType": 1,
            "size": 20,
            "orderType": 0
        }

        try:
            async with session.get(search_api_url, params=params, headers=HS_HEADERS, timeout=20) as response:
                response.raise_for_status()
                json_data = await response.json()

            if json_data.get("result") == "success" and "model" in json_data:
                model = json_data["model"]
                raw_results = model.get("data", [])
                total_pages = model.get("totalPage", 1)
                logger.info(f"✅ HS API 搜索 '{keyword}' (第 {page} 页) 成功，找到 {len(raw_results)} 条结果，共 {total_pages} 页。")

                books = []
                for raw_book in raw_results:
                    book = self._parse_book_from_search(raw_book)
                    self._book_cache[book.id] = book
                    books.append(book)

                return SearchResult(books=books, total_pages=total_pages, current_page=page)
            else:
                logger.warning(f"⚠️ HS API 搜索 '{keyword}' 返回失败或格式错误: {json_data.get('msg', '无信息')}")
                return None
        except Exception as e:
            logger.error(f"❌ 执行 HS API 搜索时发生错误: {e}", exc_info=True)
            return None

    async def _fetch_reviews(self, session: aiohttp.ClientSession, book_id: str) -> List[Dict[str, Any]]:
        """调用评论 API 获取书评"""
        reviews = []
        try:
            comments_url = urljoin(self.uaa_base_url, "/api/novel/app/novel/comments")
            params = {"novelId": book_id, "sortType": 1, "page": 1, "rows": 5}
            async with session.get(comments_url, params=params, headers=HS_HEADERS, timeout=10) as response:
                response.raise_for_status()
                comments_data = await response.json()

                if comments_data.get("result") == "success" and "data" in comments_data:
                    for item in comments_data["data"]:
                        score_data = item.get('score')
                        score_val = '无'
                        if isinstance(score_data, dict):
                            score_val = score_data.get('source', '无')
                        elif isinstance(score_data, (int, float)):
                            score_val = f"{score_data:.1f}"

                        reviews.append({
                            'author': item.get('nickName', '匿名'),
                            'content': item.get('content', ''),
                            'score': score_val,
                            'time': item.get('createTimeFormat', '')
                        })
                    logger.info(f"✅ 成功获取到 {len(reviews)} 条书评 (ID: {book_id})")
        except Exception as e:
            logger.warning(f"⚠️ 获取书评失败 (ID: {book_id}): {e}")
        return reviews

    async def get_book_details(self, session: aiohttp.ClientSession, book_id: str) -> Optional[Book]:
        """获取书籍详情 — 仅从搜索缓存获取 + 评论 API（不做 HTML 详情页解析）"""
        book = self._book_cache.get(book_id)
        if not book:
            logger.warning(f"⚠️ 缓存中未找到书籍 (ID: {book_id})，跳过详情")
            return None

        # 追加书评
        reviews = await self._fetch_reviews(session, book_id)
        book.reviews = reviews
        return book

    def get_search_type(self) -> str:
        """获取搜索类型标识"""
        return "hs"
