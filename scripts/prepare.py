"""오늘 H:730 레터에서 문장·출처를 뽑아 일력 카드 PNG를 만들고 검토 Issue를 연다.

사용: python scripts/prepare.py [--date 2026-10-29] [--force] [--no-push] [--letter URL]
"""
import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

from common import (OWNER, PAGES_MAKER, POSTS, REPO, ROOT, STATE, find_issue, git_commit_push,
                    head_sha, issue_title, raw_url, run, set_output, target_date)

ARCHIVE_URL = os.environ.get('H730_ARCHIVE_URL') or 'https://page.stibee.com/archives/70653'
MAX_LETTERS = 10
LETTER_LINK = re.compile(r'stibee\.com/(api/v1\.0/emails/share/|emails/|archives/\d+/.+)|stib\.ee/')
STYLE_NAMES = {'line': '물결밑줄형', 'box': '상자형'}
SUBSCRIBE_URL = ARCHIVE_URL.replace('/archives/', '/subscriptions/')
BASE_TAGS = '#칼럼 #필사 #오늘의문장 #뉴스레터'
TAG_PROMPT = (
    '인스타그램에 신문 칼럼을 소개하는 게시물의 해시태그를 고릅니다. '
    '칼럼 본문에서 가장 핵심적인 주제이면서 사람들이 실제로 검색할 만한 키워드 하나를 고르세요. '
    '띄어쓰기 없는 한 단어(예: 노동조합, 기후위기, 돌봄)로, # 없이 키워드만 답하세요. '
    '칼럼, 필사, 오늘의문장, 뉴스레터는 이미 붙어 있으니 고르지 마세요.'
)

# 레터 한 프레임에서 DATE 칸 뒤의 칸들을 {lines, bold} 로 돌려준다.
# H:730 레터는 문장 한 줄이 굵은 글씨 칸 하나씩이고, 그 다음 굵지 않은 칸이 출처다.
EXTRACT_JS = r"""
(dateSrc) => {
  const dateRe = new RegExp(dateSrc);
  const text = el => (el.innerText || '').replace(/ /g, ' ');
  const lines = s => s.split('\n').map(x => x.trim()).filter(Boolean);
  const squash = s => s.replace(/\s+/g, '');
  const body = document.body ? text(document.body) : '';
  const hasDate = /DATE\s*:/i.test(body);
  // 스티비 레터는 표 기반이라 가장 안쪽 td 하나가 에디터의 '칸' 하나다.
  const cells = [...document.querySelectorAll('td')]
    .filter(td => !td.querySelector('table') && text(td).trim());
  const i = cells.findIndex(c => dateRe.test(text(c)));
  if (i < 0) return { hasDate, found: false };
  // 칸 글자의 90% 이상이 굵으면 문장 칸
  const isBold = el => {
    let bold = 0, all = 0;
    const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    while (walk.nextNode()) {
      const n = squash(walk.currentNode.textContent).length;
      all += n;
      if (+getComputedStyle(walk.currentNode.parentElement).fontWeight >= 600) bold += n;
    }
    return all > 0 && bold >= all * 0.9;
  };
  const after = cells.slice(i + 1, i + 6).map(c => ({
    lines: lines(text(c)), bold: isBold(c), links: [...c.querySelectorAll('a[href]')].map(a => a.href),
  }));
  return { hasDate, found: true, cells: after };
}
"""


def date_pattern(date):
    y, m, d = date.split('-')
    return rf'DATE\s*:?\s*{y}\s*[/.]\s*0?{int(m)}\s*[/.]\s*0?{int(d)}(?!\d)'


def clean_source(s):
    s = re.sub(r'^\s*[—–―\-]+\s*', '', s)
    s = re.sub(r'^\s*출처\s*[:：]?\s*', '', s)
    return s.strip()


def parse_cells(cells):
    """DATE 뒤 굵은 칸들 = 문장 줄, 그 다음 첫 굵지 않은 칸 = 출처(칼럼 링크 포함)."""
    lines, source, link = [], '', ''
    for cell in cells:
        if cell['bold']:
            lines.extend(cell['lines'])
        else:
            source = ' '.join(cell['lines'])
            link = next((h for h in cell.get('links', []) if h.startswith('http')), '')
            break
    return lines, clean_source(source), link


def column_page(page, url, source):
    """칼럼 제목(예: '이토록 쉽고도 가능한 연대 [세상읽기]')과 본문. 제목을 못 가져오면 '[칼럼명]'."""
    title, body = '', ''
    if url:
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=30000)
            title, body = page.evaluate('''() => {
              const og = document.querySelector('meta[property="og:title"]');
              const text = document.querySelector('.article-text') || document.querySelector('article');
              return [(og && og.content) || document.title || '', text ? text.innerText : ''];
            }''')
        except Exception as e:
            print(f'칼럼 페이지를 못 읽음: {e}')
    return title.strip() or f"[{source.split(',')[-1].strip()}]", body.strip()


