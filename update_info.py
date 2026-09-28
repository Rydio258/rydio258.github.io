#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动生成电台级和单曲级 info.txt，并同步到 GitHub。

排序优先级：
    1. mp3 ID3 标签里的 disc + track 序号（最准）
    2. 文件名开头的数字
    3. 自然排序

元数据优先级（用于生成 info）：
    1. ID3 标签里的 title / artist / album
    2. 文件名解析

用法：
    python update_info.py
    python update_info.py --only-new
    python update_info.py --no-songs
    python update_info.py --no-netease
    python update_info.py --watch
    python update_info.py --dry-run
    python update_info.py --show-meta      # 只打印每首歌的元数据，不生成
"""

import os
import re
import sys
import json
import time
import hashlib
import argparse
import subprocess
from pathlib import Path

try:
    import requests
except ImportError:
    print('缺少依赖：pip install requests')
    sys.exit(1)

try:
    from mutagen import File as MutagenFile
    from mutagen.id3 import ID3
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False
    print('提示：安装 mutagen 可读取 mp3 元数据 → pip install mutagen')


# ==================== 配置 ====================

API_KEY = os.environ.get('DEEPSEEK_API_KEY', '')
API_URL = 'https://api.deepseek.com/chat/completions'
API_MODEL = 'deepseek-chat'
API_TIMEOUT = 60

REPO_DIR = Path(__file__).resolve().parent
MUSIC_DIR = REPO_DIR / 'music'

LOCAL_ONLY_DIRS = ['classical']
NO_NETEASE_DIRS = ['classical']

CACHE_FILE = REPO_DIR / '.info-cache.json'
GIT_COMMIT_MSG = 'auto: 更新 info.txt'
WATCH_INTERVAL = 600

NETEASE_SEARCH_URL = 'https://music.163.com/api/search/get/web'
NETEASE_LYRIC_URL = 'https://music.163.com/api/song/lyric'
NETEASE_HEADERS = {
    'Referer': 'https://music.163.com/',
    'User-Agent': 'Mozilla/5.0 (compatible; RadioInfoBot/1.0)',
}
NETEASE_TIMEOUT = 10

# ============================================


def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}')


# ==================== 元数据读取 ====================

def read_mp3_meta(path):
    """
    读取 mp3 元数据，返回：
    {
        'track': int | None,
        'disc': int | None,
        'title': str,
        'artist': str,
        'album': str,
        'album_artist': str,
        'year': str,
        'genre': str,
        'composer': str,
        'duration': float,
    }
    读不到就返回空字段。
    """
    meta = {
        'track': None,
        'disc': None,
        'title': '',
        'artist': '',
        'album': '',
        'album_artist': '',
        'year': '',
        'genre': '',
        'composer': '',
        'duration': 0.0,
    }

    if not HAS_MUTAGEN:
        return meta

    try:
        audio = MutagenFile(str(path), easy=True)
        if audio is None:
            return meta

        # 时长
        if audio.info and hasattr(audio.info, 'length'):
            meta['duration'] = round(audio.info.length, 1)

        def first(key):
            v = audio.get(key)
            if not v:
                return ''
            return str(v[0]).strip()

        meta['title'] = first('title')
        meta['artist'] = first('artist')
        meta['album'] = first('album')
        meta['album_artist'] = first('albumartist')
        meta['year'] = first('date') or first('year')
        meta['genre'] = first('genre')
        meta['composer'] = first('composer')

        # track 可能形如 "3" 或 "3/12"
        trk = first('tracknumber')
        if trk:
            m = re.match(r'^\s*(\d+)', trk)
            if m:
                meta['track'] = int(m.group(1))

        # disc 可能形如 "1" 或 "1/2"
        disc = first('discnumber')
        if disc:
            m = re.match(r'^\s*(\d+)', disc)
            if m:
                meta['disc'] = int(m.group(1))

    except Exception as e:
        log(f'    读取元数据失败 {path.name}: {e}')

    return meta


def get_sort_key(path):
    """
    排序 key：
    1. 有 disc + track → (0, disc, track, 自然名)
    2. 只有 track       → (0, 1, track, 自然名)
    3. 文件名有数字     → (1, 0, number, 自然名)
    4. 都没有           → (2, 0, 0, 自然名)
    """
    meta = read_mp3_meta(path) if HAS_MUTAGEN else {}

    stem = path.stem
    natural = natural_sort_key(stem)

    disc = meta.get('disc')
    track = meta.get('track')

    if track is not None:
        return (0, disc if disc is not None else 1, track, natural)

    # 退回文件名开头的数字
    m = re.match(r'^\s*(\d+)', stem)
    if m:
        return (1, 0, int(m.group(1)), natural)

    return (2, 0, 0, natural)


def sort_files(paths):
    return sorted(paths, key=get_sort_key)


def natural_sort_key(s):
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]


# ==================== 缓存 ====================

def load_cache():
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding='utf-8'))
        except Exception:
            return {}
    return {}


def save_cache(cache):
    CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )


def hash_paths(paths):
    h = hashlib.sha256()
    for p in sorted(paths, key=lambda x: x.name):
        try:
            size = p.stat().st_size if p.exists() else 0
        except OSError:
            size = 0
        h.update(f'{p.name}:{size}\n'.encode('utf-8'))
    return h.hexdigest()


# ==================== 文件名解析（元数据缺失时用） ====================

def parse_filename(name):
    stem = Path(name).stem.strip()
    stem_clean = re.sub(r'^\s*\d+[\s.\-_]+', '', stem).strip()
    if not stem_clean:
        stem_clean = stem

    if ' - ' in stem_clean:
        parts = stem_clean.split(' - ', 1)
        return {'artist': parts[0].strip(), 'title': parts[1].strip()}
    if '-' in stem_clean and ' ' not in stem_clean.split('-')[0]:
        parts = stem_clean.split('-', 1)
        return {'artist': parts[0].strip(), 'title': parts[1].strip()}
    return {'artist': '', 'title': stem_clean}


def get_song_display_name(path):
    """生成用于显示和 AI 输入的曲名"""
    meta = read_mp3_meta(path) if HAS_MUTAGEN else {}
    title = meta.get('title', '').strip()
    if title:
        return title
    parsed = parse_filename(path.name)
    return parsed['title'] or path.stem


# ==================== 网易云 ====================

def netease_search(keyword, limit=5):
    try:
        resp = requests.get(
            NETEASE_SEARCH_URL,
            params={'s': keyword, 'type': 1, 'offset': 0, 'limit': limit, 'total': 'true'},
            headers=NETEASE_HEADERS,
            timeout=NETEASE_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        songs = (data.get('result') or {}).get('songs') or []
        if not songs:
            return None
        s = songs[0]
        artists = ' / '.join(a.get('name', '') for a in (s.get('artists') or []))
        album = (s.get('album') or {}).get('name', '')
        return {
            'id': s.get('id'),
            'title': s.get('name', ''),
            'artist': artists,
            'album': album,
        }
    except Exception as e:
        log(f'    网易云搜索失败：{e}')
        return None


def netease_lyric(song_id):
    if not song_id:
        return ''
    try:
        resp = requests.get(
            NETEASE_LYRIC_URL,
            params={'id': song_id, 'lv': 1, 'kv': 1, 'tv': -1},
            headers=NETEASE_HEADERS,
            timeout=NETEASE_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return (data.get('lrc') or {}).get('lyric', '') or ''
    except Exception as e:
        log(f'    歌词获取失败：{e}')
        return ''


def strip_lrc_timestamps(lyric):
    if not lyric:
        return ''
    lines = []
    for raw in lyric.split('\n'):
        text = re.sub(r'\[\d+:\d+(?:\.\d+)?\]', '', raw).strip()
        if text and not text.startswith('作词') and not text.startswith('作曲'):
            lines.append(text)
    return '\n'.join(lines)


def fetch_netease(mp3_path):
    meta = read_mp3_meta(mp3_path) if HAS_MUTAGEN else {}
    artist = meta.get('artist', '').strip()
    title = meta.get('title', '').strip()
    if not title:
        parsed = parse_filename(mp3_path.name)
        artist = artist or parsed['artist']
        title = parsed['title']

    keyword = f'{artist} {title}'.strip() or title
    info = netease_search(keyword)
    if not info:
        return None
    lyric = netease_lyric(info['id'])
    info['lyrics'] = strip_lrc_timestamps(lyric)[:1200]
    return info


# ==================== 扫描 ====================

def scan_stations():
    if not MUSIC_DIR.is_dir():
        return []

    local_only_lower = [d.lower() for d in LOCAL_ONLY_DIRS]
    stations = []

    top_dirs = sorted(
        [d for d in MUSIC_DIR.iterdir() if d.is_dir() and not d.name.startswith('.')],
        key=lambda p: natural_sort_key(p.name)
    )

    for d in top_dirs:
        songs = []
        works = []

        items = sort_files([
            x for x in d.iterdir() if not x.name.startswith('.')
        ])

        for item in items:
            if item.is_dir():
                works.append({'name': item.name, 'path': item})
                # 作品里的 mp3 按元数据排序
                inner_mp3s = sort_files([
                    f for f in item.iterdir()
                    if f.is_file() and f.suffix.lower() == '.mp3'
                ])
                songs.extend(inner_mp3s)
            elif item.suffix.lower() == '.mp3':
                songs.append(item)

        if songs or works:
            stations.append({
                'name': d.name,
                'path': d,
                'songs': songs,
                'works': works,
                'local_only': d.name.lower() in local_only_lower,
            })

    return stations


# ==================== DeepSeek ====================

def call_deepseek(system, user, temperature=0.8):
    if not API_KEY:
        raise RuntimeError('未设置 DEEPSEEK_API_KEY 环境变量')
    payload = {
        'model': API_MODEL,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ],
        'temperature': temperature,
        'max_tokens': 1200,
    }
    resp = requests.post(
        API_URL,
        headers={'Authorization': f'Bearer {API_KEY}', 'Content-Type': 'application/json'},
        json=payload,
        timeout=API_TIMEOUT,
    )
    resp.raise_for_status()
    text = resp.json()['choices'][0]['message']['content'].strip()
    if text.startswith('```'):
        lines = text.split('\n')[1:]
        if lines and lines[-1].strip() == '```':
            lines = lines[:-1]
        text = '\n'.join(lines).strip()
    return text


def build_station_prompt(station):
    lines = [f'电台名称：{station["name"]}', '']

    if station['songs']:
        lines.append('曲目列表（按播放顺序）：')
        for idx, s in enumerate(station['songs'], 1):
            display = get_song_display_name(s)
            lines.append(f'  {idx:02d}. {display}')
        lines.append('')

    if station['works']:
        lines.append('多乐章作品：')
        for w in station['works']:
            lines.append(f'  - {w["name"]}')

    context = '\n'.join(lines)

    return f"""你是一个复古调频电台的主持人。根据下面的曲目信息，为名为「{station['name']}」的电台写一份 info.txt 文案。

