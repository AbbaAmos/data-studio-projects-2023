from pathlib import Path
import logging
from urllib.parse import urlparse
from PIL import Image
import time
import json

import asyncio
from asyncio_pool import AioPool
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OUTPUT_DIR = "screenshots"
SIZES = {"mobile": 400, "medium": 900, "wide": 1300}


class Website:
    def __init__(self, url):
        self.url = url

        pieces = urlparse(url)
        self.hostname = pieces.hostname
        if pieces.path.endswith("html"):
            self.urlpath = pieces.path.strip("/")
        else:
            self.urlpath = pieces.path.strip("/") + "/index.html"
        self.urlpath = self.urlpath.strip("/")

    async def load(self, page):
        """Load the web page"""
        logger.info(f"{self.url}: Loading")

        self.page = page
        self.page_title = "REQUEST FAILED"
        try:
            response = await self.page.goto(self.url, timeout=60000)
        except:
            logger.info(f"{self.url}: Failed to load page")
            # Exit early if fails to load
            self.successful_request = False
            return

        if response and response.ok:
            self.successful_request = True
        else:
            self.successful_request = False
        time.sleep(1)
        await self.page.evaluate(
            "window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });"
        )
        time.sleep(2)
        await self.get_desc_details()

    async def get_all_meta_tags(self):
        self.meta = {}
        self.meta['og:title'] = await self.get_meta("og:title")
        # self.meta['og:type'] = self.get_meta("og:type")
        self.meta['og:description'] = await self.get_meta("og:description")
        self.meta['og:image'] = await self.get_meta("og:image")
        # self.meta['twitter:card'] = self.get_meta("twitter:card", "name")

    async def get_meta(self, property, property_type='property'):
        logger.info(f"Getting {property}")
        qs = await self.page.query_selector(f"meta[{property_type}='{property}']")
        if qs:
            return await qs.get_attribute('content')
        else:
            return None

    async def screenshot_all(self):
        """Take a screenshot at each screen size"""
        for size in SIZES.keys():
            await self.screenshot_one(size)

    async def get_desc_details(self):
        logger.info(f"{self.url}: Getting desc details")
        self.page_title = await self.page.title() or self.urlpath

        logger.info(f"{self.url}: Page title is {self.page_title}")
        for character in ['|', '[', ']']:
            self.page_title = self.page_title.replace(character, "")

        await self.get_all_meta_tags()

    def build_desc(self):
        page_link = f"[{self.page_title}]({self.url})"
        metas = '<br>'.join([f":x: {key}" for key, value in self.meta.items() if value is None])

        if metas:
            desc = f"|{page_link}<br>{metas}<br>[how to fix](https://jonathansoma.com/everything/web/social-tags/)|"
        else:
            desc = f"|{page_link}|"
        return desc


    def get_table_row(self):
        """Markdown display of screenshots for this web page"""
        desc = self.build_desc()
        if self.successful_request:
            images = [
                f"[![{size}]({self.shot_path(size, 'thumb')})]({self.shot_path(size)})"
                for size in SIZES.keys()
            ]
        else:
            images = [ f"request failed" for size in SIZES.keys() ]

        return desc + "|".join(images) + "|"

    def shot_path(self, size, version="full"):
        """Returns the file path for a given screenshot size and version"""
        basename = self.urlpath.replace("/", "_").replace(" ", "%20")
        filename = f"{basename}-{size}-{version}.jpg"
        return Path(OUTPUT_DIR).joinpath(self.hostname).joinpath(filename)

    async def screenshot_one(self, size):
        """Create a screenshot at a given screen width"""
        width = SIZES[size]
        filepath = self.shot_path(size)
        await self.page.set_viewport_size({"width": width, "height": 700})
        time.sleep(0.5)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"{self.url}: {width}px screenshot to {filepath}")

        await self.page.screenshot(path=filepath, full_page=True, type='jpeg')

        thumb_path = self.shot_path(size, "thumb")
        logger.info(f"{self.url}: Creating thumbnail at {thumb_path}")
        with Image.open(filepath) as img:
            box = (0, 0, img.size[0], img.size[0])
            img.crop(box).resize((400, 400)).save(thumb_path)
    
    async def run_checks(self):
        logger.info(f"{self.url}: Running automatic checks")
        self.issues = []
        tiny_text = await self.page.evaluate("""
        () => [...document.querySelectorAll(".ai2html p")]
            .filter(d => window.getComputedStyle(d)['font-size'].indexOf("px") != -1)
            .filter(d => parseFloat(window.getComputedStyle(d)['font-size']) < 11)
            .map((d) => {
                return {
                    text: d.innerText,
                    size: window.getComputedStyle(d)['font-size']
                }
            })
        """)
        await self.page.set_viewport_size({"width": SIZES['mobile'], "height": 700})
        has_sideways_scroll = await self.page.evaluate("() => document.body.scrollWidth > window.innerWidth")
        missing_viewport_tag = await self.page.evaluate("() => !document.querySelector('meta[name=viewport]')")
        overlapping_elements = []
        for width in SIZES.values():
            await self.page.set_viewport_size({"width": width, "height": 700})
            new_overlaps = await self.page.evaluate("""
                () => {
                    function overlaps(e1, e2) {
                        const buffer = 5;
                        const rect1 = e1.getBoundingClientRect();
                        const rect2 = e2.getBoundingClientRect();
                        if(rect1.width == 0 || rect2.width == 0) {
                            return false
                        }
                        return !(rect1.right - buffer < rect2.left || 
                            rect1.left + buffer > rect2.right || 
                            rect1.bottom - buffer < rect2.top || 
                            rect1.top + buffer > rect2.bottom)
                    }

                    const elements = [...document.querySelectorAll('.ai2html p')];
                    const overlappingElements = []
                    for(let i = 0; i < elements.length; i++) {
                        const e1 = elements[i];
                        for(let j = i+1; j < elements.length; j++) {
                            const e2 = elements[j];
                            if(overlaps(e1, e2) && e1.innerText.trim() !== '' && e2.innerText.trim() !== '') {
                                overlappingElements.push({
                                    text1: e1.innerText,
                                    text2: e2.innerText,
                                    width: window.innerWidth
                                })
                            }
                        }
                    }
                    return overlappingElements
                }
            """)
            overlapping_elements.extend(new_overlaps)

        missing_fonts = await self.page.evaluate("""
            () => {
                function groupBy(objectArray, property) {
                    return objectArray.reduce((acc, obj) => {
                    const key = obj[property];
                    if (!acc[key]) {
                        acc[key] = [];
                    }
                    // Add object to list for given key's value
                    acc[key].push(obj);
                    return acc;
                    }, { });
                }
                
                const objects = [...document.querySelectorAll(".ai2html p")]
                    .filter(d => !(document.fonts.check("12px " + window.getComputedStyle(d)['font-family'])))
                    .map(d => {
                        return {
                            text: d.innerText,
                            font: window.getComputedStyle(d)['font-family']
                        }
                    })

                return groupBy(objects, 'font')
            }
        """)

        if not self.successful_request:
            self.issues.append("* **Could not access the page** - if you moved it, let me know!")

        if not await self.page.title():
            self.issues.append("* Needs a title, add a `<title>` tag to the `<head>`")

        if not self.urlpath.endswith("index.html"):
            name = self.urlpath.split("/")[-1].replace(".html", "")
            self.issues.append(f"* All HTML files should be named `index.html`. If this is a personal project, move `{self.urlpath}` into a folder (or repo) called `{name}`, then rename the file `index.html`. That way the project can be found at **/{name}** instead of **/{name}.html**. [Read more about index.html here](https://www.thoughtco.com/index-html-page-3466505) or how it works specifically with GitHub repos [on Fancy GitHub](https://jonathansoma.com/fancy-github/github-pages/#choosing-your-url)")

        github_link = await self.page.query_selector("a[href*='github.com']")
        if not github_link:
            self.issues.append("* Add a link to your project's GitHub repo, so people can review your code")

        # Page load doesn't really work with async?
        # self.load_duration_s = await self.page.evaluate(
        #     "() => performance.getEntriesByType('navigation')[0]['duration']"
        # ) / 1000
        # if self.load_duration_s > 5:
        #     self.issues.append(f"* Page took {round(self.load_duration_s, 2)}s to load, check image/table sizes")

        # alt tags
        img_missing_alt_tags = await self.page.query_selector_all('img:not([alt])')
        if img_missing_alt_tags:
            self.issues.append(f"* Image(s) need `alt` tags, [info here](https://abilitynet.org.uk/news-blogs/five-golden-rules-compliant-alt-text) and [tips here](https://twitter.com/FrankElavsky/status/1469023374529765385)")
            for img in img_missing_alt_tags[:5]:
                self.issues.append(f"    * Image `{await img.get_attribute('src')}` missing `alt` tag")
            if len(img_missing_alt_tags) > 5:
                self.issues.append(f"    * *and {len(img_missing_alt_tags) - 5} more*")

        # Descriptions for datawrapper charts
        datawrapper_charts = await self.page.query_selector_all(".dw-chart")
        for chart in datawrapper_charts:
            if not await chart.query_selector_all(".sr-only"):
                self.issues.append("* Datawrapper chart missing description, fill out *Alternative description for screen readers* section on Annotate tab, [tips here](https://twitter.com/FrankElavsky/status/1469023374529765385)")

        if ' ' in self.url or '_' in self.url:
            self.issues.append("* Change URL to use `-` instead of spaces or underscores")

        if self.url != self.url.lower():
            self.issues.append("* Change URL to be all in lowercase")

        if missing_viewport_tag:
            self.issues.append('* Missing viewport meta tag in `<head>`, needed to tell browser it\'s responsive. Add `<meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no">`')
        if has_sideways_scroll:
            self.issues.append(f"* Has sideways scrollbars in mobile version – check padding, margins, image widths")

        if tiny_text:
            self.issues.append("* Minimum font size should be 12px, enlarge text in Illustrator")
            for text in tiny_text[:7]:
                if text['text'] != "":
                    self.issues.append(f"    * Text `{text['text']}` is too small at {text['size']}")
            if len(tiny_text) > 7:
                self.issues.append(f"    * *and {len(tiny_text) - 7} more*")

        if overlapping_elements:
            self.issues.append("* Overlapping elements in ai2html, check [the overflow video](https://www.youtube.com/watch?v=6vHsnjTp3_w) or make a smaller size")
            for overlap in overlapping_elements[:7]:
                self.issues.append(f"   * Text `{overlap['text1']}` overlaps with `{overlap['text2']}` at screen width {overlap['width']}")
            if len(overlapping_elements) > 7:
                self.issues.append(f"   * *and {len(overlapping_elements) - 7} more*")

        if missing_fonts:
            self.issues.append("* Missing font(s), you might need web fonts – [text explanation](https://gist.github.com/jsoma/631621e0807b26d49f5aef5260f79162), [video explanation](https://www.youtube.com/watch?v=HNhIeb_jEYM&list=PLewNEVDy7gq3MSrrO3eMEW8PhGMEVh2X2&index=3)")
            for key, values in missing_fonts.items():
                self.issues.append(f"    * `{key}` font not found, used in {len(values)} text objects. Example: _{', '.join([v['text'] for v in values[:3]])}_")

    async def process_as_new_page(self, context):
        await self.load(await context.new_page())
        if self.successful_request:
            await self.screenshot_all()
        await self.run_checks()


