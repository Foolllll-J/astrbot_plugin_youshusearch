import asyncio
import aiohttp
import re
import base64
from typing import Optional, List, Dict, Any
from urllib.parse import urljoin
from astrbot.api import logger

from .base_source import BaseSource
from ..main import Book, SearchResult, HS_HEADERS

class UaaSource(BaseSource):
    """UAA 网站数据源 (hs)"""
    
    def __init__(self, config: dict):
        super().__init__(config)
        self.uaa_base_url = "https://www.uaa001.com"

    async def search(self, session: aiohttp.ClientSession, keyword: str, page: int = 1) -> Optional[SearchResult]:
        """搜索书籍"""
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
                logger.info(f"✅ HS API 搜索 '{keyword}' (Page {page}) 成功，找到 {len(raw_results)} 条结果，共 {total_pages} 页。")

                books = []
                for raw_book in raw_results:
                    book = Book(
                        id=str(raw_book.get('id', '')),
                        title=raw_book.get('title', '未知书籍'),
                        author=raw_book.get('authors', '未知作者'),
                        score=str(raw_book.get('score', 'N/A')) if raw_book.get('score') is not None else 'N/A'
                    )
                    books.append(book)

                return SearchResult(books=books, total_pages=total_pages, current_page=page)
            else:
                logger.warning(f"⚠️ HS API 搜索 '{keyword}' 返回失败或格式错误: {json_data.get('msg', '无信息')}")
                return None
        except Exception as e:
            logger.error(f"❌ 执行 HS API 搜索时发生错误: {e}", exc_info=True)
            return None

    async def get_book_details(self, session: aiohttp.ClientSession, book_id: str) -> Optional[Book]:
        """获取书籍详情"""
        novel_url = urljoin(self.uaa_base_url, f"/novel/intro?id={book_id}")

        try:
            async with session.get(novel_url, headers=HS_HEADERS, timeout=10) as response:
                response.raise_for_status()
                html_content = await response.text()

            novel_info = {}

            def clean_text(text):
                return text.strip() if text else '无'

            # Extract title
            title_match = re.search(r'<h1>(.*?)</h1>', html_content)
            novel_info['title'] = clean_text(title_match.group(1)) if title_match else '无'

            # Extract author
            author_match = re.search(r'作者：\s*<a.*?>(.*?)</a>', html_content)
            novel_info['author'] = clean_text(author_match.group(1)) if author_match else '无'

            # Extract status
            status_match = re.search(r'<span class="update_state">状态：(.*?)</span>', html_content)
            novel_info['status'] = clean_text(status_match.group(1)) if status_match else '无'

            # Extract score
            score_match = re.search(r'评分：<span>(.*?)</span>', html_content)
            novel_info['score'] = clean_text(score_match.group(1)) if score_match else '无'

            # Extract intro
            intro_match = re.search(r'<div class="txt ellipsis">小说简介：(.*?)(?:</div>|<div class="arrow")', html_content, re.DOTALL)
            novel_info['intro'] = clean_text(intro_match.group(1)) if intro_match else '无'

            # Extract tags
            tags = re.findall(r'<li><a href="/novel/list\?tag=.*?"><b>#</b>(.*?)</a></li>', html_content)
            novel_info['tags'] = tags if tags else []

            # Extract categories
            category_block_match = re.search(r'<div class="item">\s*题材：\s*(.*?)</div>', html_content, re.DOTALL)
            if category_block_match:
                categories = re.findall(r'<a.*?>(.*?)</a>', category_block_match.group(1))
                novel_info['categories'] = [cat.strip() for cat in categories]
            else:
                novel_info['categories'] = []

            # Extract latest update
            update_match = re.search(r'<div class="item">\s*最新：(.*?)\s*</div>', html_content)
            novel_info['latest_update'] = clean_text(update_match.group(1)) if update_match else '无'

            # Get reviews
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
                        logger.info(f"✅ 成功获取到 {len(reviews)} 条书评 for ID {book_id}")
            except Exception as e:
                logger.warning(f"⚠️ 获取书评失败 for ID {book_id} (可能需要登录或接口失效): {e}")

            # Create and return Book object
            all_tags = novel_info['tags'][:]  # Copy the tags list

            book = Book(
                id=book_id,
                title=novel_info['title'],
                author=novel_info['author'],
                score=novel_info['score'],
                status=novel_info['status'],
                category=novel_info['categories'][0] if novel_info['categories'] else "未知",  # Use first category as main category
                categories=novel_info['categories'],  # Store all categories separately
                tags=all_tags,  # Store tags separately
                synopsis=novel_info['intro'],
                update_time=novel_info['latest_update'],
                reviews=reviews
            )

            return book

        except Exception as e:
            logger.error(f"❌ 获取HS书籍详情失败: {e}", exc_info=True)
            return None

    def get_search_type(self) -> str:
        """获取搜索类型标识"""
        return "hs"