{context}

严格按下面的格式输出，不要加任何其他说明、标题或代码块标记：

第一段：1-2 句电台介绍，有画面感，像老式收音机的节目单。不要 emoji。

---
[morning] 一句早间节目台词
[noon] 一句午间台词
[afternoon] 一句下午台词
[evening] 一句傍晚台词
[night] 一句深夜台词
[weekend] 一句周末台词

要求：
- 全中文
- 台词里可以用 {{song}} 表示当前歌曲名，{{time}} 表示时间，{{work}} 表示作品名
- 语气自然、克制、有点文学感
- 每句台词不超过 40 字
- 直接输出，不要用 markdown 代码块包裹"""


def build_song_prompt(mp3_path, netease_info):
    meta = read_mp3_meta(mp3_path) if HAS_MUTAGEN else {}

    parts = [f'音频文件：{mp3_path.name}']

    # 优先用 ID3 元数据
    if meta.get('title'):
        parts.append(f'曲名：{meta["title"]}')
    if meta.get('artist'):
        parts.append(f'艺术家：{meta["artist"]}')
    if meta.get('album'):
        parts.append(f'专辑：{meta["album"]}')
    if meta.get('composer'):
        parts.append(f'作曲家：{meta["composer"]}')
    if meta.get('track'):
        parts.append(f'音轨号：{meta["track"]}')
    if meta.get('year'):
        parts.append(f'年份：{meta["year"]}')

    # 网易云补充
    if netease_info:
        if not meta.get('title') and netease_info.get('title'):
            parts.append(f'曲名（网易云）：{netease_info["title"]}')
        if not meta.get('artist') and netease_info.get('artist'):
            parts.append(f'艺术家（网易云）：{netease_info["artist"]}')
        if netease_info.get('lyrics'):
            parts.append('')
            parts.append('歌词节选：')
            parts.append(netease_info['lyrics'][:600])

    context = '\n'.join(parts)

    return f"""你是一个古典音乐或流行音乐的撰稿人。根据下面的信息，为这首曲子写一份 info.txt。

