import asyncio
import aiohttp
import re
import base64
from typing import Optional, List, Dict, Any
from urllib.parse import urljoin
from astrbot.api import logger

from .base_source import BaseSource
from ..main import Book, SearchResult

HS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}

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
                logger.info(f"✅ HS API 搜索 '{keyword}' (第 {page} 页) 成功，找到 {len(raw_results)} 条结果，共 {total_pages} 页。")

                books = []
                for raw_book in raw_results:
                    book = Book(
                        id=str(raw_book.get('id', '')),
                        title=raw_book.get('title', '未知书籍'),
                        author=raw_book.get('authors', '未知作者'),
                        score=str(raw_book.get('score', '暂无')) if raw_book.get('score') is not None else '暂无'
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

            # 提取标题
            title_match = re.search(r'<h1>(.*?)</h1>', html_content)
            novel_info['title'] = clean_text(title_match.group(1)) if title_match else '无'

            # 提取作者
            author_match = re.search(r'作者：\s*<a.*?>(.*?)</a>', html_content)
            novel_info['author'] = clean_text(author_match.group(1)) if author_match else '无'

            # 提取状态
            status_match = re.search(r'<span class="update_state">状态：(.*?)</span>', html_content)
            novel_info['status'] = clean_text(status_match.group(1)) if status_match else '无'

            # 提取评分
            score_match = re.search(r'评分：<span>(.*?)</span>', html_content)
            novel_info['score'] = clean_text(score_match.group(1)) if score_match else '无'

            # 提取简介
            intro_match = re.search(r'<div class="txt ellipsis">小说简介：(.*?)(?:</div>|<div class="arrow")', html_content, re.DOTALL)
            novel_info['intro'] = clean_text(intro_match.group(1)) if intro_match else '无'

            # 提取标签
            tags = re.findall(r'<li><a href="/novel/list\?tag=.*?"><b>#</b>(.*?)</a></li>', html_content)
            novel_info['tags'] = tags if tags else []

            # 提取题材
            category_block_match = re.search(r'<div class="item">\s*题材：\s*(.*?)</div>', html_content, re.DOTALL)
            if category_block_match:
                categories = re.findall(r'<a.*?>(.*?)</a>', category_block_match.group(1))
                novel_info['categories'] = [cat.strip() for cat in categories]
            else:
                novel_info['categories'] = []

            # 提取最新章节
            update_match = re.search(r'<div class="item">\s*最新：(.*?)\s*</div>', html_content)
            novel_info['latest_chapter'] = clean_text(update_match.group(1)) if update_match else None

            # 提取最后更新时间
            update_time_match = re.search(r'最后更新：\s*(.*?)\s*</div>', html_content)
            novel_info['update_time'] = clean_text(update_time_match.group(1)) if update_time_match else None

            # 从 props_box 中提取字数、热度和多肉度
            props_match = re.search(r'<div class="props_box"[^>]*?>\s*<ul>(.*?)</ul>', html_content, re.DOTALL)
            if props_match:
                props_html = props_match.group(1)
                # 肉度
                meat_match = re.search(r'<li>\s*<img src="/image/rou\.svg"/>(.*?)\s*</li>', props_html, re.DOTALL)
                novel_info['meat_ratio'] = clean_text(meat_match.group(1)) if meat_match else None
                
                # 字数
                word_match = re.search(r'<li>\s*<img src="/image/word_count\.svg"/>(.*?)\s*</li>', props_html, re.DOTALL)
                novel_info['word_count'] = clean_text(word_match.group(1)) if word_match else None
                
                # 收藏数 (热度)
                collect_match = re.search(r'<li>\s*<img src="/image/collect\.svg"/>(.*?)\s*</li>', props_html, re.DOTALL)
                novel_info['popularity'] = f"{clean_text(collect_match.group(1))}人收藏" if collect_match else None

            # 获取书评
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
                logger.warning(f"⚠️ 获取书评失败 (ID: {book_id})，可能需要登录或接口失效: {e}")

            # 创建并返回 Book 对象
            all_tags = novel_info['tags'][:]  # 复制标签列表

            book = Book(
                id=book_id,
                title=novel_info['title'] if novel_info['title'] != '无' else None,
                author=novel_info['author'] if novel_info['author'] != '无' else None,
                score=novel_info['score'] if novel_info['score'] != '无' else None,
                status=novel_info['status'] if novel_info['status'] != '无' else None,
                category=novel_info['categories'][0] if novel_info['categories'] else None,
                categories=novel_info['categories'],
                tags=all_tags,
                word_count=novel_info.get('word_count'),
                meat_ratio=novel_info.get('meat_ratio'),
                popularity=novel_info.get('popularity'),
                synopsis=novel_info['intro'] if novel_info['intro'] != '无' else None,
                update_time=novel_info['update_time'],
                last_chapter=novel_info['latest_chapter'],
                reviews=reviews
            )

            return book

        except Exception as e:
            logger.error(f"❌ 获取HS书籍详情失败: {e}", exc_info=True)
            return None

    def get_search_type(self) -> str:
        """获取搜索类型标识"""
        return "hs"