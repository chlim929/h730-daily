"""prepare 뒤에 돌면서 게시 시각까지 기다렸다가 publish 를 실행한다.

GitHub 예약 실행은 몇 시간씩 늦게 시작되므로 예약 시각에 기대지 않고 여기서 직접 기다린다.
기다리는 동안 저장소를 계속 다시 읽으므로 캡션·게시 시각 수정과 카드 삭제가 반영된다.
작업 하나는 6시간까지만 돌 수 있어서, 더 기다려야 하면 자신을 다시 실행하고 끝낸다.

사용: python scripts/wait_publish.py --date 2026-11-03 [--reminded] [--dry-run]
"""
import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timedelta

import remind
from common import KST, POSTS, REPO, run, target_date

DEFAULT_TIME = '16:00'
REMIND_BEFORE = timedelta(hours=1)
MAX_RUN = timedelta(hours=5, minutes=30)
POLL_SECONDS = 300


def publish_time(date):
    meta_path = POSTS / f'{date}.json'
    meta = json.loads(meta_path.read_text(encoding='utf-8')) if meta_path.exists() else {}
    after = meta.get('publish_after', DEFAULT_TIME)
    return datetime.fromisoformat(f'{date}T{after}').replace(tzinfo=KST)


def dispatch(workflow, **inputs):
    fields = [arg for k, v in inputs.items() for arg in ('-f', f'{k}={v}')]
    run('gh', 'workflow', 'run', workflow, '--repo', REPO,
        '--ref', os.environ.get('GITHUB_REF_NAME') or 'main', *fields)


def refresh():
    try:
        run('git', 'pull', '--quiet')
    except subprocess.CalledProcessError as e:
        print(f'저장소를 다시 읽지 못함 (다음에 재시도): {e}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--reminded', action='store_true', default=os.environ.get('REMINDED') == 'true')
    ap.add_argument('--dry-run', action='store_true', default=os.environ.get('DRY_RUN') == 'true')
    args = ap.parse_args()

    started = datetime.now(KST)
    date = target_date(args.date)
    if date != started.strftime('%Y-%m-%d'):
        print(f'{date}: 오늘 카드가 아님 — 종료')
        return
    png = POSTS / f'{date}.png'
    dry_run = str(args.dry_run).lower()
    # 알림 시각이 지난 뒤 시작했다면 방금 Issue 알림이 갔으니 다시 보내지 않는다
    reminded = args.reminded or (png.exists() and started >= publish_time(date) - REMIND_BEFORE)

    while True:
        refresh()
        if not png.exists():
            print(f'{date}: 카드가 없음 (이미 게시했거나 삭제함) — 종료')
            return
        target = publish_time(date)
        now = datetime.now(KST)
        if now >= target:
            print(f'{date}: {target:%H:%M} 도착 — publish 실행')
            dispatch('publish.yml', date=date, dry_run=dry_run)
            return
        if now - started >= MAX_RUN:
            print(f'{date}: 실행 시간 한도 — 이어서 기다리는 작업을 새로 시작')
            dispatch('wait-publish.yml', date=date, reminded=str(reminded).lower(), dry_run=dry_run)
            return
        if not reminded and now >= target - REMIND_BEFORE:
            reminded = True
            if not args.dry_run:
                remind.send(date)
        time.sleep(min(POLL_SECONDS, max(1, (target - now).total_seconds())))


if __name__ == '__main__':
    main()