def topic_hashtag(title, body):
    """칼럼의 핵심이자 검색될 만한 키워드 하나 (# 없이). API 키가 없거나 실패하면 ''."""
    if not body or not os.environ.get('ANTHROPIC_API_KEY'):
        return ''
    import anthropic
    try:
        response = anthropic.Anthropic().beta.messages.create(
            model='claude-opus-5-5',
            max_tokens=2048,
            output_config={'effort': 'low'},
            betas=['server-side-fallback-2026-07-01'],
            fallbacks='default',
            system=TAG_PROMPT,
            messages=[{'role': 'user', 'content': f'제목: {title}\n\n본문:\n{body}'}],
        )
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
        print(f'해시태그 생성 실패: {e}')
        return ''
    if response.stop_reason == 'refusal':
        return ''
    text = ''.join(b.text for b in response.content if b.type == 'text')
    word = re.sub(r'[\s#]', '', text.strip().splitlines()[-1] if text.strip() else '')
    if not re.fullmatch(r'[0-9A-Za-z가-힣]{1,20}', word) or f'#{word}' in BASE_TAGS.split():
        print(f'해시태그 응답이 이상해서 뺌: {text!r}')
        return ''
    return word


def make_caption(date, title, link, tag=''):
    d = datetime.strptime(date, '%Y-%m-%d')
    parts = [
        f'[오늘의 문장] {d.year}년 {d.month}월 {d.day}일 {"월화수목금토일"[d.weekday()]}요일',
        '오늘은 이 문장을 곱씹어 읽어보세요.',
        '____________________',
        f'🖋 {title}',
    ]
    if link:
        parts.append(f'👉🏻 칼럼 더 읽기\n{link}')
    parts.append(f'📨 더 많은 칼럼은 H:730 뉴스레터에서\n{SUBSCRIBE_URL}')
    parts.append(BASE_TAGS + (f' #{tag}' if tag else ''))
    return '\n\n'.join(parts)


def letter_links(page):
    page.goto(ARCHIVE_URL, wait_until='networkidle', timeout=60000)
    page.wait_for_timeout(1500)
    links = []
    for frame in page.frames:
        for href in frame.evaluate('() => [...document.querySelectorAll("a[href]")].map(a => a.href)'):
            href = href.split('#')[0]
            if href.rstrip('/') != ARCHIVE_URL.rstrip('/') and LETTER_LINK.search(href) and href not in links:
                links.append(href)
    if not links:
        text = page.evaluate('() => document.body ? document.body.innerText : ""')
        sys.exit(f'아카이브에서 레터 링크를 찾지 못함: {ARCHIVE_URL}\n--- 페이지 내용 ---\n{text[:1500]}')
    return links[:MAX_LETTERS]


def find_letter(page, date, links):
    pattern = date_pattern(date)
    saw_date_field = False
    for url in links:
        page.goto(url, wait_until='networkidle', timeout=60000)
        for frame in page.frames:
            try:
                res = frame.evaluate(EXTRACT_JS, pattern)
            except Exception:
                continue
            saw_date_field = saw_date_field or res['hasDate']
            if res['found']:
                return url, res
    if not saw_date_field:
        sys.exit('레터들에서 "DATE :" 칸을 하나도 못 찾음 — 레터 형식이 바뀌었는지 확인 필요\n' + '\n'.join(links))
    return None, None


def render_png(page, date, lines, source, style, out):
    params = {'date': date, 'style': style, 'src': source}
    params.update({f'l{i}': line for i, line in enumerate(lines, 1)})
    page.goto((ROOT / 'maker.html').as_uri() + '?' + urlencode(params), wait_until='load')
    button = page.get_by_role('button', name='PNG로 저장')
    button.wait_for(timeout=60000)
    page.wait_for_function('document.fonts && document.fonts.status === "loaded"', timeout=60000)
    page.wait_for_timeout(1500)  # 폰트 로딩 후 제작기가 글자 폭을 다시 재는 시간
    with page.expect_download(timeout=60000) as dl:
        button.click()
    dl.value.save_as(out)


