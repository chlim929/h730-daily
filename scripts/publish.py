"""posts/오늘.png 를 JPEG로 바꿔 인스타그램에 게시하고 검토 Issue를 닫는다.

사용: python scripts/publish.py [--date 2026-10-29] [--dry-run]
환경변수: IG_USER_ID, IG_ACCESS_TOKEN, IG_APP_SECRET(선택)
"""
import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image, ImageOps

from common import (DONE, KST, POSTS, REPO, find_issue, git_commit_push, head_sha, raw_url, run,
                    target_date)

GRAPH = 'https://graph.instagram.com'
SIZE = (1080, 1350)


def ig(method, path, **params):
    token = os.environ['IG_ACCESS_TOKEN']
    params['access_token'] = token
    secret = os.environ.get('IG_APP_SECRET')
    if secret:
        params['appsecret_proof'] = hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()
    data = urlencode(params)
    if method == 'GET':
        req = Request(f'{GRAPH}/{path}?{data}')
    else:
        req = Request(f'{GRAPH}/{path}', data=data.encode(), method='POST')
    try:
        with urlopen(req, timeout=60) as res:
            return json.loads(res.read())
    except HTTPError as e:
        # 응답 본문엔 토큰이 없다. 요청 URL은 토큰을 담고 있으니 출력하지 않는다.
        raise RuntimeError(f'Instagram API {method} /{path} → {e.code}: {e.read().decode(errors="replace")}') from None


def to_jpeg(png, jpg):
    img = Image.open(png).convert('RGB')
    if img.size != SIZE:
        img = ImageOps.fit(img, SIZE, Image.LANCZOS)
    img.save(jpg, 'JPEG', quality=95, optimize=True)


def wait_until_public(url, timeout=180):
    deadline = time.time() + timeout
    while True:
        try:
            with urlopen(Request(url, method='HEAD'), timeout=30) as res:
                if res.status == 200:
                    return
        except (HTTPError, URLError):
            pass
        if time.time() > deadline:
            raise RuntimeError(f'이미지 URL이 열리지 않음: {url}')
        time.sleep(5)


def post_to_instagram(image_url, caption):
    # 계정 ID는 토큰에서 직접 확인한다 (Secret의 IG_USER_ID와 다르면 토큰 쪽을 쓴다)
    me = ig('GET', 'me', fields='user_id,username')
    uid = str(me.get('user_id') or os.environ['IG_USER_ID'])
    print(f'인스타 계정: @{me.get("username")}')
    if os.environ.get('IG_USER_ID') and uid != os.environ['IG_USER_ID']:
        print('⚠ Secret IG_USER_ID 와 토큰의 계정 ID가 달라서 토큰 기준으로 게시함')
    container = ig('POST', f'{uid}/media', image_url=image_url, caption=caption)['id']
    for _ in range(60):
        status = ig('GET', container, fields='status_code,status')
        if status.get('status_code') == 'FINISHED':
            break
        if status.get('status_code') in ('ERROR', 'EXPIRED'):
            raise RuntimeError(f'미디어 처리 실패: {status}')
        time.sleep(5)
    else:
        raise RuntimeError('미디어 처리가 5분 안에 끝나지 않음')
    media_id = ig('POST', f'{uid}/media_publish', creation_id=container)['id']
    return ig('GET', media_id, fields='permalink').get('permalink', '')


def comment(issue, text):
    if issue:
        run('gh', 'issue', 'comment', str(issue), '--repo', REPO, '--body', text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--dry-run', action='store_true', default=os.environ.get('DRY_RUN') == 'true')
    args = ap.parse_args()

    date = target_date(args.date)
    png, jpg, meta_path = (POSTS / f'{date}{ext}' for ext in ('.png', '.jpg', '.json'))
    if not png.exists():
        print(f'{date}: posts/{date}.png 없음 — 종료')
        return
    meta = json.loads(meta_path.read_text(encoding='utf-8')) if meta_path.exists() else {}
    caption = meta.get('caption', '')
    # json의 "publish_after": "17:00" — 예약 실행은 그 시각 전이면 건너뛴다 (수동 실행은 무시)
    after = meta.get('publish_after')
    if after and os.environ.get('GITHUB_EVENT_NAME') == 'schedule':
        if datetime.now(KST) < datetime.fromisoformat(f'{date}T{after}').replace(tzinfo=KST):
            print(f'{date}: {after} 이후 게시로 지정됨 — 이번 실행은 건너뜀')
            return
    to_jpeg(png, jpg)

    if args.dry_run:
        print(f'[dry run] {jpg} 생성 ({SIZE[0]}x{SIZE[1]}), 커밋·게시 안 함\n--- caption ---\n{caption}')
        return

    issue = find_issue(date)
    try:
        git_commit_push(f'일력 {date} JPEG 변환', [jpg])
        image_url = raw_url(jpg, head_sha())
        wait_until_public(image_url)
        permalink = post_to_instagram(image_url, caption)
    except Exception as e:
        comment(issue, f'❌ 게시 실패\n\n```\n{e}\n```\n\n고친 뒤 Actions → publish 를 수동 실행하세요.')
        sys.exit(f'게시 실패: {e}')

    print(f'{date}: 게시 완료 {permalink}')
    DONE.mkdir(parents=True, exist_ok=True)
    moved = [p for p in (png, jpg, meta_path) if p.exists()]
    for p in moved:
        p.rename(DONE / p.name)
    git_commit_push(f'일력 {date} 게시 완료', moved + [DONE / p.name for p in moved])
    if issue:
        comment(issue, f'✅ 인스타그램에 게시했어요: {permalink}')
        run('gh', 'issue', 'close', str(issue), '--repo', REPO)


if __name__ == '__main__':
    main()