async def scrape_all():
    urls = [w for w in Path("websites.txt").read_text().split("\n") if w != ""]
    websites = [Website(url) for url in urls]

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            args=[
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ]
        )
        context = await browser.new_context()

            
        async with AioPool(size=3) as pool:
            for site in websites:
                await pool.spawn(site.process_as_new_page(context))

        await browser.close()
    return websites

websites = asyncio.run(scrape_all())

table_starter = """
|url|mobile|medium|wide|
|---|---|---|---|
"""

toc_table = """<table><tr>"""
toc_image_num = 0

readme_md = """"""
issues_md = """"""
toc_md = """"""

prev_host = None
for site in websites:
    if site.hostname != prev_host:
        readme_md += issues_md
        readme_md += f"\n\n## {site.hostname}\n\n{table_starter}"
        toc_image_num += 1
        if site.successful_request:
            toc_table += f"""<td><a href="#{site.hostname.replace('.','')}"><img src="{site.shot_path('medium', 'thumb')}" alt="homepage screenshot"><br>{site.hostname}</a></td>\n"""
        else:
            toc_table += f"""<td>{site.hostname} request failed</td>\n"""
        if toc_image_num % 4 == 0:
            toc_table += "</tr><tr>\n"
        issues_md = f"\n\n### Automatic Checks\n\n"
        prev_host = site.hostname

    readme_md += site.get_table_row() + "\n"

    issues_md += f"**{site.url}**\n\n"
    if site.issues:
        issues_md += '\n'.join(site.issues) + '\n\n'
    else:
        issues_md += f"No issues found! 🎉\n\n"

toc_table += "</tr></table>"

readme_md += issues_md

readme_md = (
    "# Data Studio 2023 Personal Projects Test Page\n\n" +
    "Quick checks to make sure our pages are looking their best.\n\n" +
    toc_md +
    toc_table + 
    "\n\n" +
    readme_md
)

Path("README.md").write_text(readme_md)