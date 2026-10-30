"""Optional CI-only document transport using an ordinary, identified browser.

No stealth settings, user profiles, authentication, challenges, or image/script
downloads. The application UI never imports or launches this transport.
"""
from urllib.parse import urlparse
import requests


class BrowserSession:
    def __init__(self,policy):
        from playwright.sync_api import sync_playwright
        self.engine=sync_playwright().start()
        self.browser=self.engine.chromium.launch(headless=True)
        blank=self.browser.new_page()
        actual_agent=blank.evaluate('navigator.userAgent');blank.close()
        self.headers={'User-Agent':actual_agent+' DisneyCrowdAI/3.1 (+https://github.com/sorapanel7533/disney-crowd-ai)'}
        self.context=self.browser.new_context(user_agent=self.headers['User-Agent'],java_script_enabled=False,
            extra_http_headers={'Accept-Language':'ja'})
        self.policy=policy
        self.context.route('**/*',self.route)
        self.page=self.context.new_page()

    def route(self,route):
        req=route.request;u=urlparse(req.url)
        approved=(u.scheme=='https' and u.netloc=='www.tokyodisneyresort.jp' and req.resource_type=='document'
                  and not u.path.startswith(('/app/','/iframe/')))
        if approved and u.path!='/robots.txt':
            policy=self.policy();approved=policy is not None and policy.can_fetch('DisneyCrowdAI',req.url)
        route.continue_() if approved else route.abort()

    def get(self,url,timeout=(5,15),allow_redirects=False):
        from playwright.sync_api import TimeoutError as BrowserTimeout,Error as BrowserError
        try:
            response=self.page.goto(url,wait_until='domcontentloaded',timeout=int(sum(timeout)*1000))
            if response is None:raise requests.ConnectionError('Missing document response')
            body=response.body()
            if len(body)>2*1024*1024:raise requests.RequestException('Document limit exceeded')
            result=requests.Response();result.status_code=response.status;result._content=body
            result.headers.update(response.headers);result.url=response.url;result.encoding='utf-8'
            return result
        except BrowserTimeout:raise requests.ReadTimeout('Official browser document timeout') from None
        except BrowserError:raise requests.ConnectionError('Official browser document failure') from None

    def close(self):
        self.context.close();self.browser.close();self.engine.stop()
