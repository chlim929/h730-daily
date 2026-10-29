"""게시 전 알림: 검토 Issue에 지금 올라갈 이미지·캡션을 댓글로 달고 저장소 주인을 멘션한다.

사용: python scripts/remind.py [--date 2026-10-29]
"""
import argparse
import json
import os
import tempfile

from common import OWNER, POSTS, REPO, find_issue, head_sha, raw_url, run, target_date


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    args = ap.parse_args()

    date = target_date(args.date)
    png, meta_path = POSTS / f'{date}.png', POSTS / f'{date}.json'
    if not png.exists():
        print(f'{date}: posts/{date}.png 없음 — 알림 안 함')
        return
    meta = json.loads(meta_path.read_text(encoding='utf-8')) if meta_path.exists() else {}
    issue = find_issue(date)
    if not issue:
        print(f'{date}: 열린 검토 Issue 없음 — 알림 안 함')
        return

    repo = f'https://github.com/{REPO}'
    body = f"""@{OWNER} ⏰ **{meta.get('publish_after', '16:00')}(KST)에 아래 내용으로 게시돼요.**

<img src="{raw_url(png, head_sha())}" width="360">

```
{meta.get('caption', '')}
```

- 이미지 바꾸기: `{date}.png` 로 [posts 폴더에 업로드]({repo}/upload/main/posts)
- 캡션 바꾸기: [posts/{date}.json 편집]({repo}/edit/main/posts/{date}.json)
- 오늘 올리지 않기: `posts/{date}.png` 삭제
"""
    with tempfile.NamedTemporaryFile('w', suffix='.md', delete=False, encoding='utf-8') as f:
        f.write(body)
    run('gh', 'issue', 'comment', str(issue), '--repo', REPO, '--body-file', f.name)
    os.unlink(f.name)
    print(f'{date}: Issue #{issue} 에 게시 전 알림을 남김')


if __name__ == '__main__':
    main()
