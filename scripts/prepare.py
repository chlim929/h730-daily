"""오늘 H:730 레터에서 문장·출처를 뽑아 일력 카드 PNG를 만들고 검토 Issue를 연다.

사용: python scripts/prepare.py [--date 2026-10-29] [--force] [--no-push] [--letter URL]
"""
import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

from common import (PAGES_MAKER, POSTS, REPO, ROOT, STATE, find_issue, git_commit_push,
                    head_sha, issue_title, raw_url, run, set_output, target_date)

ARCHIVE_URL = os.environ.get('H730_ARCHIVE_URL') or 'https://page.stibee.com/archives/70653'
MAX_LETTERS = 10
LETTER_LINK = re.compile(r'stibee\.com/(api/v1\.0/emails/share/|emails/|archives/\d+/.+)|stib\.ee/')
STYLE_NAMES = {'line': '물결밑줄형', 'box': '상자형'}

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
  const after = cells.slice(i + 1, i + 6).map(c => ({ lines: lines(text(c)), bold: isBold(c) }));
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
    """DATE 뒤 굵은 칸들 = 문장 줄, 그 다음 첫 굵지 않은 칸 = 출처."""
    lines, source = [], ''
    for cell in cells:
        if cell['bold'] and not source:
            lines.extend(cell['lines'])
        else:
            source = ' '.join(cell['lines'])
            break
    return lines, clean_source(source)


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
    return f"""![{date} 일력]({png_url})

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

```
{meta['caption']}
```

### 3. 게시
16:00(KST)에 자동 게시되고 이 Issue는 닫혀요. 오늘 올리지 않으려면 `posts/{date}.png` 를 삭제하세요.
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
        lines, source = parse_cells(res['cells'])
        if not 1 <= len(lines) <= 3 or not source:
            sys.exit(f'문장/출처 추출 실패: lines={lines} source={source!r}\n--- DATE 이후 칸 ---\n'
                     + json.dumps(res['cells'], ensure_ascii=False, indent=1)[:3000])

        state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
        style = 'box' if state.get('lastStyle') == 'line' else 'line'
        print(f'{date}: {url}\n  문장: {lines}\n  출처: {source}\n  스타일: {style}')

        POSTS.mkdir(exist_ok=True)
        render_png(page, date, lines, source, style, png)
        browser.close()

    meta = {
        'date': date, 'lines': lines, 'source': source, 'style': style,
        'caption': '\n'.join(lines) + '\n\n— ' + source, 'letter_url': url,
    }
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
