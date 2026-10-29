"""prepare / publish 가 같이 쓰는 경로·날짜·git·Issue 도우미."""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')  # Windows 콘솔(cp949)에서 로컬 실행할 때

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / 'posts'
DONE = POSTS / 'done'
STATE = ROOT / 'state.json'
KST = timezone(timedelta(hours=9))

REPO = os.environ.get('GITHUB_REPOSITORY') or 'chlim929/h730-daily'
OWNER, NAME = REPO.split('/')
PAGES_MAKER = f'https://{OWNER}.github.io/{NAME}/maker.html'


def target_date(arg=None):
    """입력 날짜(YYYY-MM-DD) 또는 오늘(KST)."""
    value = (arg or os.environ.get('DATE') or '').strip()
    if not value:
        return datetime.now(KST).strftime('%Y-%m-%d')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise SystemExit(f'날짜 형식이 잘못됨: {value!r} (예: 2026-10-29)')
    return value


def issue_title(date):
    return f'[검토] {date} 일력'


def run(*cmd, capture=False):
    result = subprocess.run(cmd, cwd=ROOT, check=True, text=True, encoding='utf-8',
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else None


def git_commit_push(message, paths):
    """paths 를 커밋하고 push. 바뀐 게 없으면 False."""
    run('git', 'config', 'user.name', 'github-actions[bot]')
    run('git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    run('git', 'add', '-A', '--', *[str(p) for p in paths])
    if not run('git', 'status', '--porcelain', '--', *[str(p) for p in paths], capture=True):
        return False
    run('git', 'commit', '-m', message)
    for attempt in range(3):
        try:
            run('git', 'pull', '--rebase', '--quiet')
            run('git', 'push', '--quiet')
            return True
        except subprocess.CalledProcessError:
            if attempt == 2:
                raise
    return True


def head_sha():
    return run('git', 'rev-parse', 'HEAD', capture=True)


def raw_url(path, ref='main'):
    rel = Path(path).resolve().relative_to(ROOT).as_posix()
    return f'https://raw.githubusercontent.com/{REPO}/{ref}/{rel}'


def find_issue(date):
    """열려 있는 검토 Issue 번호 (없으면 None)."""
    out = run('gh', 'issue', 'list', '--repo', REPO, '--state', 'open', '--limit', '50',
              '--search', f'{date} in:title', '--json', 'number,title', capture=True)
    for issue in json.loads(out or '[]'):
        if issue['title'] == issue_title(date):
            return issue['number']
    return None


def set_output(**values):
    path = os.environ.get('GITHUB_OUTPUT')
    if not path:
        return
    with open(path, 'a', encoding='utf-8') as f:
        for k, v in values.items():
            f.write(f'{k}={v}\n')
