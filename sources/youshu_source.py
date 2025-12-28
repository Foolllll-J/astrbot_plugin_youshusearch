import asyncio
import aiohttp
import re
import base64
from typing import Optional, List, Dict, Any
from urllib.parse import urljoin, quote
from astrbot.api import logger

from .base_source import BaseSource
from ..main import Book, SearchResult, YS_PLATFORMS, YS_CATEGORIES, YS_STATUSES, YS_API1_HEADERS, YS_API2_HEADERS

class YoushuSearchStrategy:
    """优书网搜索策略接口"""
    
    async def search(self, session: aiohttp.ClientSession, keyword: str, page: int = 1) -> Optional[tuple[List[Dict], int]]:
        pass
    
    async def get_book_details(self, session: aiohttp.ClientSession, book_id: str) -> Optional[Dict[str, Any]]:
        pass

class YpshuoStrategy(YoushuSearchStrategy):
    """Ypshuo搜索策略 (API 1)"""
    
    def __init__(self, base_url: str, headers: dict):
        self.base_url = base_url
        self.headers = headers
        self.search_api_endpoint = "api/novel/search"

    async def search(self, session: aiohttp.ClientSession, keyword: str, page: int = 1) -> Optional[tuple[List[Dict], int]]:
        search_api_url = urljoin(self.base_url, self.search_api_endpoint)
        params = {"keyword": keyword, "page": str(page)}
        try:
            async with session.get(search_api_url, params=params, headers=self.headers, timeout=20) as response:
                response.raise_for_status()
                json_content = await response.json()
                logger.info(f"搜索 '{keyword}' (Page {page}) API调用成功。")
                if json_content.get("code") == "00" and "data" in json_content:
                    data = json_content["data"]
                    results = data.get("data", [])
                    total_pages = int(data.get("pageAll", 1))
                    return results, total_pages
                else:
                    return None
        except Exception as e:
            logger.error(f"❌ 执行API搜索时发生错误: {e}", exc_info=True)
            return None

    async def get_book_details(self, session: aiohttp.ClientSession, book_id: str) -> Optional[Dict[str, Any]]:
        novel_url = f"https://www.ypshuo.com/novel/{book_id}.html"
        try:
            async with session.get(novel_url, headers=self.headers, timeout=10) as response:
                response.raise_for_status()
                html_content = await response.text()
                
            def clean_html_content(text):
                if not text:
                    return '无'
                text = re.sub(r'<[^>]+>', '', text)
                text = re.sub(r'\s+', ' ', text).strip()
                text = re.sub(r'\.{3,}全文$', '...', text).strip()
                return text if text else '无'

            novel_info = {}
            # Extract image
            og_image_match = re.search(r'<meta[^>]*?name="og:image"[^>]*?content="(.*?)"', html_content)
            if og_image_match:
                image_url = og_image_match.group(1)
                if image_url.startswith('//'):
                    image_url = 'https:' + image_url
                elif image_url.startswith('/'):
                    image_url = urljoin(self.base_url, image_url)
                novel_info['image_url'] = image_url
            else:
                image_match = re.search(r'<img src="(.*?)"[^>]*?class="book-img"', html_content)
                if image_match:
                    image_url = image_match.group(1)
                    if image_url.startswith('/'):
                        image_url = urljoin(self.base_url, image_url)
                    novel_info['image_url'] = image_url
                else:
                    novel_info['image_url'] = None

            # Extract name
            name_match = re.search(r'<h1 class="book-name".*?>(.*?)</h1>', html_content, re.DOTALL)
            novel_info['novel_name'] = name_match.group(1).strip() if name_match else '无'

            # Extract author
            author_match = re.search(r'作者：<span class="text-red-500".*?>(.*?)</span>', html_content)
            novel_info['author_name'] = author_match.group(1).strip() if author_match else '无'

            # Extract tags
            novel_info['tags'] = []
            tag_block_match = re.search(r'<div class="tag-list"[^>]*?>(.*?)</div>', html_content, re.DOTALL)
            if tag_block_match:
                tag_html = tag_block_match.group(1)
                tags_list = re.findall(r'<span[^>]*?>(.*?)</span>', tag_html)
                if tags_list:
                    novel_info['tags'] = [tag.strip() for tag in tags_list if tag.strip()]

            # Extract word count
            word_count_match = re.search(r'字数：(.*?)万字', html_content)
            if word_count_match:
                try:
                    word_str = word_count_match.group(1).strip().replace(',', '')
                    novel_info['word_number'] = float(word_str) * 10000
                except (ValueError, TypeError):
                    novel_info['word_number'] = None
            else:
                novel_info['word_number'] = None

            # Extract score and scorer
            score_data_matches = re.findall(r'<div class="item"[^>]*?>\s*<p class="score"[^>]*?>\s*(.*?)\s*</p>\s*<p[^>]*?>(.*?)</p>\s*</div>', html_content, re.DOTALL)
            novel_info['score'] = '无'
            novel_info['scorer'] = '无'
            for value, label in score_data_matches:
                if label.strip() == '评分':
                    novel_info['score'] = value.strip()
                elif label.strip() == '评分人数':
                    novel_info['scorer'] = value.strip()

            # Extract status
            status_match = re.search(r'状态：\s*(.*?)\s*<', html_content)
            novel_info['status'] = status_match.group(1).strip() if status_match else '无'

            # Extract update time
            update_time_match = re.search(r'更新时间：\s*(.*?)\s*</div>', html_content)
            novel_info['update_time_str'] = update_time_match.group(1).strip() if update_time_match else '无'

            # Extract reviews
            reviews = []
            review_item_regex = re.compile(
                r'<div class="author-info"[^>]*?>(.*?)</div>'r'.*?'r'aria-valuenow="([^"]+)"'r'.*?'r'<span class="content-inner-details"[^>]*?>(.*?)</span>', re.DOTALL)
            all_reviews = review_item_regex.findall(html_content)
            for author, rating, content_block in all_reviews[:3]:
                content = re.sub(r'<[^>]+>', '', content_block)
                content = re.sub(r'[\r\n\t]+', '', content).strip()
                content = re.sub(r'\.{3,}全文$', '...', content).strip()
                if content:
                    reviews.append({'author': author.strip(), 'content': content, 'rating': rating})
            novel_info['reviews'] = reviews

            # Extract synopsis
            synopsis_match = re.search(r'<div style="white-space:pre-wrap;"[^>]*?>(.*?)</div>', html_content, re.DOTALL)
            synopsis_content = synopsis_match.group(1).strip() if synopsis_match else '无'
            novel_info['synopsis'] = synopsis_content

            # Extract link
            link_match = re.search(r'<a href="(http.*?)".*?rel="nofollow".*?>', html_content)
            novel_info['link'] = link_match.group(1).strip() if link_match else '无'

            return novel_info
        except Exception as e:
            logger.error(f"❌ DOM解析失败。错误: {e}")
            return {}