{context}

严格按下面的格式输出，不要加其他说明、标题或代码块标记：

第一段：1-2 句简介，点出这首曲子的第一印象。

---
创作背景或相关细节，2-3 句话，简洁有信息量。如果是古典音乐，可以谈作品所属的套曲、作曲家、创作年代或改编情况。

---
听感点评，2-3 句话，可以谈配器、情绪、节奏、值得注意的段落，克制、有画面感。

要求：
- 全中文
- 每段不要超过 120 字
- 不要 emoji
- 直接输出，不要用 markdown 代码块包裹
- 如果上面有"音轨号"和"专辑"信息，请把它们对应到作品的正确位置
- 不要编造具体的年份或事件，如果不确定就用"约"或"据传"
"""


# ==================== 本地 info 规范化 ====================

def normalize_local_info(path, dry_run=False):
    if not path.exists():
        return False
    try:
        content = path.read_text(encoding='utf-8')
    except Exception as e:
        log(f'    ✗ 读取失败：{e}')
        return False

    normalized = content.replace('\r\n', '\n').replace('\r', '\n')
    lines = [line.rstrip() for line in normalized.split('\n')]
    normalized = '\n'.join(lines)
    normalized = normalized.rstrip('\n') + '\n'

    if normalized == content:
        return False
    if dry_run:
        log(f'    [DRY RUN] 将规范化 {path.relative_to(REPO_DIR)}')
        return False
    path.write_text(normalized, encoding='utf-8')
    log(f'    ✓ 规范化 {path.relative_to(REPO_DIR)}')
    return True


# ==================== 写入 ====================

def write_text_file(path, content, dry_run=False):
    new_content = content.rstrip() + '\n'
    old = path.read_text(encoding='utf-8') if path.exists() else ''
    if old == new_content:
        return False
    if dry_run:
        log(f'    [DRY RUN] 将写入 {path.relative_to(REPO_DIR)}')
        print('      ' + new_content.replace('\n', '\n      '))
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_content, encoding='utf-8')
    log(f'    ✓ 写入 {path.relative_to(REPO_DIR)}')
    return True


def should_process(info_path, current_hash, cached_hash, only_new):
    if only_new:
        return not info_path.exists()
    return (not info_path.exists()) or (current_hash != cached_hash)


# ==================== 处理：本地模式 ====================

def process_local_station(station, cache, args, changed_paths):
    name = station['name']
    station_dir = station['path']
    any_change = False

    info_path = station_dir / 'info.txt'
    if info_path.exists():
        log(f'  本地电台级：{name}')
        if normalize_local_info(info_path, dry_run=args.dry_run):
            changed_paths.append(info_path)
            any_change = True
    else:
        log(f'  本地电台级：{name}（缺 info.txt，跳过）')

    for work in station['works']:
        work_name = work['name']
        work_path = work['path']
        work_info = work_path / 'info.txt'
        if work_info.exists():
            log(f'    作品：{work_name}')
            if normalize_local_info(work_info, dry_run=args.dry_run):
                changed_paths.append(work_info)
                any_change = True
        else:
            log(f'    作品：{work_name}（缺 info.txt，跳过）')

    return any_change


# ==================== 处理：DeepSeek 模式 ====================

def process_station(station, cache, args, changed_paths):
    name = station['name']
    station_dir = station['path']
    any_change = False

    all_items = list(station['songs'])
    for w in station['works']:
        all_items.append(w['path'])
    current_hash = hash_paths(all_items)
    cached_hash = cache.get(f'{name}/__station__', {}).get('hash', '')
    info_path = station_dir / 'info.txt'

    if should_process(info_path, current_hash, cached_hash, args.only_new):
        log(f'  电台级：{name}')
        try:
            if args.dry_run:
                log(f'    [DRY RUN] prompt 长度约 {len(build_station_prompt(station))} 字')
            else:
                prompt = build_station_prompt(station)
                content = call_deepseek(
                    '你是一个中文电台文案写手，输出简洁、克制、有画面感的文字。',
                    prompt
                )
                if write_text_file(info_path, content):
                    changed_paths.append(info_path)
                    any_change = True
                cache[f'{name}/__station__'] = {'hash': current_hash, 'updated': time.time()}
        except Exception as e:
            log(f'    ✗ 失败：{e}')
    else:
        log(f'  跳过电台级：{name}')

    if args.no_songs:
        return any_change

    no_netease_dirs_lower = [d.lower() for d in NO_NETEASE_DIRS]
    skip_netease_here = args.no_netease or (name.lower() in no_netease_dirs_lower)

    for mp3 in station['songs']:
        song_info_path = mp3.with_suffix('.info.txt')
        song_hash = hash_paths([mp3])
        song_cache_key = f'{name}/__song__/{mp3.stem}'
        song_cached_hash = cache.get(song_cache_key, {}).get('hash', '')

        if not should_process(song_info_path, song_hash, song_cached_hash, args.only_new):
            continue

        display = get_song_display_name(mp3)
        log(f'    单曲：{display}')
        try:
            netease_info = None
            if not skip_netease_here and not args.dry_run:
                netease_info = fetch_netease(mp3)
                if netease_info:
                    log(f'      网易云命中：{netease_info["title"]} - {netease_info["artist"]}')

            if args.dry_run:
                log(f'      [DRY RUN] 将调用 API')
                continue

            prompt = build_song_prompt(mp3, netease_info)
            content = call_deepseek(
                '你是一个中文音乐撰稿人，文字克制、有画面感，不浮夸。',
                prompt,
                temperature=0.7
            )
            if write_text_file(song_info_path, content):
                changed_paths.append(song_info_path)
                any_change = True
            cache[song_cache_key] = {'hash': song_hash, 'updated': time.time()}
            time.sleep(0.5)
        except Exception as e:
            log(f'      ✗ 失败：{e}')

    return any_change


# ==================== Git ====================

def git_sync(changed_paths, commit_msg=GIT_COMMIT_MSG, dry_run=False):
    if not changed_paths:
        return False
    if dry_run:
        log(f'[DRY RUN] 跳过 git 同步（{len(changed_paths)} 个文件）')
        return False

    try:
        r = subprocess.run(
            ['git', 'rev-parse', '--is-inside-work-tree'],
            cwd=REPO_DIR, capture_output=True, text=True
        )
        if r.returncode != 0:
            log('不是 git 仓库，跳过同步')
            return False

        for p in changed_paths:
            rel = p.relative_to(REPO_DIR)
            subprocess.run(['git', 'add', str(rel)], cwd=REPO_DIR, check=True)

        r = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=REPO_DIR)
        if r.returncode == 0:
            log('git 无变化')
            return False

        subprocess.run(
            ['git', 'commit', '-m', commit_msg],
            cwd=REPO_DIR, check=True, capture_output=True, text=True
        )
        log(f'✓ git commit：{commit_msg}')

        r = subprocess.run(['git', 'push'], cwd=REPO_DIR, capture_output=True, text=True)
        if r.returncode != 0:
            log(f'✗ git push 失败：{r.stderr.strip()}')
            log('  提示：如果远程有新提交，先 git pull --rebase')
            return False

        log('✓ git push 成功')
        return True
    except subprocess.CalledProcessError as e:
        log(f'✗ git 命令失败：{e}')
        return False


# ==================== 显示元数据 ====================

def show_metadata():
    """打印每个 mp3 的元数据和排序结果"""
    stations = scan_stations()
    if not stations:
        log('没有找到任何电台')
        return

    for station in stations:
        if not station['songs']:
            continue
        print(f'\n===== {station["name"]} =====')
        for idx, mp3 in enumerate(station['songs'], 1):
            meta = read_mp3_meta(mp3) if HAS_MUTAGEN else {}
            track = meta.get('track')
            disc = meta.get('disc')
            title = meta.get('title', '')
            artist = meta.get('artist', '')
            album = meta.get('album', '')

            flag = '  '
            if track is None and not re.match(r'^\s*\d+', mp3.stem):
                flag = '⚠ '

            print(f'{flag}{idx:02d}. {mp3.name}')
            if track is not None or disc is not None:
                print(f'       disc {disc or "-"} / track {track or "-"}')
            if title:
                print(f'       title: {title}')
            if artist:
                print(f'       artist: {artist}')
            if album:
                print(f'       album: {album}')


# ==================== 主流程 ====================

def run_once(args):
    log('扫描 music/ 目录...')
    stations = scan_stations()
    if not stations:
        log('没有找到任何电台')
        return

    log(f'找到 {len(stations)} 个电台：{[s["name"] for s in stations]}')

    cache = load_cache()
    changed_paths = []

    for station in stations:
        try:
            if station['local_only']:
                process_local_station(station, cache, args, changed_paths)
            else:
                process_station(station, cache, args, changed_paths)
        except Exception as e:
            log(f'  电台 {station["name"]} 处理出错：{e}')

    if not args.dry_run:
        save_cache(cache)

    if changed_paths:
        log(f'共 {len(changed_paths)} 个文件有变化，开始 git 同步...')
        git_sync(changed_paths, dry_run=args.dry_run)
    else:
        log('没有文件变化')


def main():
    parser = argparse.ArgumentParser(description='自动生成电台/单曲 info.txt 并同步')
    parser.add_argument('--watch', action='store_true', help='循环运行')
    parser.add_argument('--interval', type=int, default=WATCH_INTERVAL,
                        help=f'watch 模式间隔（秒），默认 {WATCH_INTERVAL}')
    parser.add_argument('--dry-run', action='store_true', help='只打印，不调用 API、不写文件')
    parser.add_argument('--only-new', action='store_true', help='只为没有 info.txt 的条目生成')
    parser.add_argument('--no-songs', action='store_true', help='只生成电台级，跳过单曲级')
    parser.add_argument('--no-netease', action='store_true', help='不查询网易云')
    parser.add_argument('--show-meta', action='store_true', help='只打印元数据和排序，不生成')
    args = parser.parse_args()

    if args.show_meta:
        show_metadata()
        return

    if not API_KEY and not args.dry_run:
        print('错误：请设置环境变量 DEEPSEEK_API_KEY')
        print('  Windows:      set DEEPSEEK_API_KEY=YOUR_KEY_HERE')
        print('  macOS/Linux:  export DEEPSEEK_API_KEY=YOUR_KEY_HERE')
        sys.exit(1)

    if args.watch:
        log(f'watch 模式启动，每 {args.interval} 秒检查一次')
        while True:
            try:
                run_once(args)
            except KeyboardInterrupt:
                log('收到中断，退出')
                break
            except Exception as e:
                log(f'运行出错：{e}')
            log(f'等待 {args.interval} 秒...\n')
            time.sleep(args.interval)
    else:
        run_once(args)


if __name__ == '__main__':
    main()