def issue_body(date, meta, png_url):
    params = {'date': date, 'style': meta['style'], 'src': meta['source']}
    params.update({f'l{i}': line for i, line in enumerate(meta['lines'], 1)})
    review = PAGES_MAKER + '?' + urlencode(params)
    quote = '\n'.join(f'> {line}' for line in meta['lines'])
    repo = f'https://github.com/{REPO}'
    return f"""@{OWNER} 오늘 카드가 준비됐어요. **{meta.get('publish_after', '16:00')}(KST)** 게시 예정이에요.

![{date} 일력]({png_url})

{quote}
>
> — {meta['source']}

스타일: **{STYLE_NAMES[meta['style']]}** · 원문: {meta['letter_url']}

### 1. 검토·수정
- [제작기에서 열기]({review}) → 글자 크기·자간 조정 → **PNG로 저장**
  - 링크 끝에 `&size=40&tracking=-1` 처럼 붙여도 바로 적용돼요 (기본 size=42, tracking=0)
- 받은 `일력_{date}.png` 의 이름에서 `일력_` 을 지워 **`{date}.png`** 로 바꾼 뒤
  [posts 폴더에 업로드]({repo}/upload/main/posts) 하면 덮어써져요.

### 2. 캡션 수정
[posts/{date}.json 편집]({repo}/edit/main/posts/{date}.json) 에서 `"caption"` 값을 고치고 Commit. 줄바꿈은 `\\n`.
마지막 줄의 주제 해시태그: **{'#' + meta['topic_tag'] if meta.get('topic_tag') else '없음 — 직접 하나 붙여 주세요'}**

```
{meta['caption']}
```

### 3. 게시
{meta.get('publish_after', '16:00')}(KST)에 자동 게시되고 이 Issue는 닫혀요. 오늘 올리지 않으려면 `posts/{date}.png` 를 삭제하세요.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--force', action='store_true', default=os.environ.get('FORCE') == 'true')
    ap.add_argument('--no-push', action='store_true', help='로컬 테스트: 커밋·Issue 없이 파일만 만든다')
    ap.add_argument('--letter', default=os.environ.get('LETTER') or None,
                    help='아카이브 대신 이 레터 URL을 바로 읽는다')
    args = ap.parse_args()

    date = target_date(args.date)
    png, meta_path = POSTS / f'{date}.png', POSTS / f'{date}.json'
    set_output(date=date, made='false')
    if meta_path.exists() and not args.force:
        print(f'{date}: 이미 만들어져 있음 (다시 만들려면 force)')
        return

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(accept_downloads=True, viewport={'width': 1400, 'height': 1000})
        links = [args.letter] if args.letter else letter_links(page)
        url, res = find_letter(page, date, links)
        if not url:
            print(f'{date}: 오늘 레터 없음 (주말·휴간) — 종료')
            return
        lines, source, link = parse_cells(res['cells'])
        if not 1 <= len(lines) <= 3 or not source:
            sys.exit(f'문장/출처 추출 실패: lines={lines} source={source!r}\n--- DATE 이후 칸 ---\n'
                     + json.dumps(res['cells'], ensure_ascii=False, indent=1)[:3000])

        state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
        style = (os.environ.get('STYLE') or '').strip().replace('auto', '')
        if style:
            if style not in STYLE_NAMES:
                sys.exit(f'style 은 line 또는 box: {style!r}')
        elif state.get('lastDate') == date:  # 같은 날짜를 다시 만들 땐 스타일 유지
            style = state['lastStyle']
        else:
            style = 'box' if state.get('lastStyle') == 'line' else 'line'
        title, body = column_page(page, link, source)
        tag = topic_hashtag(title, body)
        print(f'{date}: {url}\n  문장: {lines}\n  출처: {source}\n  칼럼: {title} {link}\n'
              f'  해시태그: #{tag or "(없음)"}\n  스타일: {style}')

        POSTS.mkdir(exist_ok=True)
        render_png(page, date, lines, source, style, png)
        browser.close()

    meta = {
        'date': date, 'lines': lines, 'source': source, 'style': style,
        'caption': make_caption(date, title, link, tag), 'letter_url': url,
        'column_title': title, 'column_url': link, 'topic_tag': tag,
    }
    publish_after = (os.environ.get('PUBLISH_AFTER') or '').strip()
    if publish_after:
        if not re.fullmatch(r'\d{2}:\d{2}', publish_after):
            sys.exit(f'publish_after 형식이 잘못됨: {publish_after!r} (예: 17:00)')
        meta['publish_after'] = publish_after
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    state.update(lastStyle=style, lastDate=date)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    set_output(made='true')
    if args.no_push:
        print(f'저장: {png}')
        return

    git_commit_push(f'일력 {date} 생성 ({style})', [png, meta_path, STATE])
    body = issue_body(date, meta, raw_url(png, head_sha()))
    with tempfile.NamedTemporaryFile('w', suffix='.md', delete=False, encoding='utf-8') as f:
        f.write(body)
    number = find_issue(date)
    if number:
        run('gh', 'issue', 'edit', str(number), '--repo', REPO, '--body-file', f.name)
        run('gh', 'issue', 'comment', str(number), '--repo', REPO, '--body', '카드를 다시 만들었어요. 본문을 새로 고쳤어요.')
    else:
        run('gh', 'issue', 'create', '--repo', REPO, '--title', issue_title(date), '--body-file', f.name)
    os.unlink(f.name)


if __name__ == '__main__':
    main()