class YoushuMeStrategy(YoushuSearchStrategy):
    """Youshu.me搜索策略 (API 2)"""
    
    def __init__(self, base_url: str, headers: dict, cookie_string: str):
        self.base_url = base_url
        self.headers = headers
        self.cookie_string = cookie_string

    async def search(self, session: aiohttp.ClientSession, keyword: str, page: int = 1) -> Optional[tuple[List[Dict], int]]:
        try:
            results_per_page = 20
            encoded_keyword = quote(keyword)
            search_url = urljoin(self.base_url, f"/search/all/{encoded_keyword}/{page}.html")
            logger.info(f"正在访问搜索URL: {search_url}")

            async with session.get(search_url, headers=self.headers, timeout=20) as response:
                response.raise_for_status()
                body = await response.read()
                encoding = response.charset or 'utf-8'
                html_content = body.decode(encoding, errors='replace')

            def clean_html(raw_html):
                return re.sub(r'<[^>]+>', '', raw_html).strip()

            if '共有<b class="hot">' in html_content:
                logger.info("检测到搜索结果列表页，按列表解析。")
                total_results = 0
                total_match = re.search(r'共有<b class="hot">\s*(\d+)\s*</b>条结果', html_content)
                if total_match:
                    total_results = int(total_match.group(1))

                total_pages = (total_results + results_per_page - 1) // results_per_page if total_results > 0 else 1

                results = []
                result_blocks = re.findall(r'<div class="c_row">.*?<div class="cb"></div>', html_content, re.DOTALL)

                for block in result_blocks:
                    book_info = {}
                    name_match = re.search(r'<span class="c_subject"><a href="/book/(\d+)">(.*?)</a></span>', block, re.DOTALL)
                    if name_match:
                        book_info['id'] = int(name_match.group(1))
                        book_info['novel_name'] = clean_html(name_match.group(2))

                    author_match = re.search(r'<span class="c_label">作者：</span><span class="c_value">(.*?)</span>', block, re.DOTALL)
                    if author_match:
                        book_info['author_name'] = clean_html(author_match.group(1))

                    score_match = re.search(r'<span class="c_rr">([\d.]+)</span>', block)
                    if score_match:
                        book_info['score'] = score_match.group(1)

                    scorer_match = re.search(r'<span class="stard">\((\d+)人评分\)</span>', block)
                    if scorer_match:
                        book_info['scorer'] = scorer_match.group(1)

                    if 'id' in book_info and 'novel_name' in book_info:
                        results.append(book_info)

                logger.info(f"成功从列表页解析到 {len(results)} 条结果，共 {total_pages} 页。")
                return results, total_pages
            else:
                logger.info("未找到搜索列表，尝试按单本书籍详情页解析...")
                name_match = re.search(r'<title>(.*?)-.*?-优书网</title>', html_content)
                id_match = re.search(r"uservote\.php\?id=(\d+)|rating\('\d+',\s*'(\d+)'\)|addbookcase\.php\?bid=(\d+)", html_content)

                if name_match and id_match:
                    novel_id_str = next((gid for gid in id_match.groups() if gid is not None), None)
                    if novel_id_str:
                        novel_name = clean_html(name_match.group(1))
                        novel_id = int(novel_id_str)
                        logger.info(f"搜索结果为直接跳转，解析到书籍: '{novel_name}' (ID: {novel_id})")

                        results = [{'id': novel_id, 'novel_name': novel_name}]
                        total_pages = 1
                        return results, total_pages

                logger.warning("页面既不是搜索列表也不是有效的书籍详情页，判定为无结果。")
                return [], 0

        except Exception as e:
            logger.error(f"❌ 执行搜索时发生错误: {e}", exc_info=True)
            return None

    async def get_book_details(self, session: aiohttp.ClientSession, book_id: str) -> Optional[Dict[str, Any]]:
        novel_url = f"https://youshu.me/book/{book_id}"
        try:
            async with session.get(novel_url, headers=self.headers, timeout=10) as response:
                response.raise_for_status()
                html_content = await response.text()
                
            def clean_html_content(text):
                if not text:
                    return '无'
                text = re.sub(r'<[^>]+>', '', text)
                text = re.sub(r'\s+', ' ', text).strip()
                text = re.sub(r'\.{3,}全文$', '...', text).strip()
                return text if text else '无'

            novel_info = {}
            # Extract name
            name_match = re.search(r'<title>(.*?)-.*?-优书网</title>', html_content)
            novel_info['novel_name'] = clean_html_content(name_match.group(1)) if name_match else '无'

            # Extract author
            author_match = re.search(r'作者：<a.*?>(.*?)</a>', html_content)
            novel_info['author_name'] = clean_html_content(author_match.group(1)) if author_match else '无'

            # Extract score and scorer
            score_match = re.search(r'<span class="ratenum">(.*?)</span>', html_content)
            scorer_match = re.search(r'\((.*?)人已评\)', html_content)
            novel_info['score'] = clean_html_content(score_match.group(1)) if score_match else '无'
            novel_info['scorer'] = clean_html_content(scorer_match.group(1)) if scorer_match else '无'

            # Extract update time
            update_time_match = re.search(r'最后更新：(.*?)</td>', html_content)
            novel_info['update_time_str'] = clean_html_content(update_time_match.group(1)) if update_time_match else '无'

            # Extract synopsis
            synopsis_match = re.search(r'<div class="tabvalue"[^>]*?>\s*<div[^>]*?>(.*?)</div>', html_content, re.DOTALL)
            novel_info['synopsis'] = clean_html_content(synopsis_match.group(1)) if synopsis_match else '无'

            # Extract link
            link_match = re.search(r'<a class="btnlink b_hot mbs" href="(.*?)"', html_content)
            novel_info['link'] = clean_html_content(link_match.group(1)) if link_match else '无'

            # Extract image
            img_match = re.search(r'<a[^>]*?class="book-detail-img"[^>]*?><img src="(.*?)"', html_content)
            novel_info['image_url'] = urljoin(self.base_url, img_match.group(1).strip()) if img_match and img_match.group(1).strip() else None

            # Set default values
            novel_info.update({'platform': '无', 'category': '无', 'status': '无', 'word_number': None})

            # Extract platform, category, status, word count
            info_exp_match = re.search(r'<div class="author-item-exp">(.*?)</div>', html_content, re.DOTALL)
            if info_exp_match:
                raw_text = info_exp_match.group(1).replace('<i class="author-item-line"></i>', '|')
                clean_text = re.sub(r'<[^>]+>', '', raw_text)
                info_parts = [part.strip() for part in clean_text.split('|') if part.strip()]
                for part in info_parts:
                    if part in YS_PLATFORMS:
                        novel_info['platform'] = part
                    elif part in YS_CATEGORIES:
                        novel_info['category'] = part
                    elif part in YS_STATUSES:
                        novel_info['status'] = part
                    elif '字' in part:
                        word_match = re.search(r'(\d+)', part)
                        if word_match:
                            novel_info['word_number'] = float(word_match.group(1))

            # Extract tags
            novel_info['tags'] = []
            tag_section_match = re.search(r'<b>标签：</b>(.*?)</div>', html_content, re.DOTALL)
            if tag_section_match:
                tag_block = tag_section_match.group(1)
                tags = re.findall(r'<a[^>]*?>(.*?)</a>', tag_block)
                if tags:
                    novel_info['tags'] = [clean_html_content(tag) for tag in tags]

            # Extract reviews
            reviews = []
            review_blocks = re.findall(r'<div class="c_row cf[^"]*">.*?<div class="c_tag">', html_content, re.DOTALL)
            for block in review_blocks[:5]:
                author_match = re.search(r'<p>(.*?)</p></a>\s*<p><div class="user-level">', block, re.DOTALL)
                rating_match = re.search(r'<span title="(\d+)\s*颗星"', block, re.DOTALL)
                content_match = re.search(r'<div class="c_description">(.*?)</div>', block, re.DOTALL)
                if author_match and rating_match and content_match:
                    author = clean_html_content(author_match.group(1))
                    rating = rating_match.group(1)
                    content = clean_html_content(content_match.group(1))
                    if content and content != '无':
                        reviews.append({'author': author, 'content': content, 'rating': rating})
            novel_info['reviews'] = reviews

            return novel_info
        except Exception as e:
            logger.error(f"❌ DOM解析 (youshu.me) 失败。错误: {e}")
            return {}


class YoushuSource(BaseSource):
    """优书网数据源 (ys)"""
    
    def __init__(self, config: dict):
        super().__init__(config)
        self.base_api_url = config.get("base_url", "https://www.ypshuo.com/")
        self.cookie_string = config.get("cookie", "")
        
        # Determine which strategy to use based on URL
        if self.base_api_url == "https://www.ypshuo.com/":
            self.strategy = YpshuoStrategy(self.base_api_url, YS_API1_HEADERS)
        else:
            headers = YS_API2_HEADERS.copy()
            headers["Cookie"] = self.cookie_string
            headers["Referer"] = self.base_api_url
            self.strategy = YoushuMeStrategy(self.base_api_url, headers, self.cookie_string)

    async def search(self, session: aiohttp.ClientSession, keyword: str, page: int = 1) -> Optional[SearchResult]:
        """搜索书籍"""
        search_result = await self.strategy.search(session, keyword, page)
        if search_result is None or not search_result[0]:
            return None

        raw_books, total_pages = search_result
        books = []

        for raw_book in raw_books:
            book = Book(
                id=str(raw_book.get('id', '')),
                title=raw_book.get('novel_name', raw_book.get('title', '未知书籍')),
                author=raw_book.get('author_name', raw_book.get('authors', '未知作者')),
                score=raw_book.get('score', 'N/A'),
                scorer=raw_book.get('scorer', '0')
            )
            books.append(book)

        return SearchResult(books=books, total_pages=total_pages, current_page=page)

    async def get_book_details(self, session: aiohttp.ClientSession, book_id: str) -> Optional[Book]:
        """获取书籍详情"""
        details = await self.strategy.get_book_details(session, book_id)
        if not details or details.get('novel_name', '无') == '无':
            return None

        # Create Book object from details
        book = Book(
            id=book_id,
            title=details.get('novel_name', '无'),
            author=details.get('author_name', '无'),
            score=details.get('score', '无'),
            scorer=details.get('scorer', '无'),
            status=details.get('status', '未知'),
            platform=details.get('platform', '未知'),
            category=details.get('category', '未知'),
            tags=details.get('tags', []),
            word_count=details.get('word_number'),
            update_time=details.get('update_time_str', '未知'),
            synopsis=details.get('synopsis', '无'),
            link=details.get('link', ''),
            image_url=details.get('image_url'),
            reviews=details.get('reviews', [])
        )
        return book

    def get_search_type(self) -> str:
        """获取搜索类型标识"""
        return "ys"